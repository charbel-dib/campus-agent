"""Delivery of the agent's changes to outside systems (transactional outbox).

The business layer only writes to the school database. This module looks for what changed since the
last pass (new messages, new tickets, events created or modified), records one outbox row per item,
and delivers each row to the configured provider. A failure is kept with its error and retried on the
next pass, up to five attempts, so an SMTP outage never loses a notice or blocks a booking.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..clock import fmt_dt
from ..db import get_meta, set_meta
from . import ical
from .providers import Providers

log = logging.getLogger("campus_agent.outbox")
MAX_ATTEMPTS = 5
EVENT_KINDS = ("rendez_vous", "reservation", "reunion")     # what belongs in a personal calendar


def enqueue(conn, now: datetime) -> int:
    """Record what changed since the last pass. The first pass only sets the starting point."""
    since = get_meta(conn, "outbox_since")
    set_meta(conn, "outbox_since", fmt_dt(now))
    if not since:
        conn.commit()
        return 0
    n = 0
    for kind, sql in (("mail", "SELECT id, created_at AS stamp FROM messages WHERE created_at >= ?"),
                      ("ticket", "SELECT id, created_at AS stamp FROM tickets WHERE created_at >= ?"),
                      ("calendar", "SELECT id, updated_at AS stamp FROM events WHERE updated_at >= ? AND kind IN (%s)"
                       % ",".join(f"'{k}'" for k in EVENT_KINDS))):
        for r in conn.execute(sql, (since,)).fetchall():
            # An event modified again after it was sent goes back in the queue; anything else is queued once.
            cur = conn.execute(
                "INSERT INTO outbox(kind, ref, updated_at) VALUES (?,?,?) ON CONFLICT(kind, ref) DO UPDATE SET "
                "status = 'pending', attempts = 0, updated_at = excluded.updated_at "
                "WHERE outbox.kind = 'calendar' AND excluded.updated_at > outbox.updated_at", (kind, str(r["id"]), r["stamp"]))
            n += cur.rowcount
    conn.commit()
    return n


def _deliver_one(conn, providers: Providers, kind: str, ref: str) -> bool:
    """True when delivered, False when no provider handles this kind (left pending)."""
    if kind == "mail" and providers.mail:
        m = conn.execute("SELECT * FROM messages WHERE id = ?", (ref,)).fetchone()
        for r in conn.execute("SELECT p.email FROM message_recipients mr JOIN people p ON p.id = mr.person_id "
                              "WHERE mr.message_id = ?", (ref,)).fetchall():
            providers.mail.send(r["email"], m["subject"], m["body"])
        return True
    if kind == "ticket" and providers.tickets:
        t = conn.execute("SELECT t.*, p.email FROM tickets t JOIN people p ON p.id = t.person_id WHERE t.id = ?",
                         (ref,)).fetchone()
        providers.tickets.create(t["subject"], t["body"], t["email"], t["category"])
        return True
    if kind == "calendar" and providers.calendar:
        e = conn.execute("SELECT * FROM events WHERE id = ?", (ref,)).fetchone()
        uid = f"event-{ref}"
        if e is None or e["status"] == "cancelled":
            providers.calendar.delete(uid)
        else:
            providers.calendar.put(uid, ical.calendar([ical.event_to_vevent(e)]))
        return True
    return False


def deliver(conn, providers: Providers, now: datetime) -> dict:
    enqueue(conn, now)
    sent = failed = 0
    rows = conn.execute("SELECT * FROM outbox WHERE status != 'sent' AND attempts < ? ORDER BY id",
                        (MAX_ATTEMPTS,)).fetchall()
    for row in rows:
        try:
            if not _deliver_one(conn, providers, row["kind"], row["ref"]):
                continue
            conn.execute("UPDATE outbox SET status = 'sent', attempts = attempts + 1, last_error = '' WHERE id = ?",
                         (row["id"],))
            sent += 1
        except Exception as exc:  # noqa: BLE001 - any provider failure is kept and retried
            log.warning("outbox %s %s failed: %s", row["kind"], row["ref"], exc)
            conn.execute("UPDATE outbox SET status = 'failed', attempts = attempts + 1, last_error = ? WHERE id = ?",
                         (str(exc)[:300], row["id"]))
            failed += 1
        conn.commit()
    return {"sent": sent, "failed": failed}
