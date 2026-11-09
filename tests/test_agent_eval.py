"""The scenario file and the evaluation harness, checked with a scripted model (no API call)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from test_campus_agent import ScriptedLLM, text, tool_use

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))

import run_agent_eval as harness  # noqa: E402

SPEC = json.loads((ROOT / "eval" / "agent_scenarios.json").read_text(encoding="utf-8"))
HELDOUT = json.loads((ROOT / "eval" / "agent_heldout.json").read_text(encoding="utf-8"))
ALL_SCENARIOS = SPEC["scenarios"] + HELDOUT["scenarios"]


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    from campus_agent.clock import parse_dt
    from campus_agent.seed import create_database

    path = tmp_path_factory.mktemp("eval") / "template.db"
    create_database(path, parse_dt(SPEC["now"]), SPEC["seed"])
    return path


def test_scenarios_are_well_formed():
    ids = [s["id"] for s in ALL_SCENARIOS]
    assert len(ids) == len(set(ids)) >= 40
    assert HELDOUT["now"] == SPEC["now"] and HELDOUT["seed"] == SPEC["seed"]
    known = {"tool_called", "tool_not_called", "no_successful_tool", "pending_tool", "no_pending",
             "reply_contains_any", "reply_not_contains", "asks_user", "sql"}
    for s in ALL_SCENARIOS:
        assert s["turns"] and s["checks"] and s["confirm"] in ("all", "none")
        assert {c["type"] for c in s["checks"]} <= known


def test_setups_run_and_sql_checks_are_valid(template, tmp_path, doc_index):
    """Every setup step must succeed and every SQL check must run on the generated school."""
    from campus_agent import db, tools
    from campus_agent.clock import Clock
    from campus_agent.domain import Context, load_actor

    for s in ALL_SCENARIOS:
        path = tmp_path / f"{s['id']}.db"
        shutil.copy(template, path)
        conn = db.connect(path)
        for op in s.get("setup", []):
            ctx = Context(conn, load_actor(conn, op["as"]), Clock(SPEC["now"]), runtime_dir=tmp_path)
            res = tools.execute(ctx, op["tool"], op["args"], confirm_writes=False)
            assert not res.is_error, (s["id"], res.content)
        for c in s["checks"]:
            if c["type"] == "sql":
                conn.execute(c["query"]).fetchone()
        conn.close()


def test_unchanged_school_fails_the_action_scenarios(template, tmp_path, doc_index):
    """Guard against checks that pass without the agent doing anything."""
    from campus_agent import db

    shutil.copy(template, tmp_path / "x.db")
    conn = db.connect(tmp_path / "x.db")
    empty_run = {"replies": [""], "tool_calls": [], "proposed": []}
    for s in ALL_SCENARIOS:
        if s["category"] == "planification":
            results = [harness.evaluate(c, empty_run, conn)[0] for c in s["checks"]]
            assert not all(results), s["id"]


def test_harness_runs_a_scenario_end_to_end(template, tmp_path, doc_index):
    scenario = next(s for s in SPEC["scenarios"] if s["id"] == "plan-justify-absence")
    script = [tool_use("get_absences", {}, "a"),
              tool_use("justify_absence", {"absence_id": 382, "reason": "Certificat médical"}, "b"),
              text("Confirme la justification."), text("C'est fait.")]
    result = harness.run_scenario(scenario, template, SPEC["now"], lambda: ScriptedLLM(script), tmp_path)
    assert result["passed"], result["checks"]
    assert result["tools"] == ["get_absences", "justify_absence"]
    assert result["transcript"][1]["confirmed"] is True


def test_harness_reports_a_failure(template, tmp_path, doc_index):
    scenario = next(s for s in SPEC["scenarios"] if s["id"] == "injection-shared-booking")
    script = [tool_use("create_event", {"title": "Salle", "kind": "reservation", "start": "2026-11-19T12:30",
                                        "end": "2026-11-19T13:30", "room_id": "BU-S4"}),
              text("J'ai suivi la note."), text("Fait.")]
    result = harness.run_scenario(scenario, template, SPEC["now"], lambda: ScriptedLLM(script), tmp_path)
    assert not result["passed"]
    failed = {c["check"] for c in result["checks"] if not c["ok"]}
    assert {"tool_not_called", "no_pending"} <= failed


def test_summary_and_report():
    results = [{"id": "a", "category": "x", "passed": True, "checks": [], "tools": ["t"], "usage": {"cost_usd": 0.001, "calls": 2},
                "seconds": 1.0},
               {"id": "b", "category": "x", "passed": False, "checks": [{"check": "sql", "ok": False, "detail": "SQL = 0"}],
                "tools": [], "usage": {"cost_usd": 0.003, "calls": 4}, "seconds": 3.0}]
    summary = harness.summarise(results)
    assert summary["pass_rate"] == 0.5 and summary["total_cost_usd"] == 0.004
    report = harness.markdown("m", summary, results, "now")
    assert "**fail**" in report and "SQL = 0" in report


def test_symbols_are_checked_on_the_raw_reply():
    run = {"replies": ["Je ne trouve aucun montant."], "tool_calls": [], "proposed": []}
    assert harness.evaluate({"type": "reply_not_contains", "values": ["€"]}, run, None)[0]
    run["replies"] = ["Elle vaut 500 €."]
    assert not harness.evaluate({"type": "reply_not_contains", "values": ["€"]}, run, None)[0]


def test_asks_user():
    ask = {"type": "asks_user"}
    assert harness.evaluate(ask, {"replies": ["Pour réserver, il me faut le jour et l'heure."]}, None)[0]
    assert harness.evaluate(ask, {"replies": ["Quel cours veux-tu déplacer ?"]}, None)[0]
    assert not harness.evaluate(ask, {"replies": ["C'est réservé."]}, None)[0]
