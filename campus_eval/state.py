"""Snapshots of the school database and the changes between two of them.

A task states which changes it expects (``{"events": {"added": 1}, "messages": "any"}``). Every other
watched table must be unchanged: an agent that does the task *and* something else fails, as in
tau-bench where the whole end state is compared.
"""

from __future__ import annotations

import sqlite3

# Tables that hold the school's state. Bookkeeping tables (audit log, sessions, pending actions) are
# not part of the outcome; join tables follow their parent (event_people and event_groups with events,
# message_recipients with messages).
WATCHED = {
    "events": "id",
    "absences": "id",
    "grades": "id",
    "messages": "id",
    "tickets": "id",
    "certificates": "id",
}
FOLLOWERS = {"event_people": "events", "event_groups": "events", "message_recipients": "messages"}


def snapshot(conn: sqlite3.Connection) -> dict[str, dict]:
    snap: dict[str, dict] = {}
    for table, pk in WATCHED.items():
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        snap[table] = {r[pk]: tuple(r) for r in rows}
    for table, parent in FOLLOWERS.items():
        key_cols = {"event_people": ("event_id", "person_id"), "event_groups": ("event_id", "group_id"),
                    "message_recipients": ("message_id", "person_id")}[table]
        rows = conn.execute(f"SELECT {', '.join(key_cols)} FROM {table}").fetchall()
        snap[table] = {tuple(r): tuple(r) for r in rows}
    return snap


def diff(before: dict, after: dict) -> dict[str, dict[str, int]]:
    out = {}
    for table in before:
        b, a = before[table], after[table]
        added = len(a.keys() - b.keys())
        removed = len(b.keys() - a.keys())
        modified = sum(1 for k in a.keys() & b.keys() if a[k] != b[k])
        if added or removed or modified:
            out[table] = {"added": added, "removed": removed, "modified": modified}
    return out


def _matches(value: int, spec) -> bool:
    if spec == "any":
        return True
    if isinstance(spec, int):
        return value == spec
    if isinstance(spec, str) and spec.startswith(">="):
        return value >= int(spec[2:])
    if isinstance(spec, str) and spec.startswith("<="):
        return value <= int(spec[2:])
    raise ValueError(f"bad state spec {spec!r}")


def compare(changes: dict, expect: dict) -> tuple[bool, list[str]]:
    """Check observed changes against the expectation. Returns (ok, problems)."""
    problems = []
    for table in list(WATCHED) + list(FOLLOWERS):
        observed = changes.get(table, {"added": 0, "removed": 0, "modified": 0})
        if table in FOLLOWERS:
            parent = FOLLOWERS[table]
            if table not in expect:
                # follows its parent: allowed to change only if the parent may change
                parent_spec = expect.get(parent)
                if parent_spec is not None or not any(observed.values()):
                    continue
                problems.append(f"{table} changé sans changement attendu de {parent}")
                continue
        spec = expect.get(table)
        if spec == "any":
            continue
        spec = spec or {}
        for kind in ("added", "removed", "modified"):
            if not _matches(observed[kind], spec.get(kind, 0)):
                problems.append(f"{table}.{kind} = {observed[kind]} (attendu {spec.get(kind, 0)})")
    return (not problems), problems
