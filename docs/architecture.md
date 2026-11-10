# Architecture

One tool registry serves three interfaces; one business layer enforces every rule; everything a model
proposes passes through the same checks as a click in the interface.

```
 users ──▶ Web API (Starlette) ──┐        MCP clients ──▶ MCP stdio (local, one person)
           demo login or OIDC    │                        MCP HTTP (remote, bearer token)
           JSON and SSE          │                                   │
                                 ▼                                   │
                          Agent loop (agent.py)                      │
                 Claude Messages API, prompt cache, budgets,         │
                 step cap, model escalation, traces                  │
                                 │                                   │
                                 ▼                                   ▼
                     Tool registry (tools.py): schema validation, role filter, feature flags,
                     pending actions, confirmation, audit log
                                 │
        ┌────────────────────────┼─────────────────────────┐
        ▼                        ▼                         ▼
  Business rules (domain/)   Documents (BM25 or E5)   Integrations (integrations/)
  calendar, academics,       school rules, cited      iCalendar, outbox → SMTP, CalDAV,
  grading, internships,      by passage               GLPI; Moodle; OpenID Connect
  messaging, people
        │
        ▼
  SQLite (one file; one copy per visitor in the public demo)
```

## A request, step by step

1. The browser sends the message with a signed token. The token names the person; nothing in the
   conversation can change who acts.
2. The agent builds the request to Claude: the versioned instructions and the tool definitions (cached),
   the date and the user's identity, the trimmed history.
3. Claude answers with text or tool calls. Each call is validated against its schema, checked against the
   user's role, and run by the business layer, which applies permissions and rules. Every call is logged.
4. A read returns its result to Claude. A write is run in dry-run mode, stored as a *pending action* with a
   readable summary, and shown to the user as a slip.
5. The user confirms or refuses. On confirmation the server runs the action for real, re-checking every
   rule (the slot may have been taken meanwhile), then tells Claude the outcome so it can finish.
6. The turn is saved with its cost; its trace (one span per model call and per tool call) is stored and,
   if configured, exported over OTLP. After a confirmed write, the outbox delivers mail, calendar entries
   and tickets to the connected systems.

## Modules

| Module | Role |
|---|---|
| `domain/` | Permissions and business rules. The only code that writes school data. |
| `tools.py` | Tool definitions, validation, feature flags, pending actions, confirmation. |
| `agent.py`, `llm.py`, `prompts/`, `skills/` | The loop, the Claude client with retries and cost accounting, the versioned instructions, procedures loaded on demand. |
| `web.py`, `static/` | HTTP API, streaming, sign-in, the interface. |
| `mcp_server.py`, `mcp_http.py` | The same tools over MCP: local stdio, and remote HTTP with OAuth bearer tokens. |
| `telemetry.py`, `ops.py` | Traces with OpenTelemetry GenAI names; the office's metrics. |
| `privacy.py` | Export, erasure, retention. |
| `integrations/` | iCalendar, providers (SMTP, CalDAV, GLPI, Moodle), outbox, OpenID Connect. |
| `runtime.py`, `config.py` | Wiring and settings; the per-visitor copies of the demo. |
| `campus_eval/` | The evaluation bench (not shipped in the image). |

## Decisions

Each one is recorded with its context and its alternatives in [`adr/`](adr/).

## Limits

- One process: SQLite and the in-memory rate limiter do not scale across workers ([ADR 1](adr/0001-sqlite.md)).
- The reply text arrives whole; only the tool steps are streamed.
- The remote MCP endpoint and the sign-in were tested with locally signed tokens, not yet against a hosted
  identity provider.
- GLPI and Moodle adapters were tested against recorded shapes of their APIs, not against live servers.
