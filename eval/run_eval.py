"""Evaluate the ENSTA assistant on eval/questions.jsonl, with the app's own index, model and settings.

    python eval/run_eval.py                       retrieval only, no API key needed
    python eval/run_eval.py --rerank              retrieval + multilingual cross-encoder reranking
    python eval/run_eval.py --answers [--rerank]  full answers with the OpenAI model from .env (paid, ~60 calls)

Results go to eval/results/<label>.json and .md. Use --validated-only once questions are reviewed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import app  # noqa: E402  (reads .env and the same settings as the server)
from metrics import best_threshold, cited_sources, facts_match, first_hit_rank, retrieval_summary  # noqa: E402

DEFAULT_RERANKER = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"  # multilingual, ~120M parameters
POOL = 30  # candidates passed to the reranker


class UsageMeter:
    """Wraps the OpenAI client to count tokens of every call made through app.call_openai."""

    def __init__(self, client):
        self._client = client
        self.input_tokens = 0
        self.output_tokens = 0
        self.responses = self

    def create(self, **kwargs):
        response = self._client.responses.create(**kwargs)
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.input_tokens += getattr(usage, "input_tokens", 0) or 0
            self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        return response


def load_questions(validated_only: bool):
    rows = [json.loads(line) for line in (ROOT / "eval" / "questions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [r for r in rows if not r.get("drop")]
    return [r for r in rows if r.get("validated")] if validated_only else rows


def search(index, metas, model, question: str, reranker):
    import numpy as np

    qvec = app.embed_query(model, question).reshape(1, -1)
    depth = POOL if reranker is not None else app.TOP_K
    scores, ids = index.search(qvec, depth)
    hits = [(float(s), metas[i]) for s, i in zip(scores[0].tolist(), ids[0].tolist()) if 0 <= i < len(metas)]
    dense_best = hits[0][0] if hits else 0.0
    if reranker is not None and hits:
        rerank_scores = reranker.predict([(question, (m.get("text") or "")[:1400]) for _, m in hits])
        order = np.argsort(-np.asarray(rerank_scores))
        hits = [hits[j] for j in order]
    return hits[: app.TOP_K], dense_best


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rerank", nargs="?", const=DEFAULT_RERANKER, help="cross-encoder model name")
    parser.add_argument("--answers", action="store_true", help="also generate answers with the OpenAI model")
    parser.add_argument("--validated-only", action="store_true")
    args = parser.parse_args()

    import faiss
    from sentence_transformers import CrossEncoder, SentenceTransformer

    questions = load_questions(args.validated_only)
    index = faiss.read_index(str(app.INDEX_PATH))
    metas = app.load_meta(app.META_PATH)
    model = SentenceTransformer(app.EMB_MODEL)
    reranker = CrossEncoder(args.rerank) if args.rerank else None
    client = UsageMeter(app.OpenAI()) if args.answers else None

    rows = []
    started = time.perf_counter()
    for q in questions:
        hits, dense_best = search(index, metas, model, q["question"], reranker)
        sources = [m.get("source", "") for _, m in hits]
        row = {
            "id": q["id"], "type": q["type"], "lang": q["lang"], "source_type": q.get("source_type"),
            "question": q["question"], "rank": first_hit_rank(sources, q["expected_sources"]),
            "dense_best": dense_best, "retrieved": sources[:3],
        }
        if client is not None:
            refused_by_threshold = dense_best < app.MIN_SCORE
            answer = ""
            if not refused_by_threshold:
                text, _ = app.build_sources_block(hits)
                lang = app.detect_lang(q["question"]) or q["lang"]
                answer = app.call_openai(client, [], q["question"], text, lang).strip()
            refused = refused_by_threshold or not answer or app.NO_ANSWER_TOKEN in answer or answer in {
                app.UX["fr"]["no_answer"], app.UX["en"]["no_answer"]}
            cited = cited_sources(answer, sources)
            row.update({
                "answer": answer, "refused": refused,
                "facts_ok": (not refused) and facts_match(answer, q["expected_facts"]) if q["type"] == "in_scope" else None,
                "cites_expected": bool(set(cited) & set(q["expected_sources"])) if q["type"] == "in_scope" and not refused else None,
            })
        rows.append(row)
        print(f"{q['id']} rank={row['rank']} best={dense_best:.3f}" + (f" refused={row['refused']}" if client else ""))
    elapsed = time.perf_counter() - started

    inside = [r for r in rows if r["type"] == "in_scope"]
    outside = [r for r in rows if r["type"] == "out_of_scope"]
    k = app.TOP_K
    summary = {
        "embedding_model": app.EMB_MODEL, "reranker": args.rerank, "top_k": k, "min_score": app.MIN_SCORE,
        "retrieval_all": retrieval_summary(inside, k),
        "retrieval_by_lang": {lang: retrieval_summary([r for r in inside if r["lang"] == lang], k) for lang in sorted({r["lang"] for r in inside})},
        "retrieval_by_source_type": {t: retrieval_summary([r for r in inside if r["source_type"] == t], k) for t in sorted({r["source_type"] for r in inside})},
        "dense_best_score": {
            "in_scope_median": median(r["dense_best"] for r in inside),
            "out_of_scope_median": median(r["dense_best"] for r in outside) if outside else None,
            "threshold_refuses_out_of_scope": mean(1.0 if r["dense_best"] < app.MIN_SCORE else 0.0 for r in outside) if outside else None,
            "indicative_best_threshold": best_threshold([r["dense_best"] for r in inside], [r["dense_best"] for r in outside]) if outside else None,
        },
        "seconds_per_question": elapsed / max(1, len(rows)),
    }
    if client is not None:
        answered = [r for r in inside if not r["refused"]]
        summary["answers"] = {
            "model": app.OPENAI_MODEL,
            "in_scope_correct": mean(1.0 if r["facts_ok"] else 0.0 for r in inside),
            "in_scope_false_refusals": mean(1.0 if r["refused"] else 0.0 for r in inside),
            "correct_citation_when_answered": mean(1.0 if r["cites_expected"] else 0.0 for r in answered) if answered else None,
            "out_of_scope_correct_refusals": mean(1.0 if r["refused"] else 0.0 for r in outside) if outside else None,
            "input_tokens": client.input_tokens, "output_tokens": client.output_tokens,
        }

    label = ("answers-" if args.answers else "retrieval-") + ("rerank" if args.rerank else "dense")
    out_dir = ROOT / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{label}.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / f"{label}.md").write_text(to_markdown(label, summary, len(rows)), encoding="utf-8")
    print(to_markdown(label, summary, len(rows)))


def pct(x) -> str:
    return "—" if x is None else f"{100 * x:.1f} %"


def to_markdown(label: str, s: dict, n: int) -> str:
    k = s["top_k"]
    lines = [f"## {label} ({n} questions, top-{k}, embeddings `{s['embedding_model']}`, reranker `{s['reranker'] or 'none'}`)", "",
             f"| Segment | Questions | Hit@1 | Hit@3 | Hit@{k} | MRR@{k} |", "|---|---:|---:|---:|---:|---:|"]
    segments = [("All answerable", s["retrieval_all"])]
    segments += [(f"Language: {lang}", v) for lang, v in s["retrieval_by_lang"].items()]
    segments += [(f"Source: {t}", v) for t, v in s["retrieval_by_source_type"].items()]
    for name, v in segments:
        lines.append(f"| {name} | {v['questions']} | {pct(v['hit@1'])} | {pct(v['hit@3'])} | {pct(v[f'hit@{k}'])} | {v[f'mrr@{k}']:.3f} |")
    d = s["dense_best_score"]
    lines += ["", f"Best dense score, median: answerable {d['in_scope_median']:.3f}, out of scope "
              f"{d['out_of_scope_median']:.3f}. The current threshold ({s['min_score']}) refuses "
              f"{pct(d['threshold_refuses_out_of_scope'])} of out-of-scope questions."]
    if d["indicative_best_threshold"]:
        b = d["indicative_best_threshold"]
        lines.append(f"Indicative best threshold on this set: {b['threshold']:.3f} (balanced accuracy {pct(b['balanced_accuracy'])}).")
    if "answers" in s:
        a = s["answers"]
        lines += ["", f"| Answers (`{a['model']}`) | Value |", "|---|---:|",
                  f"| Correct facts, answerable questions | {pct(a['in_scope_correct'])} |",
                  f"| False refusals, answerable questions | {pct(a['in_scope_false_refusals'])} |",
                  f"| Cites the expected source, when answering | {pct(a['correct_citation_when_answered'])} |",
                  f"| Correct refusals, out-of-scope questions | {pct(a['out_of_scope_correct_refusals'])} |",
                  f"| Tokens in / out | {a['input_tokens']:,} / {a['output_tokens']:,} |"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
