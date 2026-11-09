"""Audit trail: one line per tool call, whatever the interface, kept in the database."""

from __future__ import annotations

import json
from typing import Any

from ..clock import fmt_dt
from . import Context

# Free-text fields are kept short in the log: enough to investigate, not a second copy of the data.
_MAX_FIELD = 200


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in args.items():
        if isinstance(value, str) and len(value) > _MAX_FIELD:
            value = value[:_MAX_FIELD] + "…"
        out[key] = value
    return out


def log(ctx: Context, tool: str, args: dict[str, Any], outcome: str, detail: str = "") -> None:
    ctx.conn.execute(
        "INSERT INTO audit_log(ts, actor_id, channel, tool, args_json, outcome, detail) VALUES (?,?,?,?,?,?,?)",
        (fmt_dt(ctx.now()), ctx.actor.id, ctx.channel, tool,
         json.dumps(_redact(args), ensure_ascii=False, sort_keys=True), outcome, detail[:500]))
    ctx.conn.commit()


def recent(ctx: Context, limit: int = 50, actor_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM audit_log"
    params: list[Any] = []
    if actor_id:
        sql += " WHERE actor_id = ?"
        params.append(actor_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in ctx.conn.execute(sql, params).fetchall()]
