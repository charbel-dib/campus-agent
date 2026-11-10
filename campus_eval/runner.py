"""Run one task on a fresh copy of the school and record everything the checks need."""

from __future__ import annotations

import shutil
import time
import traceback
from pathlib import Path
from typing import Callable

from campus_agent import db, tools
from campus_agent.agent import Agent, dynamic_context
from campus_agent.clock import Clock, parse_date
from campus_agent.domain import Context, load_actor
from campus_agent.llm import Usage

from . import checks as checks_mod
from . import grounding
from . import state as state_mod
from .simulator import SimulatedUser, observation

MAX_DECISIONS = 8          # confirmation slips handled per run
MAX_USER_TURNS = 8         # simulated conversations
MAX_SIM_CALLS = 14


def _setup(conn, clock, ops: list[dict], workdir: Path) -> None:
    for op in ops:
        if "sql" in op:
            conn.execute(op["sql"])
            conn.commit()
            continue
        ctx = Context(conn, load_actor(conn, op["as"]), clock, channel="eval-setup", runtime_dir=workdir)
        res = tools.execute(ctx, op["tool"], op["args"], confirm_writes=False)
        if res.is_error:
            raise RuntimeError(f"setup failed: {op['tool']} {res.content}")


class _Recorder:
    def __init__(self):
        self.replies: list[str] = []
        self.user_messages: list[str] = []
        self.tool_calls: list[dict] = []
        self.tool_outputs: list[str] = []
        self.proposed: list[dict] = []
        self.decisions: list[dict] = []
        self.stopped: list[str] = []
        self.usage = Usage()
        self.transcript: list[dict] = []

    def absorb(self, turn, entry: dict):
        self.replies.append(turn.reply)
        self.tool_calls += [s for s in turn.trace if s["type"] == "tool"]
        self.tool_outputs += turn.tool_outputs
        self.proposed += turn.pending_actions
        self.usage.merge(turn.usage)
        if turn.stopped:
            self.stopped.append(turn.stopped)
        self.transcript.append({**entry, "assistant": turn.reply,
                                "tools": [{"name": s["name"], "input": s["input"], "ok": s["ok"], "preview": s["preview"]}
                                          for s in turn.trace if s["type"] == "tool"],
                                "pending": [p["summary"] for p in turn.pending_actions]})
        return turn


def run_task(task: dict, template: Path, now: str, agent_llm: Callable, sim_llm: Callable | None,
             workdir: Path, mode: str = "clean", known_refs: set[str] | None = None) -> dict:
    """Play one task once. ``mode`` is 'attack' to add the task's attack setup (injection suite)."""
    path = workdir / f"{task['id']}-{mode}-{time.monotonic_ns()}.db"
    shutil.copy(template, path)
    conn = db.connect(path)
    clock = Clock(now)
    rec = _Recorder()
    sim = None
    base = {"id": task["id"], "suite": task["suite"], "split": task.get("split", "dev"),
            "category": task.get("category", task["suite"]), "persona": task["persona"], "mode": mode}
    started = time.monotonic()
    try:
        _setup(conn, clock, task.get("setup", []), workdir)
        # Injection tasks: the same situation with benign text (clean) or with attacker text (attack)
        _setup(conn, clock, task.get("attack_setup" if mode == "attack" else "clean_setup", []), workdir)
        before = state_mod.snapshot(conn)
        agent = Agent(conn, clock, agent_llm(), channel="eval", runtime_dir=workdir, session_budget_usd=2.0,
                      daily_budget_usd=10_000.0)
        persona = task["persona"]
        sid = agent.new_session(persona)

        if "user" in task:                                   # simulated user (tau-bench style)
            spec = task["user"]
            sim = SimulatedUser(sim_llm(), who=spec["who"], goal=spec["goal"], known=spec.get("known", "rien de plus"))
            message = task.get("opening") or sim.opening()
            decided = 0
            for _turn in range(MAX_USER_TURNS):
                rec.user_messages.append(message)
                turn = rec.absorb(agent.run(sid, persona, message), {"user": message})
                obs = observation(turn.reply, turn.pending_actions)
                pending = {p["id"]: p for p in turn.pending_actions}
                finished = False
                for _ in range(MAX_SIM_CALLS):
                    reaction = sim._ask(obs)
                    applied = False
                    for d in reaction.decisions:
                        aid = int(d["action_id"])
                        if aid not in pending or decided >= MAX_DECISIONS:
                            continue
                        p = pending.pop(aid)
                        confirm = bool(d.get("confirm"))
                        turn = rec.absorb(agent.decide(sid, persona, aid, confirm),
                                          {"decision": p["summary"], "confirmed": confirm})
                        rec.decisions.append({"action": p["summary"], "confirmed": confirm,
                                              "ok": (turn.decision or {}).get("ok")})
                        decided += 1
                        applied = True
                        pending.update({x["id"]: x for x in turn.pending_actions})
                        obs = observation(turn.reply, list(pending.values()))
                    if applied and not reaction.done:
                        continue                              # let the user react to what the decision did
                    finished = reaction.done or not reaction.message
                    message = reaction.message
                    break
                else:
                    finished = True                           # the simulated user kept deciding: stop here
                if finished:
                    break
        else:                                                # scripted turns
            for text in task["turns"]:
                rec.user_messages.append(text)
                turn = rec.absorb(agent.run(sid, persona, text), {"user": text})
                queue = list(turn.pending_actions)
                while queue and len(rec.decisions) < MAX_DECISIONS:
                    action = queue.pop(0)
                    confirm = task.get("confirm", "none") == "all"
                    turn = rec.absorb(agent.decide(sid, persona, action["id"], confirm),
                                      {"decision": action["summary"], "confirmed": confirm})
                    rec.decisions.append({"action": action["summary"], "confirmed": confirm,
                                          "ok": (turn.decision or {}).get("ok")})
                    queue += turn.pending_actions

        after = state_mod.snapshot(conn)
        actor = load_actor(conn, persona)
        semester_start = parse_date(conn.execute("SELECT value FROM meta WHERE key='semester_start'").fetchone()[0])
        evidence = "\n".join(rec.tool_outputs + rec.user_messages + [dynamic_context(conn, actor, clock)])
        retrieved = grounding.retrieved_refs(rec.tool_outputs)
        unsupported = []
        for r in rec.replies:
            unsupported += grounding.unsupported(r, evidence, semester_start, known_refs or set(), retrieved)
        run = {"replies": rec.replies, "tool_calls": rec.tool_calls, "tool_outputs": rec.tool_outputs,
               "proposed": rec.proposed, "changes": state_mod.diff(before, after),
               "unsupported": sorted(set(unsupported))}

        def evaluate(group):
            out = []
            for check in group:
                ok, detail = checks_mod.evaluate(check, run, conn)
                out.append({"check": check["type"], "ok": ok, "detail": detail})
            return out

        results = evaluate(task.get("checks", []))
        security = evaluate(task.get("security", []))
        passed = all(r["ok"] for r in results) and all(r["ok"] for r in security)
        claims = sum(len(_count_claims(r)) for r in rec.replies)
        return {**base, "passed": passed, "checks": results, "security": security,
                "utility_ok": all(r["ok"] for r in results), "attack_resisted": all(r["ok"] for r in security),
                "tools": [c["name"] for c in rec.tool_calls],
                "tool_errors": [c["preview"] for c in rec.tool_calls if not c["ok"]],
                "proposed": [p["summary"] for p in rec.proposed], "decisions": rec.decisions,
                "changes": run["changes"], "unsupported": run["unsupported"], "claims": claims,
                "user_turns": len(rec.user_messages), "stopped": rec.stopped,
                "outcome": getattr(reaction, "outcome", None) if sim else None,
                "usage": rec.usage.as_dict(), "sim_usage": sim.usage.as_dict() if sim else Usage().as_dict(),
                "seconds": round(time.monotonic() - started, 2), "transcript": rec.transcript}
    except Exception as exc:  # noqa: BLE001 - a crashed run is a failed run, reported as such
        return {**base, "passed": False, "checks": [], "security": [], "utility_ok": False, "attack_resisted": True,
                "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1500:],
                "tools": [c["name"] for c in rec.tool_calls], "tool_errors": [], "proposed": [], "decisions": [],
                "changes": {}, "unsupported": [], "claims": 0, "user_turns": len(rec.user_messages),
                "stopped": rec.stopped, "outcome": None, "usage": rec.usage.as_dict(),
                "sim_usage": sim.usage.as_dict() if sim else Usage().as_dict(),
                "seconds": round(time.monotonic() - started, 2), "transcript": rec.transcript}
    finally:
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)


def _count_claims(reply: str) -> list:
    c = grounding.extract(reply)
    return [*c.times, *c.dates, *c.rooms, *c.courses, *c.grades, *c.citations]
