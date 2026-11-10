# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Changed
- The repository now holds the campus agent only. The RAG chatbot it grew out of lives in
  [ensta-rag-assistant](https://github.com/charbel-dib/ensta-rag-assistant) (version 0).
- One `pyproject.toml` replaces the three requirements files; `ruff` runs in CI.

### Added
- `SECURITY.md`, this changelog, and `docs/roadmap.md`.

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
