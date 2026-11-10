"""Personal data: export, erasure and retention (GDPR articles 15, 17 and 5.1.e).

The school's records (grades, absences, timetable) belong to the school's own systems and keep their
own rules. What the assistant adds, and therefore what it must handle, is: conversations, traces,
feedback, pending actions and the audit trail.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from .clock import fmt_dt


def export_person(conn, person_id: str) -> dict:
    """Everything the assistant holds about one person, as plain data."""
    def rows(sql: str) -> list[dict]:
        return [dict(r) for r in conn.execute(sql, (person_id,))]

    sessions = []
    for r in conn.execute("SELECT id, created_at, updated_at, model, cost_usd, messages_json FROM agent_sessions "
                          "WHERE actor_id = ?", (person_id,)):
        texts = [{"role": m["role"], "text": m["content"]} for m in json.loads(r["messages_json"])
                 if isinstance(m["content"], str)]
        sessions.append({"id": r["id"], "created_at": r["created_at"], "updated_at": r["updated_at"],
                         "model": r["model"], "cost_usd": r["cost_usd"], "my_messages": texts})
    return {
        "person": rows("SELECT id, role, first_name, last_name, email, group_id FROM people WHERE id = ?"),
        "conversations": sessions,
        "feedback": rows("SELECT created_at, rating, reason FROM feedback WHERE actor_id = ?"),
        "pending_actions": rows("SELECT id, tool, summary, status, created_at, decided_at FROM pending_actions WHERE actor_id = ?"),
        "tickets": rows("SELECT id, category, subject, body, status, created_at FROM tickets WHERE person_id = ?"),
        "audit_trail": rows("SELECT ts, channel, tool, outcome FROM audit_log WHERE actor_id = ? ORDER BY id"),
        "traces": rows("SELECT trace_id, name, start_ns, duration_ms, status FROM spans WHERE actor_id = ? ORDER BY id"),
    }


def erase_person(conn, person_id: str) -> dict:
    """Erase what the assistant holds about a person. The audit trail keeps who did what and when,
    which the school needs to answer for its decisions, but loses the free text of the arguments."""
    counts = {}
    for table, column in (("agent_sessions", "actor_id"), ("feedback", "actor_id"), ("spans", "actor_id")):
        counts[table] = conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (person_id,)).rowcount  # noqa: S608 - fixed names
    counts["pending_actions"] = conn.execute(
        "DELETE FROM pending_actions WHERE actor_id = ? AND status != 'executed'", (person_id,)).rowcount
    counts["audit_redacted"] = conn.execute(
        "UPDATE audit_log SET args_json = '{}', detail = '' WHERE actor_id = ?", (person_id,)).rowcount
    conn.commit()
    return counts


def purge(conn, now: datetime, retention_days: int = 30, audit_retention_days: int = 365) -> dict:
    """Retention: drop conversations, traces and feedback after ``retention_days``, the audit trail after a year."""
    limit = fmt_dt(now - timedelta(days=retention_days))
    limit_ns = int((now - timedelta(days=retention_days)).timestamp() * 1e9)
    counts = {
        "agent_sessions": conn.execute("DELETE FROM agent_sessions WHERE updated_at < ?", (limit,)).rowcount,
        "feedback": conn.execute("DELETE FROM feedback WHERE created_at < ?", (limit,)).rowcount,
        "spans": conn.execute("DELETE FROM spans WHERE start_ns < ?", (limit_ns,)).rowcount,
        "pending_actions": conn.execute("DELETE FROM pending_actions WHERE created_at < ? AND status != 'pending'",
                                        (limit,)).rowcount,
        "audit_log": conn.execute("DELETE FROM audit_log WHERE ts < ?",
                                  (fmt_dt(now - timedelta(days=audit_retention_days)),)).rowcount,
    }
    conn.commit()
    return counts
