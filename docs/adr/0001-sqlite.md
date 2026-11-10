# 1. SQLite as the store

**Context.** The demo runs on one free instance; each visitor gets a private copy of the school.
**Decision.** SQLite in WAL mode, one file per school. Copying a visitor's school is a file copy; tests and each evaluation run start from a fresh file in milliseconds.
**Alternatives.** PostgreSQL: needed for several workers and for row-level locking under load, but it would make per-visitor copies and per-run isolation slower and the demo paid.
**Consequences.** One process only. SQL stays portable (no SQLite-only feature besides `PRAGMA`), so moving to PostgreSQL means changing `db.py`, the upsert syntax and the connection handling. Not done.
