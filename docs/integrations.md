# Integrations

Each adapter sits behind an interface (`integrations/providers.py`) with an in-memory double used by the
tests. Nothing is active unless its variable is set. `docker compose -f docker-compose.integrations.yml up -d`
starts local servers to try them.

| Need | Protocol or API | Variable | Local server |
|---|---|---|---|
| Subscribe to one's timetable | iCalendar feed (RFC 5545) | none | any calendar application |
| Load a timetable export | iCalendar import | none | file from HyperPlanning, ADE Campus or Celcat |
| Send notices by e-mail | SMTP | `CAMPUS_SMTP_URL=smtp://localhost:1025` | Mailpit, http://localhost:8025 |
| Push appointments to a personal calendar | CalDAV | `CAMPUS_CALDAV_URL=http://campus:campus@localhost:5232/campus/agenda/` | Radicale |
| Open tickets in the helpdesk | GLPI REST API | `CAMPUS_GLPI_URL`, `CAMPUS_GLPI_APP_TOKEN`, `CAMPUS_GLPI_USER_TOKEN` | — |
| Read assignments and due dates | Moodle web services | `CAMPUS_MOODLE_URL`, `CAMPUS_MOODLE_TOKEN` | — |
| Sign in with the school account | OpenID Connect, authorization code with PKCE | `CAMPUS_OIDC_ISSUER`, `CAMPUS_OIDC_CLIENT_ID`, `CAMPUS_PUBLIC_URL` | Keycloak, realm `campus` |
| Serve the tools to MCP clients | Streamable HTTP, OAuth bearer tokens | same as sign-in, plus `CAMPUS_OIDC_AUDIENCE` | Keycloak client `campus-mcp` |
| Send traces | OTLP/HTTP | `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006` | Phoenix |

**What has been verified.** The iCalendar code, the outbox, the token checks and the MCP endpoint are covered
by tests that run in CI. The GLPI and Moodle adapters are tested against the documented request and response
shapes, not against live servers. The compose file and the Keycloak realm have not been exercised in CI.

## Calendar feeds

"S'abonner à cet agenda" gives a private link (`/ical/<signed token>.ics`) to paste into Google Calendar,
Outlook or Apple Calendar. It lists the person's confirmed events in UTC.

```bash
python -m campus_agent.cli import-ics export.ics --organizer tch-001 --group 3A-IA
```

creates one class per VEVENT of a timetable export, maps `LOCATION` to a room when it matches a room name or
code, reads `CATEGORIES` (CM, TD, TP, EXAMEN) and skips sessions already present.

## Outbox

The business layer writes only to the school database. After each confirmed action, and on `cli sync`, the
outbox records what is new (messages, tickets, appointments and bookings) and delivers it. A failed delivery
is kept with its error and retried, up to five times; an event changed after it was sent is sent again; a
cancelled one is deleted from the calendar.

## Sign-in

With Keycloak from the compose file (users `stu-0001`, `stu-0003`, `tch-001`, `stf-01`, password `demo`):

```bash
pip install -e ".[sso]"
export CAMPUS_PUBLIC_URL=http://localhost:8002
export CAMPUS_OIDC_ISSUER=http://localhost:8080/realms/campus CAMPUS_OIDC_CLIENT_ID=campus-agent
export CAMPUS_DEMO_LOGIN=0
uvicorn campus_agent.web:app --port 8002
```

The person is matched on the e-mail claim of a verified token; an account that is not in the school is refused.
For Renater's federation (Shibboleth, CAS), put a bridge that speaks OpenID Connect in front, which Keycloak can be.

## Remote MCP

With sign-in configured, `POST /mcp` serves the tools for the bearer of an access token whose audience is
`<public URL>/mcp`, and `/.well-known/oauth-protected-resource` tells clients where to get one. The Keycloak
client `campus-mcp` adds that audience. See [ADR 7](adr/0007-remote-mcp-with-oauth.md).

## Connecting a system that is not listed

Implement the three or four methods of the matching protocol in `providers.py` (for Microsoft 365: Graph
`/me/events` behind `CalendarProvider`, Graph `sendMail` behind `MailProvider`), return it from
`from_settings`, and add a test with a fake session as `test_glpi_and_moodle_adapters_speak_the_real_api` does.
