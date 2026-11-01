"""Scoring helpers for the evaluation, kept free of models and network so they can be unit-tested."""

from __future__ import annotations

import re
from statistics import mean
from typing import Dict, Iterable, List, Optional, Sequence

CITATION_RE = re.compile(r"\[S(\d+)\]")


def normalize(text: str) -> str:
    """Lowercase and drop every kind of space, so '10 000', '10 000' and '10000' compare equal."""
    text = (text or "").lower().replace("’", "'")
    return re.sub(r"\s+", "", text)


def facts_match(answer: str, fact_groups: Sequence[Sequence[str]]) -> bool:
    """True when every group has at least one alternative present in the answer."""
    norm = normalize(answer)
    return all(any(normalize(alt) in norm for alt in group) for group in fact_groups)


def first_hit_rank(retrieved_sources: Sequence[str], expected: Iterable[str]) -> Optional[int]:
    """1-based rank of the first retrieved passage that comes from an expected source."""
    wanted = set(expected)
    for rank, source in enumerate(retrieved_sources, start=1):
        if source in wanted:
            return rank
    return None


def cited_sources(answer: str, retrieved_sources: Sequence[str]) -> List[str]:
    """Sources the answer cites as [S1], [S2]... (numbering follows the retrieved order)."""
    out = []
    for number in sorted({int(n) for n in CITATION_RE.findall(answer or "")}):
        if 1 <= number <= len(retrieved_sources):
            out.append(retrieved_sources[number - 1])
    return out


def retrieval_summary(rows: List[Dict], k: int) -> Dict[str, float]:
    ranks = [r["rank"] for r in rows]
    hit = lambda n: mean(1.0 if (x is not None and x <= n) else 0.0 for x in ranks)  # noqa: E731
    return {
        "questions": len(rows),
        "hit@1": hit(1),
        "hit@3": hit(3),
        f"hit@{k}": hit(k),
        f"mrr@{k}": mean((1.0 / x) if (x is not None and x <= k) else 0.0 for x in ranks),
    }


def best_threshold(in_scope_scores: Sequence[float], out_scores: Sequence[float]) -> Dict[str, float]:
    """Indicative only: the score cut that best separates answerable from out-of-scope questions."""
    candidates = sorted(set(in_scope_scores) | set(out_scores))
    best = {"threshold": 0.0, "balanced_accuracy": 0.0}
    for t in candidates:
        kept = mean(1.0 if s >= t else 0.0 for s in in_scope_scores)
        refused = mean(1.0 if s < t else 0.0 for s in out_scores)
        score = (kept + refused) / 2
        if score > best["balanced_accuracy"]:
            best = {"threshold": t, "balanced_accuracy": score, "in_scope_kept": kept, "out_of_scope_refused": refused}
    return best
