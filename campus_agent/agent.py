"""The agent: a tool-use loop over the Claude Messages API, with human confirmation of writes.

One user message runs the loop: Claude reads the request, calls read tools as needed, and
proposes writes. A proposed write becomes a *pending action* shown to the user; the loop does
not wait on it. When the user confirms or refuses, ``decide`` executes (or drops) the action,
tells Claude the outcome, and runs the loop again so it can finish the remaining steps.

Guards: a cap on steps per turn, a cost budget per session and per day, history trimming, and
prompt caching of the tools and the static instructions.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from . import tools
from .clock import Clock, SLOTS, fmt_dt, human_dt, monday_of
from .db import get_meta
from .domain import Actor, Context, load_actor
from .llm import LLMError, MessagesClient, Usage
from .telemetry import Tracer

log = logging.getLogger("campus_agent.agent")

MAX_HISTORY_TURNS = 12
TOOL_RESULT_MAX_CHARS = 12_000

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
SKILLS_DIR = Path(__file__).resolve().parent / "skills"

# The instructions live in a versioned file: a prompt change is a reviewed diff that goes through the
# evaluation gate, and its hash is recorded in every trace and every evaluation result.
STATIC_INSTRUCTIONS = (PROMPTS_DIR / "system.md").read_text(encoding="utf-8").strip()


def prompt_version() -> str:
    payload = STATIC_INSTRUCTIONS + json.dumps([t.api_definition() for t in tools.TOOLS], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def skills_index() -> str:
    """One line per procedure file; the body is loaded on demand with the load_skill tool."""
    lines = []
    for path in sorted(SKILLS_DIR.glob("*.md")):
        first = path.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
        lines.append(f"- {path.stem} : {first}")
    return ("# Procédures\nAvant une démarche de cette liste, charge sa procédure avec load_skill et suis-la :\n"
            + "\n".join(lines))

ROLE_FR = {"student": "étudiant(e)", "teacher": "enseignant(e)", "staff": "personnel de scolarité"}


def dynamic_context(conn, actor: Actor, clock: Clock) -> str:
    now = clock.now()
    monday = monday_of(now.date())
    friday = monday + timedelta(days=4)
    who = f"{actor.name}, {ROLE_FR[actor.role]}, identifiant {actor.id}"
    if actor.group_id:
        group = conn.execute("SELECT name FROM groups WHERE id = ?", (actor.group_id,)).fetchone()
        who += f", groupe {actor.group_id} ({group['name']})"
    if actor.department:
        who += f", {actor.title or ''} ({actor.department})".replace(" ()", "")
    slots = ", ".join(f"{s:%H:%M}–{e:%H:%M}" for s, e in SLOTS)
    return (f"Utilisateur connecté : {who}.\n"
            f"Nous sommes le {human_dt(now)} (heure de Paris). Semaine en cours : du lundi {monday:%d/%m} "
            f"au vendredi {friday:%d/%m/%Y}.\n"
            f"Semestre : du {get_meta(conn, 'semester_start')} au {get_meta(conn, 'semester_end')}.\n"
            f"Créneaux de cours : {slots} ; pas de cours le mercredi après-midi.")


@dataclass
class TurnResult:
    session_id: str
    reply: str
    trace: list[dict] = field(default_factory=list)
    pending_actions: list[dict] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stopped: str | None = None            # max_steps, budget, error
    decision: dict | None = None          # result of a confirmed / refused action
    # Full text of every tool result the model saw this turn. Kept out of as_dict (the web client
    # does not need it); the evaluation reads it to detect leaks and unsupported claims.
    tool_outputs: list[str] = field(default_factory=list)
    trace_id: str = ""                    # telemetry trace of this turn
    choices: list[str] = field(default_factory=list)   # clickable answers to a clarifying question

    def as_dict(self) -> dict:
        return {"session_id": self.session_id, "reply": self.reply, "trace": self.trace,
                "pending_actions": self.pending_actions, "usage": self.usage.as_dict(), "stopped": self.stopped,
                "trace_id": self.trace_id, **({"choices": self.choices} if self.choices else {}),
                **({"decision": self.decision} if self.decision else {})}


class Agent:
    def __init__(self, conn, clock: Clock, llm: MessagesClient, *, max_steps: int = 10, max_tokens: int = 4096,
                 session_budget_usd: float = 0.25, daily_budget_usd: float = 2.0, channel: str = "agent",
                 runtime_dir: Path | None = None, pending_ttl_minutes: int = 15, ledger=None,
                 escalation_llm: MessagesClient | None = None, escalate_after_steps: int = 6,
                 cache_ttl: str = "", otlp_endpoint: str = ""):
        self.conn = conn
        self.clock = clock
        self.llm = llm
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.session_budget_usd = session_budget_usd
        self.daily_budget_usd = daily_budget_usd
        self.channel = channel
        self.runtime_dir = runtime_dir
        self.pending_ttl_minutes = pending_ttl_minutes
        # Optional shared spend ledger (public demo: one database per visitor, one budget for all)
        self.ledger = ledger
        # Model routing: a stronger model takes over a turn that runs long or keeps hitting tool errors.
        self.escalation_llm = escalation_llm
        self.escalate_after_steps = escalate_after_steps
        self.cache_ttl = cache_ttl            # "1h" keeps the cached instructions for an hour instead of 5 min
        self.otlp_endpoint = otlp_endpoint
        self.on_event = None                  # callable(dict): live steps for the streaming endpoint

    # ------------------------------------------------------------------ sessions

    def new_session(self, actor_id: str) -> str:
        load_actor(self.conn, actor_id)
        sid = secrets.token_urlsafe(12)
        now = fmt_dt(self.clock.now())
        self.conn.execute("INSERT INTO agent_sessions(id, actor_id, created_at, updated_at, model) VALUES (?,?,?,?,?)",
                          (sid, actor_id, now, now, getattr(self.llm, "model", "")))
        self.conn.commit()
        return sid

    def _session(self, session_id: str, actor_id: str):
        row = self.conn.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None or row["actor_id"] != actor_id:
            raise KeyError("session inconnue")
        return row

    def _save(self, session_id: str, messages: list[dict], usage: Usage) -> None:
        self.conn.execute(
            "UPDATE agent_sessions SET messages_json=?, updated_at=?, input_tokens=input_tokens+?, "
            "output_tokens=output_tokens+?, cache_read_tokens=cache_read_tokens+?, "
            "cache_write_tokens=cache_write_tokens+?, cost_usd=cost_usd+? WHERE id=?",
            (json.dumps(messages, ensure_ascii=False), fmt_dt(self.clock.now()), usage.input_tokens,
             usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens, usage.cost_usd, session_id))
        self.conn.commit()

    def history(self, session_id: str, actor_id: str) -> list[dict]:
        """The conversation as the user sees it: their messages and the assistant's text."""
        msgs = json.loads(self._session(session_id, actor_id)["messages_json"])
        out = []
        for m in msgs:
            if isinstance(m["content"], str):
                role = "decision" if m["content"].startswith("[Décision]") else m["role"]
                out.append({"role": role, "text": m["content"]})
            elif m["role"] == "assistant" and not any(b.get("type") == "tool_use" for b in m["content"]):
                text = "\n".join(b["text"] for b in m["content"] if b.get("type") == "text").strip()
                if text:
                    out.append({"role": "assistant", "text": text})
        return out

    def spent_today(self) -> float:
        if self.ledger is not None:
            return self.ledger.spent(self.clock.now().date())
        today = self.clock.now().date().isoformat()
        return self.conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM agent_sessions WHERE substr(updated_at,1,10)=?",
                                 (today,)).fetchone()[0]

    # ------------------------------------------------------------------ entry points

    def run(self, session_id: str, actor_id: str, user_text: str) -> TurnResult:
        row = self._session(session_id, actor_id)
        messages = json.loads(row["messages_json"])
        text = user_text.strip()[:4000]
        if not text:
            return TurnResult(session_id, "Écris ta demande.", stopped="empty")
        messages.append({"role": "user", "content": text})
        return self._loop(session_id, load_actor(self.conn, actor_id), messages, row["cost_usd"])

    def decide(self, session_id: str, actor_id: str, action_id: int, confirm: bool) -> TurnResult:
        row = self._session(session_id, actor_id)
        actor = load_actor(self.conn, actor_id)
        ctx = self._ctx(actor, session_id)
        pending = self.conn.execute("SELECT session_id FROM pending_actions WHERE id = ?", (int(action_id),)).fetchone()
        if pending is None or pending["session_id"] != session_id:
            return TurnResult(session_id, "Action inconnue dans cette conversation.", stopped="error")
        result = (tools.confirm_pending(ctx, action_id, self.pending_ttl_minutes) if confirm
                  else tools.cancel_pending(ctx, action_id))
        if confirm and not result.is_error:
            note = f"[Décision] L'utilisateur a confirmé l'action n°{action_id}. Elle est exécutée. Résultat : "
        elif confirm:
            note = (f"[Décision] L'utilisateur a confirmé l'action n°{action_id}, mais elle a échoué à l'exécution "
                    "(les règles sont revérifiées au moment de confirmer). Erreur : ")
        else:
            note = f"[Décision] L'utilisateur a refusé l'action n°{action_id}. Elle n'est pas exécutée. Détail : "
        messages = json.loads(row["messages_json"])
        messages.append({"role": "user", "content": note + _truncate(result.as_text())})
        turn = self._loop(session_id, actor, messages, row["cost_usd"])
        turn.decision = {"action_id": int(action_id), "confirmed": confirm, "ok": not result.is_error,
                         "result": result.content}
        turn.tool_outputs.insert(0, result.as_text())
        return turn

    # ------------------------------------------------------------------ loop

    def _ctx(self, actor: Actor, session_id: str) -> Context:
        return Context(self.conn, actor, self.clock, channel=self.channel, session_id=session_id,
                       runtime_dir=self.runtime_dir)

    def _system(self, actor: Actor) -> list[dict]:
        static = STATIC_INSTRUCTIONS + ("\n\n" + skills_index() if tools.flag("skills") else "")
        cache = {"type": "ephemeral", **({"ttl": self.cache_ttl} if self.cache_ttl else {})}
        return [{"type": "text", "text": static, "cache_control": cache},
                {"type": "text", "text": dynamic_context(self.conn, actor, self.clock)}]

    def _emit(self, event: dict) -> None:
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:  # noqa: BLE001 - a broken listener must not break the turn
                log.exception("event listener failed")

    def _loop(self, session_id: str, actor: Actor, messages: list[dict], spent_before: float) -> TurnResult:
        ctx = self._ctx(actor, session_id)
        tool_defs = [t.api_definition() for t in tools.tools_for(actor.role)]
        system = self._system(actor)
        turn = TurnResult(session_id, "")
        replies: list[str] = []
        llm = self.llm
        errors = 0
        tracer = Tracer(self.conn, session_id, actor.id, self.otlp_endpoint)
        turn.trace_id = tracer.trace_id
        root = tracer.start("invoke_agent campus-agent", **{
            "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "campus-agent",
            "gen_ai.provider.name": "anthropic", "gen_ai.conversation.id": session_id,
            "campus.actor.role": actor.role, "campus.channel": self.channel, "campus.prompt_version": prompt_version()})

        for _step in range(self.max_steps):
            if self.escalation_llm is not None and llm is self.llm and (_step >= self.escalate_after_steps or errors >= 2):
                llm = self.escalation_llm
                root.set("campus.escalated_to", getattr(llm, "model", ""))
                self._emit({"type": "escalation", "model": getattr(llm, "model", "")})
            if spent_before + turn.usage.cost_usd >= self.session_budget_usd:
                turn.stopped = "budget"
                replies.append("Cette conversation a atteint son budget. Ouvre une nouvelle conversation pour continuer.")
                break
            if self.spent_today() + turn.usage.cost_usd >= self.daily_budget_usd:
                turn.stopped = "budget"
                replies.append("Le budget quotidien de la démonstration est atteint. Réessaie demain.")
                break
            try:
                model = getattr(llm, "model", "")
                chat = tracer.start(f"chat {model}", parent=root, **{
                    "gen_ai.operation.name": "chat", "gen_ai.provider.name": "anthropic", "gen_ai.request.model": model})
                started = self.clock_seconds()
                resp = llm.create(system=system, messages=prepare_messages(messages, self.cache_ttl),
                                  tools=tool_defs, max_tokens=self.max_tokens)
                before = turn.usage.cost_usd
                turn.usage.add(model, resp.get("usage", {}), self.clock_seconds() - started)
                raw = resp.get("usage", {})
                chat.set("gen_ai.usage.input_tokens", raw.get("input_tokens", 0) or 0)
                chat.set("gen_ai.usage.output_tokens", raw.get("output_tokens", 0) or 0)
                chat.set("gen_ai.usage.cache_read.input_tokens", raw.get("cache_read_input_tokens", 0) or 0)
                chat.set("gen_ai.response.finish_reasons", [resp.get("stop_reason") or ""])
                chat.set("campus.cost_usd", round(turn.usage.cost_usd - before, 6))
                tracer.end(chat)
            except LLMError as exc:
                chat.set("error.type", type(exc).__name__)
                tracer.end(chat, "error")
                log.error("LLM call failed: %s", exc)
                turn.stopped = "error"
                replies.append("Le service d'IA ne répond pas pour le moment. Réessaie dans un instant.")
                break

            content = resp.get("content", [])
            messages.append({"role": "assistant", "content": content})
            texts = [b["text"] for b in content if b.get("type") == "text" and b["text"].strip()]
            calls = [b for b in content if b.get("type") == "tool_use"]
            if texts:
                turn.trace.append({"type": "text", "text": "\n".join(texts)})
            if resp.get("stop_reason") != "tool_use" or not calls:
                replies.extend(texts)
                if resp.get("stop_reason") == "max_tokens":
                    turn.stopped = "max_tokens"
                break
            results = []
            asked = None
            for call in calls:
                span = tracer.start(f"execute_tool {call['name']}", parent=root, **{
                    "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": call["name"],
                    "gen_ai.tool.call.id": call.get("id", "")})
                res = tools.execute(ctx, call["name"], call.get("input", {}), confirm_writes=True)
                span.set("campus.tool.outcome", res.content.get("code", "error") if res.is_error
                         else ("pending" if res.pending_action else "ok"))
                if res.is_error:
                    errors += 1
                    span.set("error.type", res.content.get("code", "error"))
                tracer.end(span, "error" if res.is_error else "ok")
                if call["name"] == "ask_user" and not res.is_error:
                    asked = res.content
                step = {"type": "tool", "name": call["name"], "input": call.get("input", {}),
                        "ok": not res.is_error, "preview": _preview(res)}
                if not res.is_error and "download_url" in res.content:
                    step["download_url"] = res.content["download_url"]
                turn.trace.append(step)
                self._emit(step)
                if res.pending_action:
                    turn.pending_actions.append(res.pending_action)
                turn.tool_outputs.append(res.as_text())
                results.append({"type": "tool_result", "tool_use_id": call["id"],
                                "content": _truncate(res.as_text()), **({"is_error": True} if res.is_error else {})})
            messages.append({"role": "user", "content": results})
            if asked:                       # a structured question ends the turn: the user answers with a click
                replies.append(asked["question"])
                turn.choices = asked["options"]
                break
        else:
            turn.stopped = "max_steps"
            replies.append("J'ai atteint le nombre maximal d'étapes pour cette demande ; reformule-la plus simplement.")
            messages.append({"role": "assistant", "content": [{"type": "text", "text": replies[-1]}]})

        if messages[-1]["role"] == "user":       # keep strict user/assistant alternation for the next turn
            messages.append({"role": "assistant", "content": [{"type": "text", "text": replies[-1] if replies else "…"}]})
        turn.reply = "\n\n".join(r for r in replies if r).strip()
        if not turn.reply and turn.pending_actions:
            turn.reply = "Confirme l'action proposée pour que je la réalise."
        root.set("gen_ai.usage.input_tokens", turn.usage.input_tokens)
        root.set("gen_ai.usage.output_tokens", turn.usage.output_tokens)
        root.set("campus.cost_usd", round(turn.usage.cost_usd, 6))
        root.set("campus.model_calls", turn.usage.calls)
        root.set("campus.pending_actions", len(turn.pending_actions))
        if turn.stopped:
            root.set("error.type", turn.stopped)
        tracer.end(root, "error" if turn.stopped in ("error", "max_steps") else "ok")
        tracer.flush()
        self._save(session_id, messages, turn.usage)
        if self.ledger is not None and turn.usage.cost_usd:
            self.ledger.record(self.clock.now().date(), turn.usage.cost_usd)
        return turn

    @staticmethod
    def clock_seconds() -> float:
        import time

        return time.monotonic()


def trim_history(messages: list[dict], max_turns: int = MAX_HISTORY_TURNS) -> list[dict]:
    """Keep the last ``max_turns`` user turns, cutting only where a turn starts (a plain user text)."""
    starts = [i for i, m in enumerate(messages) if m["role"] == "user" and isinstance(m["content"], str)]
    if len(starts) <= max_turns:
        return messages
    return messages[starts[-max_turns]:]


def drop_old_thinking(messages: list[dict]) -> list[dict]:
    """Remove thinking blocks from finished turns; keep those of the turn in progress.

    Thinking blocks are signed against the conversation that produced them, including the system
    prompt, which carries the current time and so changes between turns. Claude only needs the
    thinking of the current tool-use loop; older blocks are dropped (they would also cost tokens).
    """
    starts = [i for i, m in enumerate(messages) if m["role"] == "user" and isinstance(m["content"], str)]
    current = starts[-1] if starts else 0
    out = []
    for i, m in enumerate(messages):
        if i < current and m["role"] == "assistant" and isinstance(m["content"], list):
            kept = [b for b in m["content"] if b.get("type") not in ("thinking", "redacted_thinking")]
            m = {**m, "content": kept or [{"type": "text", "text": "…"}]}
        out.append(m)
    return out


def prepare_messages(messages: list[dict], cache_ttl: str = "") -> list[dict]:
    return _with_cache_breakpoint(drop_old_thinking(trim_history(messages)), cache_ttl)


def _with_cache_breakpoint(messages: list[dict], cache_ttl: str = "") -> list[dict]:
    """Mark the end of the conversation so the next call reads the whole prefix from the cache."""
    if not messages:
        return messages
    out = list(messages)
    last = copy.deepcopy(out[-1])
    if isinstance(last["content"], str):
        last["content"] = [{"type": "text", "text": last["content"]}]
    if last["content"]:
        last["content"][-1]["cache_control"] = {"type": "ephemeral", **({"ttl": cache_ttl} if cache_ttl else {})}
    out[-1] = last
    return out


def _truncate(text: str, limit: int = TOOL_RESULT_MAX_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "… [résultat tronqué]"


def _preview(res: tools.ToolResult) -> str:
    c = res.content
    if res.is_error:
        return c.get("error", "erreur")
    if res.pending_action:
        return "À confirmer : " + res.pending_action["summary"]
    for key in ("summary", "message"):
        if key in c:
            return str(c[key])
    for key in ("events", "slots", "results", "rooms", "absences", "courses", "received", "tickets"):
        if key in c and isinstance(c[key], list):
            return f"{len(c[key])} élément(s)"
    if "busy" in c:
        return f"{len(c['busy'])} plage(s) occupée(s)"
    return "ok"
