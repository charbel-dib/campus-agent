"""Operations: traces, feedback, hand-off, the office's metrics, personal data, streaming, optional features."""

from __future__ import annotations

import json
from datetime import timedelta

from campus_agent import ops, privacy, telemetry, tools
from campus_agent.clock import Clock, parse_dt

from conftest import NOW
from test_campus_agent import STUDENT, ScriptedLLM, make_agent, text, tool_use
from test_campus_web import call, login, make_app, runtime  # noqa: F401 - fixture

THURSDAY = [tool_use("get_schedule", {"start_date": "2026-11-19"}), text("Jeudi, quatre séances.")]


def test_a_turn_is_traced_with_genai_names_and_no_text(conn, doc_index):
    agent = make_agent(conn, ScriptedLLM(THURSDAY))
    turn = agent.run(agent.new_session(STUDENT), STUDENT, "Qu'est-ce que j'ai jeudi ? SECRET-PHRASE")
    spans = telemetry.trace(conn, turn.trace_id)
    assert [s["name"] for s in spans] == ["invoke_agent campus-agent", "chat claude-haiku-5-5",
                                          "execute_tool get_schedule", "chat claude-haiku-5-5"]
    root, chat, tool = spans[0], spans[1], spans[2]
    assert root["parent_id"] is None and chat["parent_id"] == root["span_id"] == tool["parent_id"]
    assert chat["attributes"]["gen_ai.operation.name"] == "chat" and chat["attributes"]["gen_ai.usage.input_tokens"] == 1000
    assert tool["attributes"]["gen_ai.tool.name"] == "get_schedule" and tool["attributes"]["campus.tool.outcome"] == "ok"
    assert root["attributes"]["campus.model_calls"] == 2 and len(root["attributes"]["campus.prompt_version"]) == 12
    stored = " ".join(r[0] for r in conn.execute("SELECT attributes_json FROM spans"))
    assert "SECRET-PHRASE" not in stored and "Jeudi" not in stored          # identifiers and counts only


def test_otlp_payload_shape(conn):
    tracer = telemetry.Tracer(conn, "s", "stu-0001")
    root = tracer.start("invoke_agent campus-agent")
    child = tracer.start("chat m", parent=root, **{"gen_ai.usage.input_tokens": 12, "ok": True, "cost": 0.5})
    tracer.end(child, "error")
    tracer.end(root)
    spans = telemetry.otlp_payload(tracer.spans)["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert spans[1]["parentSpanId"] == spans[0]["spanId"] and len(spans[0]["traceId"]) == 32
    assert spans[1]["status"]["code"] == 2 and spans[1]["kind"] == 3
    assert {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "12"}} in spans[1]["attributes"]


def test_feedback_handoff_and_office_metrics(runtime):  # noqa: F811
    app, _ = make_app(runtime, THURSDAY)
    token = login(app)
    sid = call(app, "POST", "/api/sessions", token=token)[2]["session_id"]
    turn = call(app, "POST", f"/api/sessions/{sid}/messages", {"text": "Qu'est-ce que j'ai jeudi ?"}, token)[2]
    assert call(app, "POST", f"/api/sessions/{sid}/feedback", {"rating": -1, "reason": "trop long",
                                                                "trace_id": turn["trace_id"]}, token)[0] == 200
    assert call(app, "POST", f"/api/sessions/{sid}/feedback", {"rating": 5}, token)[0] == 400
    status, _, data = call(app, "POST", f"/api/sessions/{sid}/handoff", token=token)
    assert status == 200 and data["ticket_id"]
    assert len(call(app, "GET", f"/api/traces/{turn['trace_id']}", token=token)[2]["spans"]) == 4
    assert call(app, "GET", "/api/admin/metrics", token=token)[0] == 403            # students do not see it
    office = login(app, "stf-01")
    assert call(app, "GET", f"/api/traces/{turn['trace_id']}", token=login(app, "stu-0003"))[2]["spans"] == []
    m = call(app, "GET", "/api/admin/metrics", token=office)[2]
    assert m["turns"]["count"] == 1 and m["feedback"]["down"] == 1 and m["feedback"]["last_down"][0]["reason"] == "trop long"
    assert m["unmet"][0]["subject"] == "Relais humain demandé" and m["tools"][0]["tool"] in ("get_schedule", "create_ticket")
    assert call(app, "GET", "/api/status")[2]["prompt_version"] == m["prompt_version"]


def test_streaming_sends_steps_then_the_turn(runtime):  # noqa: F811
    app, _ = make_app(runtime, THURSDAY)
    token = login(app)
    sid = call(app, "POST", "/api/sessions", token=token)[2]["session_id"]
    status, headers, raw = call(app, "POST", f"/api/sessions/{sid}/stream", {"text": "Qu'est-ce que j'ai jeudi ?"}, token)
    assert status == 200 and headers["content-type"].startswith("text/event-stream")
    events = [(b.split("\n")[0][7:], json.loads(b.split("\n")[1][6:])) for b in raw.decode().strip().split("\n\n")]
    assert [e[0] for e in events] == ["step", "turn"]
    assert events[0][1]["name"] == "get_schedule" and events[1][1]["reply"] == "Jeudi, quatre séances."


def test_export_erase_and_retention(conn, doc_index):
    agent = make_agent(conn, ScriptedLLM(THURSDAY))
    sid = agent.new_session(STUDENT)
    agent.run(sid, STUDENT, "Qu'est-ce que j'ai jeudi ?")
    data = privacy.export_person(conn, STUDENT)
    assert data["conversations"][0]["my_messages"][0]["text"] == "Qu'est-ce que j'ai jeudi ?"
    assert data["audit_trail"] and data["traces"] and data["person"][0]["id"] == STUDENT
    json.dumps(data)
    counts = privacy.erase_person(conn, STUDENT)
    assert counts["agent_sessions"] == 1 and counts["spans"] == 4
    after = privacy.export_person(conn, STUDENT)
    assert after["conversations"] == [] and after["traces"] == [] and after["audit_trail"]      # who-did-what stays
    assert conn.execute("SELECT args_json FROM audit_log WHERE actor_id = ?", (STUDENT,)).fetchone()[0] == "{}"
    other = make_agent(conn, ScriptedLLM([text("Bonjour.")]))
    other.run(other.new_session("stu-0002"), "stu-0002", "Bonjour")
    assert privacy.purge(conn, parse_dt(NOW), 30)["agent_sessions"] == 0
    assert privacy.purge(conn, parse_dt(NOW) + timedelta(days=31), 30)["agent_sessions"] == 1


def test_a_tool_can_be_taken_out_of_service(ctx_for, doc_index):
    tools.configure(disabled={"create_ticket"})
    try:
        assert "create_ticket" not in [t.name for t in tools.tools_for("student")]
        res = tools.execute(ctx_for(STUDENT), "create_ticket", {"category": "autre", "subject": "abc", "body": "x" * 12})
        assert res.is_error and res.content["code"] == "denied"
    finally:
        tools.configure()
    assert "ask_user" not in [t.name for t in tools.tools_for("student")]            # optional tools are off by default


def test_structured_question_ends_the_turn_with_choices(conn, doc_index):
    tools.configure({"clarify": True})
    try:
        agent = make_agent(conn, ScriptedLLM([tool_use("ask_user", {"question": "Quel cours annuler ?",
                                                                    "options": ["Le TP de vendredi", "Le cours de lundi"]})]))
        turn = agent.run(agent.new_session("tch-001"), "tch-001", "Annule mon cours")
        assert turn.reply == "Quel cours annuler ?" and turn.as_dict()["choices"] == ["Le TP de vendredi", "Le cours de lundi"]
    finally:
        tools.configure()


def test_skills_are_listed_then_loaded_on_demand(conn, doc_index):
    tools.configure({"skills": True})
    try:
        llm = ScriptedLLM([tool_use("load_skill", {"name": "justifier-une-absence"}), text("Voici la démarche.")])
        agent = make_agent(conn, llm)
        agent.run(agent.new_session(STUDENT), STUDENT, "Comment justifier une absence ?")
        assert "justifier-une-absence : Justifier une absence" in llm.requests[0]["system"][0]["text"]
        assert "cinq jours ouvrés" in json.dumps(llm.requests[1]["messages"], ensure_ascii=False)
        assert tools.execute(agent._ctx(agent_actor(conn), "s"), "load_skill", {"name": "../secret"}).is_error
    finally:
        tools.configure()


def agent_actor(conn):
    from campus_agent.domain import load_actor

    return load_actor(conn, STUDENT)


def test_a_stronger_model_takes_over_after_repeated_tool_errors(conn, doc_index):
    bad = lambda i: tool_use("get_schedule", {"start_date": "pas une date"}, f"c{i}")  # noqa: E731
    small = ScriptedLLM([bad(1), bad(2)])
    big = ScriptedLLM([text("Je reprends : précise la date.")])
    big.model = "claude-sonnet-5-5"
    agent = make_agent(conn, small, escalation_llm=big)
    turn = agent.run(agent.new_session(STUDENT), STUDENT, "Mon agenda")
    assert turn.reply.startswith("Je reprends") and not small.steps and not big.steps
    root = telemetry.trace(conn, turn.trace_id)[0]
    assert root["attributes"]["campus.escalated_to"] == "claude-sonnet-5-5"
    assert ops.metrics(conn)["turns"]["escalated"] == 1


def test_long_cache_ttl_is_applied_when_asked(conn, doc_index):
    llm = ScriptedLLM([text("Bonjour.")])
    agent = make_agent(conn, llm, cache_ttl="1h")
    agent.run(agent.new_session(STUDENT), STUDENT, "Bonjour")
    assert llm.requests[0]["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert Clock(NOW).now() == parse_dt(NOW)
