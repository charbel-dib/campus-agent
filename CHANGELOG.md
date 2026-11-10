# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [2.0.0] — 2026-11-10

### Added
- **Operations.** Traces named after the OpenTelemetry GenAI conventions, stored and optionally exported
  over OTLP; thumbs on every reply; hand-off to the office; a metrics tab for the office (usage, cost,
  latency, confirmation rate, refusals, unmet requests); `/api/status`; feature flags to switch a tool off.
- **Personal data.** Export and erasure from the interface and the command line; retention periods; a
  record of processing (`docs/privacy.md`).
- **Integrations.** iCalendar subscription feeds and import of a timetable export; an outbox delivering
  to SMTP, CalDAV and GLPI with retries; Moodle assignments; OpenID Connect sign-in; a remote MCP endpoint
  with bearer tokens bound to the resource; `docker-compose.integrations.yml` (Mailpit, Radicale, Keycloak, Phoenix).
- **Domains.** Internship agreements (request, tutor validation, signature), grade entry and publication,
  make-up registration: seven tools, their rules in the school documents, 14 evaluation tasks.
- **Agent.** Tool steps streamed over server-sent events. Off by default: escalation to a stronger model,
  clickable clarifying questions, procedures loaded on demand, one-hour prompt cache.
- **Security.** Threat model with one named test per control; `security.yml` (pip-audit, bandit, Trivy,
  CycloneDX bill of materials); Dependabot.
- **Documentation.** Architecture, eight decision records, threat model, evaluation method and report,
  operations runbook, privacy, integrations, business rules, a guide per role and a FAQ in French.

### Changed
- The instructions live in `campus_agent/prompts/system.md`; their hash is in every trace and result.
- The agent keeps to the period the user gives and asks for the date of a room booking.
- Final campaign: 707 of 712 runs passed with Claude Haiku 5.5 (`docs/evaluation.md`).

## [1.1.0] — 2026-11-10

### Added
- `campus_eval`: 156 tasks in eight suites (simulated user and pass^k, tool-call accuracy, milestones and
  minefields, injection in each data channel, access canaries, groundedness, robustness, regression),
  bootstrap intervals, and a CI gate (`eval.yml`).

### Changed
- The repository holds the campus agent only. The RAG chatbot it grew out of lives in
  [ensta-rag-assistant](https://github.com/charbel-dib/ensta-rag-assistant) (version 0).
- One `pyproject.toml` replaces the three requirements files; `ruff` runs in CI.
- Agent instructions and the ticket tool fixed from the failures of the first campaigns.

### Removed
- `eval/run_agent_eval.py` and its scenario files, migrated to `eval/suites/core.json`.

## [1.0.0] — 2026-11-09

First public version, deployed at https://agent-campus.onrender.com.

### Added
- A generated fictional engineering school (290 students, 30 teachers, 3 staff, 26 rooms, one semester)
  and its written rules.
- 18 tools filtered by role: calendars, free slots, rooms, appointments within office hours, study-room
  bookings, grades, absences and their justification, PDF certificates, messages, tickets, cited document
  search.
- Server-side permissions; every write held as a pending action until the user confirms, then
  re-validated; audit log of every tool call.
- Claude tool-use loop with prompt caching, step cap, budgets per conversation and per day, cost shown
  to the user; MCP server exposing the same tools.
- Web interface with a presentation page, confirmation slips, week view, inbox and audit tab.
- Public demo mode: a private copy of the school per visitor, message and cost quota, shared daily ledger.
- Evaluation: 29 tuning and 12 held-out scenarios checked on the database; results for Claude Haiku 5.5
  and Sonnet 5.5.
- Docker image, GitHub CI (tests and image probe), Render deployment, keep-alive workflow.
