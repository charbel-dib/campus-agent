"""The agent loop with a scripted model: tool calls, confirmation, guards, caching, history."""

from __future__ import annotations

import copy
import json

import pytest

from campus_agent.agent import Agent, trim_history
from campus_agent.clock import Clock
from campus_agent.llm import LLMError

from conftest import NOW

STUDENT = "stu-0001"


class ScriptedLLM:
    """Plays back a list of steps. Each step is a dict response or a function(request) -> response."""

    model = "claude-haiku-5-5"

    def __init__(self, steps):
        self.steps = list(steps)
        self.requests = []

    def create(self, *, system, messages, tools, max_tokens):
        self.requests.append({"system": copy.deepcopy(system), "messages": copy.deepcopy(messages),
                              "tools": [t["name"] for t in tools]})
        if not self.steps:
            raise AssertionError("the agent called the model more often than scripted")
        step = self.steps.pop(0)
        return step(self.requests[-1]) if callable(step) else step


def tool_use(name, args, call_id="call_1"):
    return {"stop_reason": "tool_use", "usage": {"input_tokens": 1000, "output_tokens": 50},
            "content": [{"type": "text", "text": "Je regarde."},
                        {"type": "tool_use", "id": call_id, "name": name, "input": args}]}


def text(msg):
    return {"stop_reason": "end_turn", "usage": {"input_tokens": 1200, "output_tokens": 80,
                                                 "cache_read_input_tokens": 900},
            "content": [{"type": "text", "text": msg}]}


def make_agent(conn, llm, **kw):
    return Agent(conn, Clock(NOW), llm, **kw)


def assert_valid_conversation(messages):
    """Roles alternate, and every tool_use is answered by a tool_result in the next message."""
    assert messages[0]["role"] == "user"
    for a, b in zip(messages, messages[1:]):
        assert a["role"] != b["role"], (a, b)
    for i, m in enumerate(messages):
        if m["role"] == "assistant" and isinstance(m["content"], list):
            ids = {b["id"] for b in m["content"] if b.get("type") == "tool_use"}
            if ids:
                answered = {b["tool_use_id"] for b in messages[i + 1]["content"]}
                assert ids == answered


def stored_messages(conn, sid):
    return json.loads(conn.execute("SELECT messages_json FROM agent_sessions WHERE id=?", (sid,)).fetchone()[0])


def test_read_only_turn(conn):
    llm = ScriptedLLM([tool_use("get_absences", {}), text("Tu as une absence non justifiée.")])
    agent = make_agent(conn, llm)
    sid = agent.new_session(STUDENT)
    turn = agent.run(sid, STUDENT, "Ai-je des absences ?")
    assert turn.reply == "Tu as une absence non justifiée."
    assert [s["name"] for s in turn.trace if s["type"] == "tool"] == ["get_absences"]
    assert turn.trace[1]["ok"] and turn.pending_actions == []
    assert turn.usage.calls == 2 and turn.usage.cost_usd > 0
    # the tool result reached the model
    second = llm.requests[1]["messages"]
    assert second[-1]["content"][0]["type"] == "tool_result" and "non_justifiee" in second[-1]["content"][0]["content"]
    assert_valid_conversation(stored_messages(conn, sid))
    assert agent.history(sid, STUDENT) == [{"role": "user", "text": "Ai-je des absences ?"},
                                           {"role": "assistant", "text": "Tu as une absence non justifiée."}]


def test_student_only_gets_student_tools(conn):
    llm = ScriptedLLM([text("Bonjour")])
    agent = make_agent(conn, llm)
    agent.run(agent.new_session(STUDENT), STUDENT, "Bonjour")
    assert "justify_absence" in llm.requests[0]["tools"] and "review_absence" not in llm.requests[0]["tools"]


def _absence(conn):
    return conn.execute("SELECT id FROM absences WHERE student_id=? AND status='non_justifiee'", (STUDENT,)).fetchone()[0]


def test_write_is_proposed_then_confirmed(conn):
    ab = _absence(conn)
    llm = ScriptedLLM([
        tool_use("justify_absence", {"absence_id": ab, "reason": "Certificat médical"}),
        text("Je dépose ta justification ; confirme pour l'envoyer."),
        lambda req: text("C'est fait : ta justification est en attente.")
        if "[Décision] L'utilisateur a confirmé" in req["messages"][-1]["content"][-1]["text"] else text("??"),
    ])
    agent = make_agent(conn, llm)
    sid = agent.new_session(STUDENT)
    turn = agent.run(sid, STUDENT, "Justifie mon absence, j'ai un certificat médical")
    assert len(turn.pending_actions) == 1 and "Justifier" in turn.pending_actions[0]["summary"]
    assert conn.execute("SELECT status FROM absences WHERE id=?", (ab,)).fetchone()[0] == "non_justifiee"

    after = agent.decide(sid, STUDENT, turn.pending_actions[0]["id"], confirm=True)
    assert after.decision["ok"] and after.reply.startswith("C'est fait")
    assert conn.execute("SELECT status FROM absences WHERE id=?", (ab,)).fetchone()[0] == "en_attente"
    assert_valid_conversation(stored_messages(conn, sid))


def test_refused_write_changes_nothing(conn):
    ab = _absence(conn)
    llm = ScriptedLLM([tool_use("justify_absence", {"absence_id": ab, "reason": "Certificat médical"}),
                       text("Confirme ?"), text("D'accord, je n'envoie rien.")])
    agent = make_agent(conn, llm)
    sid = agent.new_session(STUDENT)
    turn = agent.run(sid, STUDENT, "Justifie mon absence")
    after = agent.decide(sid, STUDENT, turn.pending_actions[0]["id"], confirm=False)
    assert not after.decision["confirmed"]
    assert "refusé" in llm.requests[-1]["messages"][-1]["content"][-1]["text"]
    assert conn.execute("SELECT status FROM absences WHERE id=?", (ab,)).fetchone()[0] == "non_justifiee"


def test_actions_of_another_session_are_rejected(conn):
    ab = _absence(conn)
    llm = ScriptedLLM([tool_use("justify_absence", {"absence_id": ab, "reason": "Certificat médical"}), text("Confirme ?")])
    agent = make_agent(conn, llm)
    sid = agent.new_session(STUDENT)
    action = agent.run(sid, STUDENT, "Justifie").pending_actions[0]["id"]
    other = agent.new_session(STUDENT)
    assert agent.decide(other, STUDENT, action, confirm=True).stopped == "error"
    with pytest.raises(KeyError):
        agent.run(sid, "stu-0002", "Je prends la session d'Inès")


def test_step_cap(conn):
    llm = ScriptedLLM([tool_use("get_messages", {}, f"c{i}") for i in range(3)])
    agent = make_agent(conn, llm, max_steps=3)
    sid = agent.new_session(STUDENT)
    turn = agent.run(sid, STUDENT, "Boucle")
    assert turn.stopped == "max_steps"
    assert_valid_conversation(stored_messages(conn, sid))


def test_session_budget(conn):
    expensive = {"stop_reason": "tool_use", "usage": {"input_tokens": 2_000_000, "output_tokens": 0},
                 "content": [{"type": "tool_use", "id": "c1", "name": "get_messages", "input": {}}]}
    llm = ScriptedLLM([expensive])
    agent = make_agent(conn, llm, session_budget_usd=0.05)
    sid = agent.new_session(STUDENT)
    turn = agent.run(sid, STUDENT, "Coûteux")
    assert turn.stopped == "budget" and len(llm.requests) == 1
    assert_valid_conversation(stored_messages(conn, sid))
    # the next turn is refused before calling the model
    assert agent.run(sid, STUDENT, "Encore").stopped == "budget" and len(llm.requests) == 1


def test_model_errors_are_reported_politely(conn):
    def boom(_req):
        raise LLMError("HTTP 529 overloaded")

    agent = make_agent(conn, ScriptedLLM([boom]))
    sid = agent.new_session(STUDENT)
    turn = agent.run(sid, STUDENT, "Bonjour")
    assert turn.stopped == "error" and "ne répond pas" in turn.reply
    assert_valid_conversation(stored_messages(conn, sid))


def test_prompt_caching_and_context(conn):
    llm = ScriptedLLM([tool_use("get_messages", {}), text("Rien.")])
    agent = make_agent(conn, llm)
    sid = agent.new_session(STUDENT)
    agent.run(sid, STUDENT, "Mes messages ?")
    system = llm.requests[0]["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "Inès Haddad" in system[1]["text"] and "lundi 16 novembre 2026" in system[1]["text"]
    assert llm.requests[1]["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in json.dumps(stored_messages(conn, sid))


def test_tool_errors_are_returned_to_the_model(conn):
    llm = ScriptedLLM([tool_use("get_grades", {"student_id": "stu-0002"}), text("Je ne peux pas.")])
    agent = make_agent(conn, llm)
    turn = agent.run(agent.new_session(STUDENT), STUDENT, "Notes de Lucas ?")
    result = llm.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] and "propres données" in result["content"]
    assert not turn.trace[1]["ok"]


def test_trim_history_cuts_on_turn_boundaries():
    msgs = []
    for i in range(20):
        msgs += [{"role": "user", "content": f"q{i}"},
                 {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "x", "input": {}}]},
                 {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "r"}]},
                 {"role": "assistant", "content": [{"type": "text", "text": "a"}]}]
    out = trim_history(msgs, max_turns=5)
    assert out[0] == {"role": "user", "content": "q15"} and len(out) == 20
    assert_valid_conversation(out)


def test_thinking_of_finished_turns_is_not_sent_again(conn):
    thought = {"type": "thinking", "thinking": "…", "signature": "sig"}

    def with_thinking(resp):
        return {**resp, "content": [thought, *resp["content"]]}

    llm = ScriptedLLM([with_thinking(tool_use("get_messages", {})), with_thinking(text("Rien.")),
                       with_thinking(tool_use("get_absences", {}, "c2")), text("Une absence.")])
    agent = make_agent(conn, llm)
    sid = agent.new_session(STUDENT)
    agent.run(sid, STUDENT, "Mes messages ?")
    # inside the first turn, the thinking that preceded the tool call is sent back with it
    assert llm.requests[1]["messages"][1]["content"][0]["type"] == "thinking"
    agent.run(sid, STUDENT, "Mes absences ?")
    second_turn = llm.requests[2]["messages"]
    old = [b for m in second_turn[:-1] if m["role"] == "assistant" for b in m["content"]]
    assert all(b.get("type") != "thinking" for b in old)
    current = llm.requests[3]["messages"]
    assert current[-2]["content"][0]["type"] == "thinking"     # the current loop keeps its own
    assert_valid_conversation(current)
