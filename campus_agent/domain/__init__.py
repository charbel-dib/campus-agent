"""Business layer shared by every interface (agent, MCP server, HTTP API, evaluation).

Permissions are enforced here, on the server, from the authenticated actor. The language
model never chooses who it acts as: it only proposes arguments, which these functions check.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ..clock import Clock


class DomainError(Exception):
    """An expected refusal, explained to the user. ``code`` is logged in the audit trail."""

    code = "error"

    def __init__(self, message: str, details: Any = None):
        super().__init__(message)
        self.message = message
        self.details = details


class PermissionDenied(DomainError):
    code = "denied"


class NotFound(DomainError):
    code = "not_found"


class InvalidRequest(DomainError):
    code = "invalid"


class Conflict(DomainError):
    code = "conflict"


@dataclass(frozen=True)
class Actor:
    id: str
    role: str                 # student, teacher, staff
    first_name: str
    last_name: str
    email: str
    group_id: str | None = None
    department: str | None = None
    title: str | None = None

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def is_student(self) -> bool:
        return self.role == "student"

    @property
    def is_teacher(self) -> bool:
        return self.role == "teacher"

    @property
    def is_staff(self) -> bool:
        return self.role == "staff"


@dataclass
class Context:
    """Everything a business function needs: storage, who acts, the clock and where the call came from."""

    conn: sqlite3.Connection
    actor: Actor
    clock: Clock
    channel: str = "api"            # agent, mcp, api, eval
    session_id: str | None = None
    runtime_dir: Any = None         # Path where generated files (certificates) are written
    cache: dict = field(default_factory=dict)

    def now(self) -> datetime:
        return self.clock.now()


def load_actor(conn: sqlite3.Connection, person_id: str) -> Actor:
    row = conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
    if row is None:
        raise NotFound(f"personne inconnue : {person_id}")
    return Actor(id=row["id"], role=row["role"], first_name=row["first_name"], last_name=row["last_name"],
                 email=row["email"], group_id=row["group_id"], department=row["department"], title=row["title"])


def fold(text: str) -> str:
    """Lower-case, accent-free text for tolerant name matching."""
    norm = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return " ".join(norm.lower().replace("-", " ").replace("'", " ").split())


def add_working_days(day: date, n: int) -> date:
    current = day
    while n > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            n -= 1
    return current


def person_label(conn: sqlite3.Connection, person_id: str) -> str:
    row = conn.execute("SELECT first_name, last_name FROM people WHERE id = ?", (person_id,)).fetchone()
    return f"{row['first_name']} {row['last_name']}" if row else person_id


def teacher_groups(conn: sqlite3.Connection, teacher_id: str) -> set[str]:
    """Groups a teacher teaches (any section, or the lectures of a course they lead)."""
    rows = conn.execute("""
        SELECT group_id FROM course_sections WHERE teacher_id = ?
        UNION
        SELECT eg.group_id FROM events e JOIN event_groups eg ON eg.event_id = e.id
        WHERE e.organizer_id = ? AND e.course_id IS NOT NULL
    """, (teacher_id, teacher_id)).fetchall()
    return {r[0] for r in rows}


def teacher_course_groups(conn: sqlite3.Connection, teacher_id: str) -> set[tuple[str, str]]:
    """(course, group) pairs a teacher is responsible for: their sections, and every group of a course they lead."""
    rows = conn.execute("""
        SELECT course_id, group_id FROM course_sections WHERE teacher_id = ?
        UNION
        SELECT cs.course_id, cs.group_id FROM course_sections cs
        WHERE cs.course_id IN (SELECT DISTINCT e.course_id FROM events e
                               WHERE e.organizer_id = ? AND e.kind IN ('cours', 'examen') AND e.course_id IS NOT NULL)
    """, (teacher_id, teacher_id)).fetchall()
    return {(r[0], r[1]) for r in rows}
