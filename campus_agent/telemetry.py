"""Traces of the agent, named after the OpenTelemetry semantic conventions for generative AI.

One trace per turn: a root span ``invoke_agent campus-agent``, a child ``chat {model}`` per model
call and a child ``execute_tool {name}`` per tool call, with the attributes of the conventions
(``gen_ai.operation.name``, ``gen_ai.request.model``, ``gen_ai.usage.input_tokens``,
``gen_ai.tool.name``, ``error.type``…). Those conventions are still in "Development" status
(version 1.37 was followed), so the names are kept in this one file.

Spans are always written to the ``spans`` table, which feeds the operations page. When
``OTEL_EXPORTER_OTLP_ENDPOINT`` is set they are also sent as OTLP/HTTP JSON to any collector
(Arize Phoenix, Langfuse, Jaeger, Grafana Tempo). This uses the wire format directly instead of the
OpenTelemetry SDK, which keeps the core free of dependencies; swapping in the SDK means replacing
``Tracer`` only.

Privacy: spans carry identifiers, token counts, tool names and outcomes. They never carry the text
of a prompt, a reply or a tool argument.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time

log = logging.getLogger("campus_agent.telemetry")


class Span:
    def __init__(self, name: str, trace_id: str, parent_id: str | None, attributes: dict):
        self.name, self.trace_id, self.parent_id = name, trace_id, parent_id
        self.span_id = secrets.token_hex(8)
        self.attributes = dict(attributes)
        self.start_ns = time.time_ns()
        self.end_ns: int | None = None
        self.status = "ok"

    def set(self, key: str, value) -> None:
        self.attributes[key] = value


class Tracer:
    def __init__(self, conn, session_id: str | None, actor_id: str | None, otlp_endpoint: str = ""):
        self.conn, self.session_id, self.actor_id = conn, session_id, actor_id
        self.otlp_endpoint = otlp_endpoint.rstrip("/")
        self.trace_id = secrets.token_hex(16)
        self.spans: list[Span] = []

    def start(self, name: str, parent: Span | None = None, **attributes) -> Span:
        span = Span(name, self.trace_id, parent.span_id if parent else None, attributes)
        self.spans.append(span)
        return span

    @staticmethod
    def end(span: Span, status: str = "ok") -> None:
        span.end_ns = time.time_ns()
        span.status = status

    def flush(self) -> None:
        """Store the finished spans, then export them without making the user wait."""
        done = [s for s in self.spans if s.end_ns is not None]
        self.spans = []
        try:
            self.conn.executemany(
                "INSERT INTO spans(trace_id, span_id, parent_id, name, session_id, actor_id, start_ns, duration_ms, "
                "status, attributes_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(s.trace_id, s.span_id, s.parent_id, s.name, self.session_id, self.actor_id, s.start_ns,
                  (s.end_ns - s.start_ns) / 1e6, s.status, json.dumps(s.attributes, ensure_ascii=False)) for s in done])
            self.conn.commit()
        except Exception:  # noqa: BLE001 - telemetry never breaks a turn
            log.exception("could not store spans")
        if self.otlp_endpoint and done:
            threading.Thread(target=_export, args=(self.otlp_endpoint, otlp_payload(done)), daemon=True).start()


def _value(v) -> dict:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [_value(x) for x in v]}}
    return {"stringValue": str(v)}


def otlp_payload(spans: list[Span], service: str = "campus-agent") -> dict:
    """The OTLP/HTTP JSON body for ``POST {endpoint}/v1/traces``."""
    return {"resourceSpans": [{
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service}}]},
        "scopeSpans": [{"scope": {"name": "campus_agent.telemetry"}, "spans": [{
            "traceId": s.trace_id, "spanId": s.span_id, **({"parentSpanId": s.parent_id} if s.parent_id else {}),
            "name": s.name, "kind": 3 if s.name.startswith("chat") else 1,       # CLIENT for model calls, else INTERNAL
            "startTimeUnixNano": str(s.start_ns), "endTimeUnixNano": str(s.end_ns),
            "attributes": [{"key": k, "value": _value(v)} for k, v in s.attributes.items()],
            "status": {"code": 2 if s.status == "error" else 1},
        } for s in spans]}]}]}


def _export(endpoint: str, payload: dict) -> None:
    try:
        import requests

        requests.post(endpoint + "/v1/traces", json=payload, timeout=5).raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log.warning("OTLP export failed: %s", exc)


def trace(conn, trace_id: str, actor_id: str | None = None) -> list[dict]:
    """The spans of one trace, in order, for the "behind the scenes" panel."""
    sql, params = "SELECT * FROM spans WHERE trace_id = ?", [trace_id]
    if actor_id:
        sql += " AND actor_id = ?"
        params.append(actor_id)
    return [{"span_id": r["span_id"], "parent_id": r["parent_id"], "name": r["name"], "status": r["status"],
             "duration_ms": round(r["duration_ms"], 1), "attributes": json.loads(r["attributes_json"])}
            for r in conn.execute(sql + " ORDER BY start_ns, id", params)]
