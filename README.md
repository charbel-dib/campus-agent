# Ker Avel campus agent

[![Tests](https://github.com/charbel-dib/ensta_chatbot/actions/workflows/tests.yml/badge.svg)](https://github.com/charbel-dib/ensta_chatbot/actions/workflows/tests.yml)

An AI agent for an engineering school: it reads calendars, finds common free slots, books rooms
and appointments, moves classes and tells the students, handles absence justifications, issues
certificates and answers from the school rules, with citations. It acts with the rights of the
person signed in, and **nothing changes until that person confirms**.

It runs on a fictional school, *École d'ingénieurs Ker Avel*: 290 students, 30 teachers, 3 staff,
26 rooms and a full semester, all generated. The same tools are served to a web interface, through
the Claude API, and to any MCP client such as Claude Desktop.

This repository started as a RAG chatbot over the ENSTA website ([version 0](docs/ensta-rag.md),
still in `app.py`). The agent is the next step: from answering questions to doing the work.

<!-- DEMO -->

## In one minute

<!-- RESULTS -->

- **18 tools, filtered by role.** A student books a study room or an appointment, a teacher
  moves a class for their groups, the school office reviews justifications.
- **Every write waits for the user.** The agent proposes, the user sees a slip with the exact
  action and confirms; the server then checks every rule again before writing.
- **Permissions live on the server**, not in the prompt: a student who talks the model into
  asking for someone else's grades still gets a refusal from the tool.
- **Measured**: 29 scenarios checked on the database after each run, plus 87 tests without any
  model call.

## Try it

```bash
pip install -r requirements-agent.txt
echo ANTHROPIC_API_KEY=sk-ant-... > .env      # set a monthly spend limit in the Anthropic console first

python -m campus_agent.cli ask --as stu-0001 "Prends-moi un rendez-vous avec M. Le Goff" -v
uvicorn campus_agent.web:app --port 8002      # then open http://127.0.0.1:8002
```

The school is generated on first start (`campus_data/campus.db`, about 0.2 s). `python -m
campus_agent.cli reset` regenerates it. Set `CAMPUS_NOW=2026-11-16T09:00` to pin the clock, as
the evaluation does.

With Docker: `docker build -t campus-agent . && docker run -p 8000:8000 -e ANTHROPIC_API_KEY=...
-e CAMPUS_SECRET_KEY=... campus-agent`.

### Personas of the demo

| Id | Person | Try |
|---|---|---|
| `stu-0001` | Inès Haddad, 3rd year, AI track | « J'étais absente au TP de vendredi, j'ai un certificat médical » |
| `stu-0003` | Maël Guivarc'h, 1st year | « Combien de temps ai-je pour justifier une absence ? » |
| `tch-001` | Yann Le Goff, machine learning | « Je ne peux pas assurer mon TP de vendredi : déplace-le la semaine prochaine et préviens les étudiants » |
| `tch-008` | Hugo Lefèvre, calculus | « Organise un rattrapage de deux heures pour le groupe 1A-TD1 la semaine prochaine » |
| `stf-01` | Claire Morvan, school office | « Quelles justifications d'absence sont en attente ? » |

## What the agent can do

| Tool | Student | Teacher | Office | Writes |
|---|:-:|:-:|:-:|:-:|
| `search_people`, `search_courses` | ✓ | ✓ | ✓ | |
| `get_schedule` (own in detail; others as busy/free only) | ✓ | ✓ | ✓ | |
| `find_free_slots` (people, groups, a free room) | ✓ | ✓ | ✓ | |
| `find_available_rooms` | study rooms | teaching rooms | all | |
| `get_grades` | own, published | own courses | all | |
| `get_absences` | own | own courses | all | |
| `search_documents` (school rules, cited) | ✓ | ✓ | ✓ | |
| `get_messages`, `list_tickets` | ✓ | ✓ | ✓ | |
| `generate_certificate` (PDF) | own | | ✓ | |
| `create_event` | study room, appointment in office hours | classes for own groups, meetings | ✓ | ✓ |
| `update_event`, `cancel_event` (participants notified) | own events | own courses | ✓ | ✓ |
| `justify_absence` (within five working days) | ✓ | | | ✓ |
| `review_absence` | | | ✓ | ✓ |
| `send_announcement` | | own groups | ✓ | ✓ |
| `create_ticket` | ✓ | ✓ | ✓ | ✓ |

The school rules the tools enforce are the ones written in its documents
([`campus_agent/data/docs`](campus_agent/data/docs)): study rooms two hours at most and once a
day, appointments of 15 to 30 minutes inside a teacher's office hours, no class on Wednesday
afternoon, justification within five working days, a refusal always explained, and so on.

## How it works

```mermaid
flowchart LR
    UI[Web interface] -->|HTTP| API[campus_agent.web]
    CD[Claude Desktop / MCP client] -->|MCP stdio| MCP[campus_agent.mcp_server]
    API --> AG[Agent loop<br/>campus_agent.agent]
    AG <-->|Messages API, tool use| CL[(Claude)]
    AG --> TR[Tool registry<br/>campus_agent.tools]
    MCP --> TR
    TR --> DOM[Business rules and permissions<br/>campus_agent.domain]
    DOM --> DB[(SQLite: school, pending actions, audit log)]
    DOM --> DOCS[School rules, BM25 / E5 search]
```

One registry describes each tool once (schema, roles, read or write). The agent, the MCP server
and the evaluation all go through it, so the rules cannot drift between interfaces.

**Confirmation of writes.** A write tool never runs from the model's call. The registry validates
it in a dry run, stores a *pending action* with a summary written by the server (not by the
model), and returns « waiting for confirmation » to the model. Only the user's click runs it, and
the business function checks everything again at that moment: if the slot was taken meanwhile,
the action fails cleanly and the agent is told why.

```mermaid
sequenceDiagram
    participant U as User
    participant A as Agent
    participant C as Claude
    participant T as Tools
    U->>A: Move my Friday lab to next week
    A->>C: request + tools for this role
    C->>T: get_schedule, find_free_slots
    C->>T: update_event(...)
    T-->>C: pending action #12 (nothing written)
    A-->>U: reply + slip « Move … and notify 30 people »
    U->>A: Confirm #12
    A->>T: run #12, rules checked again
    T-->>A: done, 30 students notified
    A->>C: [Decision] confirmed, result
    C-->>U: summary
```

In an MCP client, the client's own approval step plays that role: writes run when called, and
every write tool carries `destructiveHint` so the client asks the user first.

## Built for production, not only for the demo

- **Permissions from the token, never from the conversation.** The web API signs a token for the
  person (a demo login here; the school's single sign-on in production). Every business function
  checks what this person may see or change.
- **Data minimisation (GDPR).** Other people's calendars come back as busy/free only. A student sees
  other students' names and groups, not their e-mail. Free text in the audit log is truncated.
- **Prompt injection.** Text written by users (event notes, messages) comes back to the model
  labelled as user data, and the instructions say it is never an order. Even when the model is
  fooled, the attacker's text cannot widen the user's rights, and every write still needs the
  user's click. Three evaluation scenarios plant such text.
- **Audit trail.** Every tool call is logged with who, what, through which channel and with which
  outcome (`ok`, `pending`, `executed`, `denied`, `conflict`…); the office sees it in the interface.
- **Cost control.** Prompt caching of the tools, instructions and conversation; a step cap per
  turn; a budget per conversation and per day; the cost of each exchange computed from the
  published prices and shown to the user.
- **Robustness.** Retries with back-off on rate limits and overloads, timeouts, tool errors
  returned to the model so it can recover, history trimmed only on turn boundaries.
- **Engineering.** Typed settings from the environment, a dependency-light core (standard library,
  `requests`, `reportlab`), 87 tests, CI that also builds the Docker image and probes it,
  non-root container with a health check.

## Evaluation

`eval/agent_scenarios.json` holds 29 scenarios in seven categories: reading, rules (with the
expected citation), planning (actions confirmed by the harness), permissions, ambiguous requests,
conflicts and injection. Each runs on a fresh copy of the school; the checks look at the tools
called, the actions proposed, **the database afterwards** and the reply. No model grades another.

```bash
python eval/run_agent_eval.py --model claude-haiku-5-5
python eval/run_agent_eval.py --model claude-sonnet-5-5
```

<!-- EVAL -->

## Use it from Claude Desktop

Add the server to Claude Desktop's configuration (`claude_desktop_config.json`), choosing who it acts for:

```json
{
  "mcpServers": {
    "ker-avel": {
      "command": "python",
      "args": ["-m", "campus_agent.mcp_server", "--as", "tch-001"],
      "env": { "PYTHONPATH": "C:/path/to/ensta_chatbot" }
    }
  }
}
```

## Repository

| Path | What |
|---|---|
| `campus_agent/domain/` | Business rules and permissions: calendar, academics, messaging, documents, audit |
| `campus_agent/tools.py` | Tool registry, validation, pending actions and confirmation |
| `campus_agent/agent.py`, `llm.py` | Agent loop, Claude client, cost accounting |
| `campus_agent/web.py`, `static/` | HTTP API and interface |
| `campus_agent/mcp_server.py` | MCP server |
| `campus_agent/seed.py`, `data/docs/` | Generated school and its written rules |
| `eval/run_agent_eval.py` | Scenario evaluation |
| `tests/` | Tests (agent, tools, rules, web, MCP, evaluation harness, and the v0 RAG) |
| `app.py`, `ENSTA_DATASET/` | Version 0, the ENSTA RAG ([its README](docs/ensta-rag.md)) |

## Limits and next steps

- SQLite and an in-memory rate limiter fit one process; several workers need PostgreSQL and a
  shared store.
- The calendar is the school's own. An adapter to Google Calendar or Microsoft 365 would sit behind
  the same business functions.
- The demo login stands in for single sign-on; the MCP server acts for one person set at start, where
  a remote deployment would read the person from an OAuth token.
- Internship agreements and grade entry are documented in the rules but not yet tools.

MIT licence. The school, its people and its data are fictional.
