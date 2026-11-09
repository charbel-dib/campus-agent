"""The MCP server, end to end: a real MCP client launches it over stdio and calls tools."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from campus_agent.mcp_server import tool_listing

ROOT = Path(__file__).resolve().parents[1]


def test_listing_marks_writes_as_destructive():
    listing = {d["name"]: d for d in tool_listing("student")}
    assert "review_absence" not in listing
    assert listing["get_schedule"]["annotations"]["read_only_hint"] is True
    assert listing["create_event"]["annotations"]["destructive_hint"] is True
    assert "accord explicite" in listing["create_event"]["description"]


def test_stdio_round_trip(tmp_path):
    pytest.importorskip("mcp")
    import anyio
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = {"CAMPUS_RUNTIME_DIR": str(tmp_path), "CAMPUS_NOW": "2026-11-16T09:00", "PYTHONPATH": str(ROOT)}
    params = StdioServerParameters(command=sys.executable, args=["-m", "campus_agent.mcp_server", "--as", "stu-0001"],
                                   env=env, cwd=str(ROOT))

    async def scenario():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert "Ker Avel" in (init.instructions or "")
                names = {t.name for t in (await session.list_tools()).tools}
                assert {"get_schedule", "find_free_slots", "justify_absence"} <= names
                assert "review_absence" not in names

                res = await session.call_tool("get_absences", {})
                body = json.loads(res.content[0].text)
                assert not res.is_error and body["by_status"]["non_justifiee"] >= 1

                denied = await session.call_tool("review_absence", {"absence_id": 1, "decision": "justifiee"})
                assert denied.is_error and json.loads(denied.content[0].text)["code"] == "denied"

                ticket = await session.call_tool("create_ticket", {"category": "salles", "subject": "Lampe",
                                                                   "body": "La lampe de BU-S2 clignote."})
                assert not ticket.is_error and json.loads(ticket.content[0].text)["ticket_id"]

    anyio.run(scenario)
    conn = sqlite3.connect(tmp_path / "campus.db")
    rows = conn.execute("SELECT tool, outcome, channel FROM audit_log ORDER BY id").fetchall()
    assert rows == [("get_absences", "ok", "mcp"), ("review_absence", "denied", "mcp"),
                    ("create_ticket", "executed", "mcp")]
