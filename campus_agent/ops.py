"""Operations: what the school office looks at to run the assistant day to day.

``metrics`` aggregates the tables the agent already fills (sessions, spans, audit log, pending
actions, feedback, tickets) into the figures of the operations page: usage, cost, latency, how often
users confirm what the agent proposes, refusals, and the requests the agent could not serve.
"""

from __future__ import annotations

import json
from collections import Counter


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 2)


def metrics(conn, days: int = 14) -> dict:
    sessions = conn.execute(
        "SELECT substr(s.created_at, 1, 10) AS day, COUNT(*) AS n, COALESCE(SUM(s.cost_usd), 0) AS cost "
        "FROM agent_sessions s WHERE s.messages_json != '[]' GROUP BY day ORDER BY day DESC LIMIT ?", (days,)).fetchall()
    by_role = conn.execute(
        "SELECT p.role, COUNT(DISTINCT s.actor_id) AS users, COUNT(*) AS conversations FROM agent_sessions s "
        "JOIN people p ON p.id = s.actor_id WHERE s.messages_json != '[]' GROUP BY p.role").fetchall()
    turns = conn.execute("SELECT duration_ms, status, attributes_json FROM spans WHERE parent_id IS NULL").fetchall()
    durations = [t["duration_ms"] / 1000 for t in turns]
    attrs = [json.loads(t["attributes_json"]) for t in turns]
    cost = sum(a.get("campus.cost_usd", 0) for a in attrs)
    tools_used: Counter = Counter()
    outcomes: Counter = Counter()
    for r in conn.execute("SELECT tool, outcome, COUNT(*) AS n FROM audit_log GROUP BY tool, outcome"):
        if r["outcome"] in ("ok", "pending"):
            tools_used[r["tool"]] += r["n"]
        outcomes[r["outcome"]] += r["n"]
    actions = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM pending_actions GROUP BY status")}
    decided = sum(actions.get(k, 0) for k in ("executed", "cancelled", "failed"))
    fb = {r["rating"]: r["n"] for r in conn.execute("SELECT rating, COUNT(*) AS n FROM feedback GROUP BY rating")}
    calls = sum(outcomes.values())
    return {
        "conversations_by_day": [{"day": r["day"], "conversations": r["n"], "cost_usd": round(r["cost"], 4)} for r in sessions],
        "users_by_role": [dict(r) for r in by_role],
        "turns": {"count": len(turns), "failed": sum(t["status"] == "error" for t in turns),
                  "latency_p50_s": _percentile(durations, 0.5), "latency_p95_s": _percentile(durations, 0.95),
                  "mean_cost_cents": round(100 * cost / len(turns), 4) if turns else None,
                  "escalated": sum(1 for a in attrs if a.get("campus.escalated_to"))},
        "tools": [{"tool": t, "calls": n} for t, n in tools_used.most_common(12)],
        "tool_calls": {"total": calls, "denied": outcomes.get("denied", 0), "conflict": outcomes.get("conflict", 0),
                       "invalid": outcomes.get("invalid", 0), "error": outcomes.get("error", 0),
                       "error_rate": round(outcomes.get("error", 0) / calls, 4) if calls else None},
        "actions": {**actions, "confirmation_rate": round(actions.get("executed", 0) / decided, 3) if decided else None},
        "feedback": {"up": fb.get(1, 0), "down": fb.get(-1, 0),
                     "last_down": [dict(r) for r in conn.execute(
                         "SELECT created_at, reason, trace_id FROM feedback WHERE rating = -1 ORDER BY id DESC LIMIT 10")]},
        # What the assistant could not do: hand-offs to a human and tickets about the assistant itself.
        "unmet": [dict(r) for r in conn.execute(
            "SELECT created_at, subject FROM tickets WHERE category = 'assistant' ORDER BY id DESC LIMIT 15")],
    }
