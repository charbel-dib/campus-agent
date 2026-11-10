"""SQLite storage: schema and connection helpers.

Times are stored as naive local school time ("YYYY-MM-DDTHH:MM", Europe/Paris). The school
runs in a single time zone, so this keeps comparisons simple and readable; an adapter to an
external calendar converts at its boundary.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS groups (
    id    TEXT PRIMARY KEY,           -- e.g. 1A-TD2, 3A-IA
    name  TEXT NOT NULL,
    year  TEXT NOT NULL,              -- 1A, 2A, 3A
    track TEXT                        -- 3A specialty, NULL otherwise
);

CREATE TABLE IF NOT EXISTS people (
    id         TEXT PRIMARY KEY,      -- stu-0001, tch-001, stf-01
    role       TEXT NOT NULL CHECK (role IN ('student', 'teacher', 'staff')),
    first_name TEXT NOT NULL,
    last_name  TEXT NOT NULL,
    email      TEXT NOT NULL UNIQUE,
    group_id   TEXT REFERENCES groups(id),
    department TEXT,
    title      TEXT
);
CREATE INDEX IF NOT EXISTS idx_people_group ON people(group_id);

CREATE TABLE IF NOT EXISTS rooms (
    id        TEXT PRIMARY KEY,       -- A-AMPHI1, B-104, C-202, BU-S2
    name      TEXT NOT NULL,
    building  TEXT NOT NULL,
    capacity  INTEGER NOT NULL,
    kind      TEXT NOT NULL CHECK (kind IN ('amphi', 'td', 'tp_info', 'labo', 'study', 'meeting')),
    equipment TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS courses (
    id    TEXT PRIMARY KEY,           -- IA301
    title TEXT NOT NULL,
    ects  REAL NOT NULL,
    year  TEXT NOT NULL,
    track TEXT
);

CREATE TABLE IF NOT EXISTS course_sections (
    id         TEXT PRIMARY KEY,      -- IA301/3A-IA
    course_id  TEXT NOT NULL REFERENCES courses(id),
    group_id   TEXT NOT NULL REFERENCES groups(id),
    teacher_id TEXT NOT NULL REFERENCES people(id)
);
CREATE INDEX IF NOT EXISTS idx_sections_teacher ON course_sections(teacher_id);
CREATE INDEX IF NOT EXISTS idx_sections_group ON course_sections(group_id);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('cours', 'td', 'tp', 'examen', 'reunion',
                                               'permanence', 'rendez_vous', 'reservation', 'autre')),
    start        TEXT NOT NULL,
    end          TEXT NOT NULL,
    room_id      TEXT REFERENCES rooms(id),
    course_id    TEXT REFERENCES courses(id),
    organizer_id TEXT NOT NULL REFERENCES people(id),
    status       TEXT NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed', 'cancelled')),
    description  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start);
CREATE INDEX IF NOT EXISTS idx_events_room ON events(room_id, start);

CREATE TABLE IF NOT EXISTS event_people (
    event_id  INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES people(id),
    PRIMARY KEY (event_id, person_id)
);
CREATE INDEX IF NOT EXISTS idx_event_people_person ON event_people(person_id);

CREATE TABLE IF NOT EXISTS event_groups (
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    group_id TEXT NOT NULL REFERENCES groups(id),
    PRIMARY KEY (event_id, group_id)
);
CREATE INDEX IF NOT EXISTS idx_event_groups_group ON event_groups(group_id);

CREATE TABLE IF NOT EXISTS grades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  TEXT NOT NULL REFERENCES people(id),
    course_id   TEXT NOT NULL REFERENCES courses(id),
    label       TEXT NOT NULL,
    score       REAL NOT NULL,
    coefficient REAL NOT NULL,
    date        TEXT NOT NULL,
    published   INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_grades_student ON grades(student_id);

CREATE TABLE IF NOT EXISTS absences (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id     TEXT NOT NULL REFERENCES people(id),
    event_id       INTEGER NOT NULL REFERENCES events(id),
    status         TEXT NOT NULL CHECK (status IN ('non_justifiee', 'en_attente', 'justifiee', 'refusee')),
    reason         TEXT NOT NULL DEFAULT '',
    submitted_at   TEXT,
    reviewed_by    TEXT REFERENCES people(id),
    reviewed_at    TEXT,
    review_comment TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_absences_student ON absences(student_id);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id       TEXT NOT NULL REFERENCES people(id),
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    recipients_json TEXT NOT NULL,      -- {"people": [...], "groups": [...]}
    recipient_count INTEGER NOT NULL,
    event_id        INTEGER REFERENCES events(id),
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message_recipients (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    person_id  TEXT NOT NULL REFERENCES people(id),
    PRIMARY KEY (message_id, person_id)
);
CREATE INDEX IF NOT EXISTS idx_message_recipients_person ON message_recipients(person_id);

CREATE TABLE IF NOT EXISTS tickets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id  TEXT NOT NULL REFERENCES people(id),
    category   TEXT NOT NULL,
    subject    TEXT NOT NULL,
    body       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'ouvert',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS certificates (
    id           TEXT PRIMARY KEY,        -- random reference, also the file name
    student_id   TEXT NOT NULL REFERENCES people(id),
    requested_by TEXT NOT NULL REFERENCES people(id),
    path         TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id    TEXT NOT NULL REFERENCES people(id),
    session_id  TEXT,
    tool        TEXT NOT NULL,
    args_json   TEXT NOT NULL,
    summary     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'executed', 'cancelled', 'expired', 'failed')),
    created_at  TEXT NOT NULL,
    decided_at  TEXT,
    result_json TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    actor_id  TEXT NOT NULL,
    channel   TEXT NOT NULL,              -- agent, mcp, api, eval
    tool      TEXT NOT NULL,
    args_json TEXT NOT NULL,
    outcome   TEXT NOT NULL,              -- ok, pending, executed, denied, invalid, conflict, not_found, error
    detail    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_id, ts);

CREATE TABLE IF NOT EXISTS agent_sessions (
    id            TEXT PRIMARY KEY,
    actor_id      TEXT NOT NULL REFERENCES people(id),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    messages_json TEXT NOT NULL DEFAULT '[]',
    model         TEXT NOT NULL DEFAULT '',
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0
);

-- Telemetry: one row per span, named and attributed after the OpenTelemetry GenAI conventions.
CREATE TABLE IF NOT EXISTS spans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id    TEXT NOT NULL,
    span_id     TEXT NOT NULL,
    parent_id   TEXT,
    name        TEXT NOT NULL,
    session_id  TEXT,
    actor_id    TEXT,
    start_ns    INTEGER NOT NULL,
    duration_ms REAL NOT NULL,
    status      TEXT NOT NULL DEFAULT 'ok',
    attributes_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id);

CREATE TABLE IF NOT EXISTS feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    actor_id   TEXT NOT NULL,
    trace_id   TEXT NOT NULL DEFAULT '',
    rating     INTEGER NOT NULL CHECK (rating IN (-1, 1)),
    reason     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS internships (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id    TEXT NOT NULL REFERENCES people(id),
    company       TEXT NOT NULL,
    subject       TEXT NOT NULL,
    start_date    TEXT NOT NULL,
    end_date      TEXT NOT NULL,
    company_tutor TEXT NOT NULL,
    school_tutor_id TEXT NOT NULL REFERENCES people(id),
    status        TEXT NOT NULL CHECK (status IN ('deposee', 'validee_tuteur', 'signee', 'refusee')),
    comment       TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS makeup_registrations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL REFERENCES people(id),
    course_id  TEXT NOT NULL REFERENCES courses(id),
    reason     TEXT NOT NULL CHECK (reason IN ('absence_justifiee', 'moyenne_insuffisante')),
    created_at TEXT NOT NULL,
    UNIQUE (student_id, course_id)
);

-- Deliveries to outside systems (mail, calendar, helpdesk): written with the change, sent afterwards.
CREATE TABLE IF NOT EXISTS outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,              -- mail, calendar, ticket
    ref        TEXT NOT NULL,              -- messages.id, events.id or tickets.id
    status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE (kind, ref)
);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    """Open a connection with foreign keys on and rows readable by column name."""
    conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on any error: a write is all or nothing."""
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
