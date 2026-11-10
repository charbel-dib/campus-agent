"""Adapters: iCalendar, the outbox with its providers, OpenID Connect tokens, the remote MCP endpoint."""

from __future__ import annotations

import dataclasses
import time

import pytest

from campus_agent import tools
from campus_agent.clock import parse_dt
from campus_agent.integrations import ical, outbox, providers

from conftest import NOW
from test_campus_web import call, login, make_app, runtime  # noqa: F401 - fixture

HYPERPLANNING = """BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//INDEX-EDUCATION//HYPERPLANNING//FR\r
BEGIN:VEVENT\r
UID:Cours-1-2-3@hp\r
DTSTART:20261207T071500Z\r
DTEND:20261207T091500Z\r
SUMMARY;LANGUAGE=fr:Optimisation - M. LE GOFF Yann - Cours\r
LOCATION;LANGUAGE=fr:B-104\r
CATEGORIES:HYPERPLANNING\r
DESCRIPTION;LANGUAGE=fr:Matière : Optimisation\\nEnseignant : M. LE GOFF Yann\\nSalle\r
  : B-104\\, bâtiment B\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:Cours-4@hp\r
DTSTART;TZID=Europe/Paris:20261208T103000\r
DTEND;TZID=Europe/Paris:20261208T123000\r
SUMMARY:Optimisation - TD\r
CATEGORIES:TD\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:journee@hp\r
DTSTART;VALUE=DATE:20261211\r
SUMMARY:Journée banalisée\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_ical_feed_is_valid_and_round_trips(conn):
    text = ical.feed(conn, person_id="stu-0001")
    lines = text.split("\r\n")
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-2] == "END:VCALENDAR"
    assert all(len(line.encode()) <= 75 for line in lines)                       # RFC 5545 line folding
    events = ical.parse(text)
    own = conn.execute("SELECT COUNT(DISTINCT e.id) FROM events e LEFT JOIN event_groups g ON g.event_id = e.id "
                       "LEFT JOIN event_people p ON p.event_id = e.id WHERE e.status = 'confirmed' AND "
                       "(e.organizer_id = 'stu-0001' OR p.person_id = 'stu-0001' OR g.group_id = '3A-IA')").fetchone()[0]
    assert len(events) == own > 50
    row = conn.execute("SELECT * FROM events WHERE id = ?", (int(events[0]["uid"].split("-")[1].split("@")[0]),)).fetchone()
    assert events[0]["start"] == row["start"] and events[0]["summary"] == row["title"]      # UTC and back
    assert "DESCRIPTION" not in text                                             # other people's free text stays out


def test_ical_escaping_and_folding():
    row = {"id": 1, "title": "Réunion; budget, suite\nligne 2 " + "x" * 80, "kind": "reunion", "start": "2026-11-17T10:00",
           "end": "2026-11-17T11:00", "updated_at": NOW, "status": "confirmed", "room_id": None}
    back = ical.parse(ical.calendar([ical.event_to_vevent(row)]))[0]
    assert back["summary"] == row["title"] and back["start"] == row["start"]


def test_timetable_export_is_imported_once(conn):
    out = ical.import_events(conn, HYPERPLANNING, "tch-001", "3A-IA", parse_dt(NOW))
    assert out == {"created": 2, "skipped": 0}                                   # the all-day entry is ignored
    rows = conn.execute("SELECT * FROM events WHERE created_at = ? ORDER BY start", (NOW,)).fetchall()
    assert (rows[0]["start"], rows[0]["room_id"], rows[0]["kind"]) == ("2026-12-07T08:15", "B-104", "cours")
    assert (rows[1]["start"], rows[1]["kind"]) == ("2026-12-08T10:30", "td")
    assert ical.import_events(conn, HYPERPLANNING, "tch-001", "3A-IA", parse_dt(NOW)) == {"created": 0, "skipped": 2}


def test_outbox_delivers_mail_calendar_and_tickets_and_retries(ctx_for, conn):
    class FlakyMail(providers.MemoryMail):
        fail = True

        def send(self, to, subject, body):
            if self.fail:
                raise ConnectionError("smtp down")
            super().send(to, subject, body)

    p = providers.Providers(mail=FlakyMail(), calendar=providers.MemoryCalendar(), tickets=providers.MemoryTickets())
    now = parse_dt(NOW)
    assert outbox.deliver(conn, p, now) == {"sent": 0, "failed": 0}               # first pass: nothing from the past
    teacher = ctx_for("tch-001")
    tools.execute(teacher, "send_announcement", {"subject": "Salle", "body": "Le TP a lieu en C-202.",
                                                 "person_ids": ["stu-0001", "stu-0002"]}, confirm_writes=False)
    tools.execute(ctx_for("stu-0001"), "create_ticket", {"category": "informatique", "subject": "Wifi",
                                                         "body": "Le wifi ne marche pas en B-104."}, confirm_writes=False)
    ev = tools.execute(ctx_for("stu-0001"), "create_event", {"title": "Révisions", "kind": "reservation", "room_id": "BU-S1",
                                                             "start": "2026-11-18T14:00", "end": "2026-11-18T16:00"},
                       confirm_writes=False)
    assert not ev.is_error, ev.content
    assert outbox.deliver(conn, p, now) == {"sent": 2, "failed": 1}               # the mail failed, nothing is lost
    assert p.tickets.created[0]["requester"].endswith("@ker-avel.example") and len(p.calendar.items) == 1
    assert "SUMMARY:Révisions" in next(iter(p.calendar.items.values()))
    assert conn.execute("SELECT status, last_error FROM outbox WHERE kind = 'mail'").fetchone()["last_error"] == "smtp down"
    p.mail.fail = False
    assert outbox.deliver(conn, p, now) == {"sent": 1, "failed": 0}
    assert sorted(m["to"].split("@")[0] for m in p.mail.sent) == sorted(
        r[0].split("@")[0] for r in conn.execute("SELECT email FROM people WHERE id IN ('stu-0001', 'stu-0002')"))
    assert outbox.deliver(conn, p, now) == {"sent": 0, "failed": 0}               # nothing is sent twice


def test_glpi_and_moodle_adapters_speak_the_real_api():
    class Reply:
        def __init__(self, data):
            self.data, self.status_code = data, 200

        def json(self):
            return self.data

        def raise_for_status(self):
            pass

    class Http:
        def __init__(self):
            self.calls = []

        def get(self, url, **kw):
            self.calls.append(("GET", url, kw))
            if url.endswith("initSession"):
                return Reply({"session_token": "abc"})
            return Reply({"courses": [{"fullname": "IA301", "assignments": [{"name": "Rapport", "duedate": 1795000000},
                                                                               {"name": "Notebook", "duedate": 1794000000}]}]})

        def post(self, url, **kw):
            self.calls.append(("POST", url, kw))
            return Reply({"id": 42})

    http = Http()
    glpi = providers.GlpiTickets("https://glpi.example/apirest.php", "APP", "USER", session=http)
    assert glpi.create("Wifi", "En panne", "ines@ker-avel.example", "informatique") == "42"
    assert [c[1].rsplit("/", 1)[1] for c in http.calls] == ["initSession", "Ticket", "killSession"]
    assert http.calls[0][2]["headers"]["Authorization"] == "user_token USER"
    assert http.calls[1][2]["headers"]["Session-Token"] == "abc" and http.calls[1][2]["json"]["input"]["name"] == "Wifi"
    moodle = providers.MoodleAssignments("https://moodle.example", "TOK", session=http)
    assert [a["name"] for a in moodle.upcoming()] == ["Notebook", "Rapport"]
    assert http.calls[-1][2]["params"]["wsfunction"] == "mod_assign_get_assignments"


# ---------------------------------------------------------------- OpenID Connect and remote MCP

jwt = pytest.importorskip("jwt")
ISSUER = "https://sso.ker-avel.example/realms/campus"
RESOURCE = "https://agent.ker-avel.example/mcp"


@pytest.fixture(scope="module")
def keys():
    from cryptography.hazmat.primitives.asymmetric import rsa

    return rsa.generate_private_key(public_exponent=65537, key_size=2048), rsa.generate_private_key(65537, 2048)


def token(key, **claims):
    base = {"iss": ISSUER, "aud": RESOURCE, "exp": int(time.time()) + 300, "email": "yann.le-goff@ker-avel.example"}
    return jwt.encode({**base, **claims}, key, algorithm="RS256")


class Jwks:
    def __init__(self, key):
        self.key = key.public_key()

    def get_signing_key_from_jwt(self, _token):
        return type("K", (), {"key": self.key})()


def test_tokens_are_checked_for_signature_issuer_audience_and_expiry(keys, conn):
    from campus_agent.integrations.oidc import AuthError, Verifier, person_for

    good, other = keys
    v = Verifier(ISSUER, RESOURCE, jwks_client=Jwks(good))
    email = conn.execute("SELECT email FROM people WHERE id = 'tch-001'").fetchone()[0]
    assert person_for(conn, v.claims(token(good, email=email))) == "tch-001"
    for bad in (token(other, email=email), token(good, aud="https://another.example"), token(good, iss="https://evil.example"),
                token(good, exp=int(time.time()) - 10), "not-a-token"):
        with pytest.raises(AuthError):
            v.claims(bad)
    with pytest.raises(AuthError):
        person_for(conn, v.claims(token(good, email="stranger@example.org")))     # valid token, not a member


def test_remote_mcp_acts_for_the_bearer(keys, runtime):  # noqa: F811
    from starlette.applications import Starlette

    from campus_agent import mcp_http
    from campus_agent.integrations.oidc import Verifier

    good, other = keys
    conn = runtime.connect()
    mail = {pid: conn.execute("SELECT email FROM people WHERE id = ?", (pid,)).fetchone()[0] for pid in ("stu-0001", "tch-001")}
    conn.close()
    app = Starlette(routes=mcp_http.routes(runtime, Verifier(ISSUER, RESOURCE, jwks_client=Jwks(good)), RESOURCE))
    meta = call(app, "GET", "/.well-known/oauth-protected-resource")[2]
    assert meta == {"resource": RESOURCE, "authorization_servers": [ISSUER], "bearer_methods_supported": ["header"],
                    "resource_name": "Ker Avel campus tools"}
    status, headers, _ = call(app, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert status == 401 and "resource_metadata=" in headers["www-authenticate"]
    assert call(app, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                token=token(other, email=mail["stu-0001"]))[0] == 401
    assert call(app, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                token=token(good, email=mail["stu-0001"], aud="https://another.example"))[0] == 401

    def rpc(who, method, params=None, mid=1):
        return call(app, "POST", "/mcp", {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}},
                    token=token(good, email=mail[who]))[2]

    init = rpc("stu-0001", "initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert init["protocolVersion"] == "2025-06-18" and "Haddad" in init["instructions"]
    names = lambda who: {t["name"] for t in rpc(who, "tools/list")["result"]["tools"]}  # noqa: E731
    assert "justify_absence" in names("stu-0001") and "justify_absence" not in names("tch-001")
    assert "enter_grades" in names("tch-001") and "enter_grades" not in names("stu-0001")
    denied = rpc("stu-0001", "tools/call", {"name": "get_grades", "arguments": {"student_id": "stu-0002"}})["result"]
    assert denied["isError"] and "propres données" in denied["content"][0]["text"]
    assert rpc("stu-0001", "nope")["error"]["code"] == -32601
    assert call(app, "POST", "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"},
                token=token(good, email=mail["stu-0001"]))[0] == 202


def test_calendar_subscription_link(runtime):  # noqa: F811
    app, _ = make_app(runtime)
    url = call(app, "GET", "/api/ical-link", token=login(app))[2]["url"]
    status, headers, body = call(app, "GET", url.split("http://test")[1])
    assert status == 200 and headers["content-type"].startswith("text/calendar") and b"BEGIN:VEVENT" in body
    assert call(app, "GET", "/ical/forged.ics")[0] == 401
    assert dataclasses.is_dataclass(runtime.settings)
