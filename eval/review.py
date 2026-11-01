"""Review the evaluation questions one by one before trusting the scores.

    python eval/review.py

For each unvalidated question: the question, the expected facts and an excerpt of the expected source.
Answer  o = correct (validated)   s = drop this question   Enter = skip for now   q = save and quit.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "eval" / "questions.jsonl"
META = ROOT / "ENSTA_DATASET" / "04_index" / "ensta_meta.jsonl"


def excerpt(text: str, facts) -> str:
    clean = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    clean = re.sub(r"\s+", " ", clean)
    lower = clean.lower()
    for group in facts:
        for alt in group:
            pos = lower.find(alt.lower())
            if pos >= 0:
                return "…" + clean[max(0, pos - 250): pos + 250] + "…"
    return clean[:500] + "…"


def main() -> None:
    rows = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines() if line.strip()]
    texts = {}
    for line in META.read_text(encoding="utf-8").splitlines():
        meta = json.loads(line)
        texts.setdefault(meta["source"], []).append(meta.get("text") or "")
    todo = [r for r in rows if not r.get("validated") and not r.get("drop")]
    print(f"{len(todo)} question(s) à relire sur {len(rows)}.\n")
    for r in todo:
        print("=" * 80)
        print(f"{r['id']} [{r['type']}, {r['lang']}] {r['question']}")
        if r["type"] == "in_scope":
            print(f"Faits attendus : {r['expected_facts']}")
            source = r["expected_sources"][0]
            print(f"Source : {source}")
            print(excerpt(" ".join(texts.get(source, [""])), r["expected_facts"]))
        else:
            print("Hors périmètre : l'assistant doit refuser.")
        choice = input("\no = juste, s = supprimer, Entrée = plus tard, q = quitter > ").strip().lower()
        if choice == "q":
            break
        if choice == "o":
            r["validated"] = True
        elif choice == "s":
            r["drop"] = True
    QUESTIONS.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    done = sum(1 for r in rows if r.get("validated"))
    print(f"\nEnregistré : {done} validée(s), {sum(1 for r in rows if r.get('drop'))} supprimée(s).")


if __name__ == "__main__":
    main()
