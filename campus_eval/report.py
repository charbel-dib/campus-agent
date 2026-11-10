"""Markdown reports from merged results."""

from __future__ import annotations

from .suites import ORDER

SUITE_TITLES = {
    "tasks": "Realistic tasks with a simulated user (tau-bench)",
    "tool_calls": "Tool choice and arguments (BFCL)",
    "traps": "Milestones and minefields (ToolSandbox)",
    "injection": "Indirect prompt injection (AgentDojo)",
    "access": "Access control with canaries (OWASP LLM02/LLM06)",
    "grounding": "Hallucinations and grounding",
    "robustness": "Robustness to wording",
    "core": "Regression: the 41 scenarios of version 1.0",
}


def _pct(x) -> str:
    return "—" if x is None else f"{100 * x:.0f} %"


def _headline(suite: str, m: dict) -> str:
    k = m.get("trials", 1)
    base = f"pass^1 {_pct(m.get('pass^1'))}"
    if k > 1:
        base += f", pass^{k} {_pct(m.get(f'pass^{k}'))}"
    extra = {
        "tool_calls": lambda: ", ".join(f"{c} {_pct(v)}" for c, v in m.get("accuracy_by_category", {}).items()),
        "traps": lambda: f"minefields hit {_pct(m.get('minefield_hit_rate'))}",
        "injection": lambda: (f"utility {_pct(m.get('utility_without_attack'))} clean, "
                              f"{_pct(m.get('utility_under_attack'))} under attack; "
                              f"attack success {_pct(m.get('attack_success_rate'))}"),
        "access": lambda: f"leaks {_pct(m.get('leak_rate'))}; positive controls {_pct(m.get('positive_controls_ok'))}",
        "grounding": lambda: ", ".join(f"{c} {_pct(v)}" for c, v in m.get("accuracy_by_category", {}).items()),
        "robustness": lambda: ", ".join(f"{c} {_pct(v)}" for c, v in m.get("accuracy_by_variant", {}).items()),
        "tasks": lambda: f"{m.get('mean_user_turns')} user turns on average",
    }.get(suite)
    return base + (f"; {extra()}" if extra else "")


def model_report(model: str, summary: dict, results: list[dict], meta: dict) -> str:
    g = summary["global"]
    lines = [f"# Evaluation — {model}", "",
             f"Campus agent {meta.get('version')}, prompt `{meta.get('prompt_hash')}`, clock pinned at {meta.get('now')}, "
             f"{meta.get('k')} trial(s) per task, simulated user `{meta.get('sim_model')}`.", "",
             f"**{g['runs']} runs, {_pct(g['pass_rate'])} passed.** Agent cost ${g['agent_cost_usd']:.2f} "
             f"({g['mean_cost_cents']:.3f} cent per run), latency p50 {g['latency_p50_s']} s and p95 {g['latency_p95_s']} s. "
             f"Unsupported claims: {g['unsupported_claims']} of {g['checkable_claims']} checkable facts "
             f"({_pct(g['unsupported_claim_rate'])}), malformed tool calls {_pct(g['malformed_tool_call_rate'])}.", "",
             "| Suite | Tasks | Runs | Result |", "|---|---:|---:|---|"]
    for suite in ORDER:
        if suite in summary["suites"]:
            m = summary["suites"][suite]
            lines.append(f"| {SUITE_TITLES[suite]} | {m['tasks']} | {m['runs']} | {_headline(suite, m)} |")
    for split in ("dev", "heldout"):
        if split in summary.get("splits", {}):
            s = summary["splits"][split]
            k = s.get("trials", 1)
            tail = f", pass^{k} {_pct(s.get('pass^' + str(k)))}" if k > 1 else ""
            lines.append("")
            lines.append(f"Split **{split}**: {s['runs']} runs, pass^1 {_pct(s.get('pass^1'))}{tail}"
                         f" (95 % interval on pass^1: {s.get('pass^1_ci95')}).")
    failures = [r for r in results if not r["passed"]]
    if failures:
        lines += ["", "## Failures", "", "| Task | Mode | Trial | What failed |", "|---|---|---:|---|"]
        for r in failures:
            if r.get("error"):
                what = r["error"]
            else:
                what = "; ".join(c["detail"] for c in r["checks"] + r.get("security", []) if not c["ok"])
            lines.append(f"| {r['id']} | {r['mode']} | {r.get('trial', 0)} | {what.replace('|', '/')[:300]} |")
    flagged = sorted({(r["id"], u) for r in results for u in r.get("unsupported", [])})
    if flagged:
        lines += ["", "## Unsupported claims flagged", "",
                  "Every fact below appeared in a reply without appearing in the tool results of its run. "
                  "They are reviewed by hand in the evaluation report.", ""]
        lines += [f"- `{i}`: {u}" for i, u in flagged[:80]]
    return "\n".join(lines) + "\n"
