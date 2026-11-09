"""HTTP API and demo page, called in-process through a minimal ASGI client (no server, no network)."""

from __future__ import annotations

import dataclasses
import json

import anyio
import pytest

from test_campus_agent import ScriptedLLM, text, tool_use

from conftest import NOW


def call(app, method, path, body=None, token=None, raw: bytes | None = None):
    """Send one HTTP request to an ASGI app and return (status, headers, decoded body)."""

    async def run():
        payload = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
        route, _, query = path.partition("?")
        headers = [(b"host", b"test"), (b"content-type", b"application/json")]
        if token:
            headers.append((b"authorization", f"Bearer {token}".encode()))
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
                 "scheme": "http", "path": route, "raw_path": route.encode(), "query_string": query.encode(),
                 "root_path": "", "headers": headers, "client": ("127.0.0.1", 5000), "server": ("test", 80)}
        delivered = False

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": payload, "more_body": False}
            await anyio.sleep(3600)

        messages = []

        async def send(message):
            messages.append(message)

        await app(scope, receive, send)
        start = next(m for m in messages if m["type"] == "http.response.start")
        data = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        hdrs = {k.decode(): v.decode() for k, v in start["headers"]}
        if hdrs.get("content-type", "").startswith("application/json"):
            data = json.loads(data)
        return start["status"], hdrs, data

    return anyio.run(run)


@pytest.fixture()
def runtime(tmp_path, monkeypatch, doc_index):
    from campus_agent.runtime import boot
    from campus_agent.config import load_settings

    monkeypatch.setenv("CAMPUS_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUS_NOW", NOW)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("CAMPUS_SECRET_KEY", "test-secret")
    settings = dataclasses.replace(load_settings(env_file=tmp_path / "none.env"), rate_limit_per_minute=5)
    return boot(settings, reset=True)


def make_app(runtime, steps=()):
    from campus_agent.web import create_app

    llm = ScriptedLLM(steps)
    return create_app(runtime, llm_factory=lambda: llm), llm


def login(app, person="stu-0001"):
    status, _, data = call(app, "POST", "/api/login", {"person_id": person})
    assert status == 200
    return data["token"]


def test_page_and_security_headers(runtime):
    app, _ = make_app(runtime)
    status, headers, body = call(app, "GET", "/")
    assert status == 200 and b"Ker Avel" in body
    assert "default-src 'self'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert call(app, "GET", "/static/app.js")[0] == 200


def test_health_and_personas(runtime):
    app, _ = make_app(runtime)
    status, _, health = call(app, "GET", "/api/health")
    assert status == 200 and health["agent_configured"] and health["now"].startswith("2026-11-16")
    personas = call(app, "GET", "/api/personas")[2]["personas"]
    assert [p["id"] for p in personas][:2] == ["stu-0001", "stu-0003"]


def test_authentication_is_required_and_checked(runtime):
    app, _ = make_app(runtime)
    assert call(app, "GET", "/api/me")[0] == 401
    assert call(app, "GET", "/api/me", token="forged")[0] == 401
    token = login(app)
    forged = token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB")
    assert call(app, "GET", "/api/me", token=forged)[0] == 401
    assert call(app, "GET", "/api/me", token=token)[2]["actor"]["name"] == "Inès Haddad"
    assert call(app, "POST", "/api/login", {"person_id": "stu-0100"})[0] == 400   # not a demo persona


def test_conversation_with_confirmation(runtime):
    conn = runtime.connect()
    ab = conn.execute("SELECT id FROM absences WHERE student_id='stu-0001' AND status='non_justifiee'").fetchone()[0]
    app, llm = make_app(runtime, [
        tool_use("justify_absence", {"absence_id": ab, "reason": "Certificat médical"}),
        text("Je prépare la justification ; confirme-la."),
        text("C'est fait."),
    ])
    token = login(app)
    sid = call(app, "POST", "/api/sessions", token=token)[2]["session_id"]
    status, _, turn = call(app, "POST", f"/api/sessions/{sid}/messages", {"text": "Justifie mon absence"}, token)
    assert status == 200 and len(turn["pending_actions"]) == 1
    action = turn["pending_actions"][0]["id"]

    other = login(app, "stu-0003")
    assert call(app, "POST", f"/api/sessions/{sid}/actions/{action}", {"confirm": True}, other)[0] == 404

    history = call(app, "GET", f"/api/sessions/{sid}/history", token=token)[2]
    assert [p["id"] for p in history["pending_actions"]] == [action]

    status, _, after = call(app, "POST", f"/api/sessions/{sid}/actions/{action}", {"confirm": True}, token)
    assert status == 200 and after["decision"]["ok"] and after["reply"] == "C'est fait."
    assert conn.execute("SELECT status FROM absences WHERE id=?", (ab,)).fetchone()[0] == "en_attente"
    assert call(app, "GET", f"/api/sessions/{sid}/history", token=token)[2]["pending_actions"] == []


def test_calendar_messages_and_audit(runtime):
    app, _ = make_app(runtime)
    student, staff = login(app), login(app, "stf-01")
    week = call(app, "GET", "/api/calendar", token=student)[2]
    assert week["from"] == "2026-11-16" and week["view"] == "full" and len(week["events"]) >= 9
    assert call(app, "GET", "/api/calendar?start=2026-11-23", token=student)[2]["from"] == "2026-11-23"
    assert "received" in call(app, "GET", "/api/messages", token=student)[2]
    own = call(app, "GET", "/api/audit", token=student)[2]["entries"]
    assert own and {e["actor_id"] for e in own} == {"stu-0001"}
    call(app, "GET", "/api/calendar", token=staff)
    everything = call(app, "GET", "/api/audit", token=staff)[2]["entries"]
    assert {"stu-0001", "stf-01"} <= {e["actor_id"] for e in everything}


def test_certificate_download_is_restricted(runtime):
    from campus_agent.domain import academics

    conn = runtime.connect()
    ref = academics.generate_certificate(runtime.context(conn, "stu-0001"))["reference"]
    app, _ = make_app(runtime)
    status, headers, body = call(app, "GET", f"/api/certificates/{ref}", token=login(app))
    assert status == 200 and headers["content-type"] == "application/pdf" and body[:4] == b"%PDF"
    assert call(app, "GET", f"/api/certificates/{ref}", token=login(app, "stu-0003"))[0] == 403
    assert call(app, "GET", f"/api/certificates/{ref}")[0] == 401


def test_input_limits_and_rate_limit(runtime):
    app, _ = make_app(runtime, [text("ok")] * 10)
    token = login(app)
    sid = call(app, "POST", "/api/sessions", token=token)[2]["session_id"]
    assert call(app, "POST", f"/api/sessions/{sid}/messages", raw=b"x" * 20_000, token=token)[0] == 413
    assert call(app, "POST", f"/api/sessions/{sid}/messages", raw=b"{not json", token=token)[0] == 400
    assert call(app, "POST", f"/api/sessions/{sid}/messages", {"text": "  "}, token)[0] == 400
    assert call(app, "POST", "/api/sessions/nope/messages", {"text": "Bonjour"}, token)[0] == 404
    codes = [call(app, "POST", f"/api/sessions/{sid}/messages", {"text": "Bonjour"}, token)[0] for _ in range(6)]
    assert 429 in codes


def test_model_unavailable_gives_503(runtime):
    from campus_agent.llm import LLMError
    from campus_agent.web import create_app

    def broken():
        raise LLMError("ANTHROPIC_API_KEY manquante")

    app = create_app(runtime, llm_factory=broken)
    token = login(app)
    sid = call(app, "POST", "/api/sessions", token=token)[2]["session_id"]
    status, _, body = call(app, "POST", f"/api/sessions/{sid}/messages", {"text": "Bonjour"}, token)
    assert status == 503 and "indisponible" in body["error"]


# ---------------------------------------------------------------- public demo: one school per visitor

@pytest.fixture()
def demo_runtime(tmp_path, monkeypatch, doc_index):
    from campus_agent.config import load_settings
    from campus_agent.runtime import boot

    monkeypatch.setenv("CAMPUS_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUS_NOW", NOW)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("CAMPUS_SECRET_KEY", "test-secret")
    settings = dataclasses.replace(load_settings(env_file=tmp_path / "none.env"), sandbox=True,
                                   visitor_message_limit=3, rate_limit_per_minute=100)
    return boot(settings)


def _booking(text_reply="Réservé."):
    args = {"title": "Révisions", "kind": "reservation", "start": "2026-11-18T14:00", "end": "2026-11-18T16:00",
            "room_id": "BU-S1"}
    return [tool_use("create_event", args), text("Confirme la réservation."), text(text_reply)]


def test_each_visitor_has_their_own_school(demo_runtime):
    app, _ = make_app(demo_runtime, _booking())
    alice, bob = login(app), login(app)
    sid = call(app, "POST", "/api/sessions", token=alice)[2]["session_id"]
    turn = call(app, "POST", f"/api/sessions/{sid}/messages", {"text": "Réserve BU-S1 mercredi"}, alice)[2]
    call(app, "POST", f"/api/sessions/{sid}/actions/{turn['pending_actions'][0]['id']}", {"confirm": True}, alice)

    def wednesday(token):
        events = call(app, "GET", "/api/calendar?start=2026-11-16", token=token)[2]["events"]
        return [e["title"] for e in events if e["start"].startswith("2026-11-18")]

    assert "Révisions" in wednesday(alice) and "Révisions" not in wednesday(bob)
    assert call(app, "GET", f"/api/sessions/{sid}/history", token=bob)[0] == 404   # not in bob's school


def test_changing_persona_keeps_the_visitors_school(demo_runtime):
    app, _ = make_app(demo_runtime, _booking())
    student = login(app)
    sid = call(app, "POST", "/api/sessions", token=student)[2]["session_id"]
    turn = call(app, "POST", f"/api/sessions/{sid}/messages", {"text": "Réserve"}, student)[2]
    call(app, "POST", f"/api/sessions/{sid}/actions/{turn['pending_actions'][0]['id']}", {"confirm": True}, student)
    staff = call(app, "POST", "/api/login", {"person_id": "stf-01", "previous_token": student})[2]["token"]
    entries = call(app, "GET", "/api/audit", token=staff)[2]["entries"]
    assert any(e["tool"] == "create_event" and e["outcome"] == "executed" for e in entries)


def test_visitor_message_quota(demo_runtime):
    app, _ = make_app(demo_runtime, [text("ok")] * 5)
    token = login(app)
    sid = call(app, "POST", "/api/sessions", token=token)[2]["session_id"]
    remaining = [call(app, "POST", f"/api/sessions/{sid}/messages", {"text": "Bonjour"}, token)[2]["quota"]["remaining"]
                 for _ in range(3)]
    assert remaining == [2, 1, 0]
    status, _, body = call(app, "POST", f"/api/sessions/{sid}/messages", {"text": "Encore"}, token)
    assert status == 429 and "3 messages" in body["error"]
    assert call(app, "GET", "/api/me", token=token)[2]["quota"]["remaining"] == 0
    other = login(app)                                         # a new visitor starts afresh
    assert call(app, "GET", "/api/me", token=other)[2]["quota"]["remaining"] == 3


def test_spend_is_shared_across_visitors(demo_runtime):
    app, _ = make_app(demo_runtime, [text("ok")] * 4)
    for _ in range(2):
        token = login(app)
        sid = call(app, "POST", "/api/sessions", token=token)[2]["session_id"]
        call(app, "POST", f"/api/sessions/{sid}/messages", {"text": "Bonjour"}, token)
    health = call(app, "GET", "/api/health")[2]
    assert health["demo"] and health["spent_today_usd"] > 0
    from datetime import date
    assert demo_runtime.ledger.spent(date(2026, 11, 16)) == pytest.approx(health["spent_today_usd"], abs=1e-4)


def test_expired_school_copy_requires_a_new_login(demo_runtime):
    import shutil

    app, _ = make_app(demo_runtime)
    token = login(app)
    for d in demo_runtime.sandboxes.root.iterdir():
        shutil.rmtree(d)
    status, _, body = call(app, "GET", "/api/me", token=token)
    assert status == 401 and "expiré" in body["error"]


def test_old_school_copies_are_cleaned_and_capped(demo_runtime):
    import os
    import time

    boxes = demo_runtime.sandboxes
    boxes.max_count = 3
    ids = [boxes.create() for _ in range(5)]
    assert len(boxes._dirs()) == 3 and boxes.directory(ids[-1])
    old = boxes.root / ids[-1] / "campus.db"
    past = time.time() - boxes.ttl_seconds - 10
    os.utime(old, (past, past))
    assert boxes.directory(ids[-1]) is None
    boxes.cleanup()
    assert not (boxes.root / ids[-1]).exists()
