"""Scenario evaluation of the campus agent with a real model.

Each scenario runs on a fresh copy of the fictional school, plays the user's messages, answers
the confirmation slips as the scenario says, then checks what happened: tools called, actions
proposed, the database afterwards, and words in the reply. No model grades another model.

    python eval/run_agent_eval.py                                # Claude Haiku 5.5, every scenario
    python eval/run_agent_eval.py --model claude-sonnet-5-5
    python eval/run_agent_eval.py --only plan-move-session,injection-role-claim --repeat 3

Results: eval/results/agent-<model>.json (with transcripts) and agent-<model>.md.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from campus_agent import db, tools  # noqa: E402
from campus_agent.agent import Agent  # noqa: E402
from campus_agent.clock import Clock, parse_dt  # noqa: E402
from campus_agent.config import load_settings  # noqa: E402
from campus_agent.domain import Context, fold, load_actor  # noqa: E402
from campus_agent.domain.documents import DocumentIndex  # noqa: E402
from campus_agent.llm import ClaudeClient, Usage  # noqa: E402
from campus_agent.seed import create_database  # noqa: E402

SCENARIOS = ROOT / "eval" / "agent_scenarios.json"
RESULTS = ROOT / "eval" / "results"
MAX_DECISIONS = 6
OPS = {"==": lambda a, b: a == b, ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b}


# ---------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------

def evaluate(check: dict, run: dict, conn) -> tuple[bool, str]:
    kind = check["type"]
    calls = run["tool_calls"]
    reply = fold(" ".join(run["replies"])).replace("’", "'")
    if kind == "tool_called":
        ok = any(c["name"] == check["name"] for c in calls)
        return ok, f"{check['name']} {'appelé' if ok else 'non appelé'}"
    if kind == "tool_not_called":
        ok = not any(c["name"] == check["name"] for c in calls)
        return ok, f"{check['name']} {'non appelé' if ok else 'appelé'}"
    if kind == "no_successful_tool":
        bad = [c for c in calls if c["name"] == check["name"] and c["ok"]
               and all(c["input"].get(k) == v for k, v in check.get("args", {}).items())]
        return not bad, "aucun appel réussi" if not bad else f"appel réussi : {bad[0]['input']}"
    if kind == "pending_tool":
        ok = any(p["tool"] == check["name"] for p in run["proposed"])
        return ok, f"action {check['name']} {'proposée' if ok else 'non proposée'}"
    if kind == "no_pending":
        ok = not run["proposed"]
        return ok, "aucune action proposée" if ok else f"actions proposées : {[p['tool'] for p in run['proposed']]}"
    if kind == "reply_contains_any":
        values = [fold(v) if v != "?" else "?" for v in check["values"]]
        ok = any(v in reply for v in values)
        return ok, f"réponse contient l'un de {check['values']}" if ok else f"absent de la réponse : {check['values']}"
    if kind == "reply_not_contains":
        found = [v for v in check["values"] if fold(v) in reply or v in " ".join(run["replies"])]
        return not found, "ok" if not found else f"présent dans la réponse : {found}"
    if kind == "sql":
        value = conn.execute(check["query"]).fetchone()[0]
        ok = OPS[check["op"]](value, check["value"])
        return ok, f"SQL = {value} (attendu {check['op']} {check['value']})"
    raise ValueError(f"unknown check {kind}")


# ---------------------------------------------------------------------------------------
# One scenario
# ---------------------------------------------------------------------------------------

def run_scenario(scenario: dict, template: Path, now: str, llm_factory: Callable, workdir: Path) -> dict:
    path = workdir / f"{scenario['id']}-{time.monotonic_ns()}.db"
    shutil.copy(template, path)
    conn = db.connect(path)
    clock = Clock(now)
    try:
        for op in scenario.get("setup", []):
            ctx = Context(conn, load_actor(conn, op["as"]), clock, channel="eval-setup", runtime_dir=workdir)
            res = tools.execute(ctx, op["tool"], op["args"], confirm_writes=False)
            if res.is_error:
                raise RuntimeError(f"setup failed for {scenario['id']}: {res.content}")
        llm = llm_factory()
        agent = Agent(conn, clock, llm, channel="eval", runtime_dir=workdir, session_budget_usd=1.0,
                      daily_budget_usd=1000.0)
        sid = agent.new_session(scenario["persona"])
        run = {"replies": [], "tool_calls": [], "proposed": [], "decisions": [], "stopped": [], "usage": Usage()}
        started = time.monotonic()

        def absorb(turn):
            run["replies"].append(turn.reply)
            run["tool_calls"] += [s for s in turn.trace if s["type"] == "tool"]
            run["proposed"] += turn.pending_actions
            run["usage"].merge(turn.usage)
            if turn.stopped:
                run["stopped"].append(turn.stopped)
            return turn

        transcript = []
        for text in scenario["turns"]:
            turn = absorb(agent.run(sid, scenario["persona"], text))
            transcript.append({"user": text, "assistant": turn.reply, "trace": turn.trace})
            queue = list(turn.pending_actions)
            decisions = 0
            while queue and decisions < MAX_DECISIONS:
                action = queue.pop(0)
                confirm = scenario.get("confirm", "none") == "all"
                turn = absorb(agent.decide(sid, scenario["persona"], action["id"], confirm))
                decisions += 1
                run["decisions"].append({"action": action["summary"], "confirmed": confirm,
                                         "ok": (turn.decision or {}).get("ok")})
                transcript.append({"decision": action["summary"], "confirmed": confirm,
                                   "assistant": turn.reply, "trace": turn.trace})
                queue += turn.pending_actions
        elapsed = time.monotonic() - started

        results = []
        for check in scenario["checks"]:
            ok, detail = evaluate(check, run, conn)
            results.append({"check": check["type"], "ok": ok, "detail": detail})
        usage = run["usage"].as_dict()
        return {"id": scenario["id"], "category": scenario["category"], "persona": scenario["persona"],
                "passed": all(r["ok"] for r in results), "checks": results,
                "tools": [c["name"] for c in run["tool_calls"]], "proposed": [p["summary"] for p in run["proposed"]],
                "stopped": run["stopped"], "usage": usage, "seconds": round(elapsed, 2), "transcript": transcript}
    except Exception as exc:  # noqa: BLE001 - a crashed scenario is a failed scenario, reported as such
        return {"id": scenario["id"], "category": scenario["category"], "persona": scenario["persona"],
                "passed": False, "checks": [], "error": f"{type(exc).__name__}: {exc}", "tools": [], "proposed": [],
                "stopped": [], "usage": Usage().as_dict(), "seconds": 0.0, "transcript": []}
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------
# Whole run and report
# ---------------------------------------------------------------------------------------

def run_all(scenarios: list[dict], now: str, seed: int, llm_factory: Callable, repeat: int = 1,
            workers: int = 4) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        template = tmpdir / "template.db"
        create_database(template, parse_dt(now), seed)
        jobs = [s for s in scenarios for _ in range(repeat)]
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            return list(pool.map(lambda s: run_scenario(s, template, now, llm_factory, tmpdir), jobs))


def summarise(results: list[dict]) -> dict:
    n = len(results)
    cost = sum(r["usage"]["cost_usd"] for r in results)
    by_cat: dict[str, list] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r["passed"])
    return {"runs": n, "passed": sum(r["passed"] for r in results),
            "pass_rate": round(sum(r["passed"] for r in results) / n, 3) if n else 0.0,
            "total_cost_usd": round(cost, 4), "mean_cost_usd": round(cost / n, 5) if n else 0.0,
            "mean_seconds": round(sum(r["seconds"] for r in results) / n, 1) if n else 0.0,
            "mean_model_calls": round(sum(r["usage"]["calls"] for r in results) / n, 1) if n else 0.0,
            "by_category": {k: {"runs": len(v), "passed": sum(v)} for k, v in sorted(by_cat.items())}}


def markdown(model: str, summary: dict, results: list[dict], now: str) -> str:
    lines = [f"## agent evaluation — {model}", "",
             f"{summary['runs']} runs on the fictional school (clock pinned at {now}). "
             f"Passed: **{summary['passed']}/{summary['runs']}** ({summary['pass_rate'] * 100:.0f} %). "
             f"Total cost {summary['total_cost_usd']:.4f} $, mean {summary['mean_cost_usd'] * 100:.3f} cents and "
             f"{summary['mean_seconds']} s per scenario, {summary['mean_model_calls']} model calls on average.", "",
             "| Category | Runs | Passed |", "|---|---:|---:|"]
    for cat, v in summary["by_category"].items():
        lines.append(f"| {cat} | {v['runs']} | {v['passed']} |")
    lines += ["", "| Scenario | Result | Tools | Cost (cents) | Seconds |", "|---|---|---|---:|---:|"]
    for r in results:
        tools_used = ", ".join(dict.fromkeys(r["tools"])) or "—"
        lines.append(f"| {r['id']} | {'pass' if r['passed'] else '**fail**'} | {tools_used} | "
                     f"{r['usage']['cost_usd'] * 100:.3f} | {r['seconds']} |")
    failures = [r for r in results if not r["passed"]]
    if failures:
        lines += ["", "### Failures", ""]
        for r in failures:
            if r.get("error"):
                lines.append(f"- **{r['id']}**: {r['error']}")
                continue
            if "error" in r.get("stopped", []):
                lines.append(f"- **{r['id']}**: the model API returned an error (see the JSON transcript)")
            for c in r["checks"]:
                if not c["ok"]:
                    lines.append(f"- **{r['id']}** ({c['check']}): {c['detail']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model")
    parser.add_argument("--only", help="comma-separated scenario ids")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--effort", default=None, help="low, medium or high (default: the model's)")
    parser.add_argument("--batch", help="run part N of K, e.g. 1/3 (long runs split across calls)")
    parser.add_argument("--merge", action="store_true", help="combine the saved batches into one report")
    args = parser.parse_args(argv)

    settings = load_settings()
    model = args.model or settings.model
    spec = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    scenarios = spec["scenarios"]
    stem = f"agent-{model}" + (f"-{args.effort}" if args.effort else "")
    if args.merge:
        parts = sorted(RESULTS.glob(f"{stem}-batch*.json"))
        results = [r for p in parts for r in json.loads(p.read_text(encoding="utf-8"))["results"]]
        order = {s["id"]: i for i, s in enumerate(scenarios)}
        results.sort(key=lambda r: order.get(r["id"], 999))
        _write(stem, model, args.effort, results, spec["now"])
        for p in parts:
            p.unlink()
            p.with_suffix(".md").unlink(missing_ok=True)
        return
    if args.only:
        wanted = set(args.only.split(","))
        scenarios = [s for s in scenarios if s["id"] in wanted]
        stem += "-partial"
    if args.batch:
        n, k = (int(x) for x in args.batch.split("/"))
        scenarios = scenarios[n - 1::k]
        stem += f"-batch{n}of{k}"
    tools.set_document_index(DocumentIndex(settings.docs_dir, settings.docs_retriever, settings.emb_model))

    def factory():
        return ClaudeClient(api_key=settings.anthropic_api_key, model=model, timeout=settings.api_timeout_seconds,
                            effort=args.effort or settings.effort or None)

    if not settings.anthropic_api_key:
        sys.exit("ANTHROPIC_API_KEY is missing: add it to .env")
    print(f"{len(scenarios)} scenario(s) × {args.repeat} with {model}…", flush=True)
    results = run_all(scenarios, spec["now"], spec["seed"], factory, args.repeat, args.workers)
    _write(stem, model, args.effort, results, spec["now"])


def _write(stem: str, model: str, effort: str | None, results: list[dict], now: str) -> None:
    summary = summarise(results)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{stem}.json").write_text(json.dumps({"model": model, "effort": effort, "summary": summary,
                                                       "results": results}, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    report = markdown(model + (f" (effort {effort})" if effort else ""), summary, results, now)
    (RESULTS / f"{stem}.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
