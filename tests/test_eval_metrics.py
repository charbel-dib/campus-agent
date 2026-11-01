"""Scoring rules of the evaluation (eval/metrics.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

from metrics import best_threshold, cited_sources, facts_match, first_hit_rank, retrieval_summary  # noqa: E402


def test_facts_match_ignores_case_and_spacing():
    assert facts_match("Le budget était de 10 000 euros.", [["10 000"]])
    assert facts_match("Il dure 2 ans, en anglais.", [["2 ans", "4 semestres"], ["anglais"]])
    assert not facts_match("Il dure 2 ans.", [["2 ans"], ["anglais"]])


def test_first_hit_rank():
    assert first_hit_rank(["a", "b", "c"], ["c"]) == 3
    assert first_hit_rank(["a", "b"], ["z"]) is None


def test_cited_sources_follow_retrieved_order_and_ignore_unknown_numbers():
    assert cited_sources("Oui [S2], voir aussi [S9] et [S2].", ["a", "b"]) == ["b"]


def test_retrieval_summary():
    s = retrieval_summary([{"rank": 1}, {"rank": 3}, {"rank": None}, {"rank": 8}], k=8)
    assert s["hit@1"] == 0.25 and s["hit@3"] == 0.5 and s["hit@8"] == 0.75
    assert abs(s["mrr@8"] - (1 + 1 / 3 + 1 / 8) / 4) < 1e-9


def test_best_threshold_separates_clear_cases():
    b = best_threshold([0.85, 0.9, 0.88], [0.7, 0.72])
    assert b["balanced_accuracy"] == 1.0 and 0.72 < b["threshold"] <= 0.85
