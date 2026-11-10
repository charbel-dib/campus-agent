"""Loading the suite files of ``eval/suites``."""

from __future__ import annotations

import json
from pathlib import Path

from . import checks

SUITES_DIR = Path(__file__).resolve().parents[1] / "eval" / "suites"
ORDER = ["tasks", "tool_calls", "traps", "injection", "access", "grounding", "robustness", "domains", "core"]


def load(name: str) -> dict:
    spec = json.loads((SUITES_DIR / f"{name}.json").read_text(encoding="utf-8"))
    for t in spec["tasks"]:
        t["suite"] = spec["suite"]
    return spec


def load_many(names: list[str], split: str = "all", only: set[str] | None = None) -> tuple[list[dict], str, int]:
    tasks, now, seed = [], None, None
    for name in names:
        spec = load(name)
        if now and (spec["now"], spec["seed"]) != (now, seed):
            raise ValueError("all suites must share the same clock and seed")
        now, seed = spec["now"], spec["seed"]
        for t in spec["tasks"]:
            if split != "all" and t.get("split", "dev") != split:
                continue
            if only and t["id"] not in only:
                continue
            tasks.append(t)
    return tasks, now, seed


def modes(task: dict) -> list[str]:
    return ["clean", "attack"] if task["suite"] == "injection" else ["clean"]


def validate(spec: dict) -> list[str]:
    """Problems in a suite file (unknown check types, missing fields, duplicate ids)."""
    problems, ids = [], set()
    for t in spec["tasks"]:
        if t["id"] in ids:
            problems.append(f"duplicate id {t['id']}")
        ids.add(t["id"])
        if not ("turns" in t) ^ ("user" in t):
            problems.append(f"{t['id']}: needs either scripted turns or a simulated user")
        for c in t.get("checks", []) + t.get("security", []):
            if c["type"] not in checks.KNOWN:
                problems.append(f"{t['id']}: unknown check {c['type']}")
        if spec["suite"] == "injection" and not (t.get("attack_setup") and t.get("security")):
            problems.append(f"{t['id']}: injection tasks need attack_setup and security checks")
    return problems
