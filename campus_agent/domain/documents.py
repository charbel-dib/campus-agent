"""Search in the school's rules and procedures, with a citable reference for each passage.

Two retrievers share one interface:

- ``bm25`` (default): lexical BM25 over the sections, no dependency, deterministic. On a small,
  well-titled corpus like this one it is a strong baseline.
- ``dense``: the multilingual E5 embeddings already used by the ENSTA RAG (``app.py``), combined
  with BM25 by reciprocal rank fusion. Needs ``sentence-transformers``.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import InvalidRequest, fold

STOPWORDS = set("""
a au aux avec ce ces cet cette d dans de des du elle en est et eux il ils je la le les leur lui ma mais me meme
mes moi mon ne nos notre nous on ou par pas pour qu que qui sa se ses son sur ta te tes toi ton tu un une vos
votre vous y l c s n j m t est sont ete etre avoir a ai as avons avez ont fait faire peut peux puis comment quel
quelle quels quelles quoi combien quand est-ce faut dois doit-on
""".split())


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", fold(text))
    out = []
    for w in words:
        if w in STOPWORDS or len(w) < 2:
            continue
        # light French stemming: plural and a few frequent endings
        for suffix in ("ements", "ement", "ations", "ation", "ees", "ee", "er", "ez", "es", "s", "x"):
            if len(w) > len(suffix) + 3 and w.endswith(suffix):
                w = w[: -len(suffix)]
                break
        out.append(w)
    return out


@dataclass(frozen=True)
class Passage:
    ref: str            # e.g. procedure-absences#1
    doc_title: str
    section: str
    text: str


def load_passages(docs_dir: Path) -> list[Passage]:
    passages: list[Passage] = []
    for path in sorted(docs_dir.glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        doc_title = next((l[2:].strip() for l in lines if l.startswith("# ")), path.stem)
        section, buf, n = "Introduction", [], 0

        def flush():
            body = "\n".join(buf).strip()
            # skip the one-line preamble ("Établissement fictif…") that every document carries
            if body and not (section == "Introduction" and len(body) < 160):
                passages.append(Passage(f"{path.stem}#{n}", doc_title, section, body))

        for line in lines:
            if line.startswith("## "):
                flush()
                n += 1
                section, buf = line[3:].strip(), []
            elif not line.startswith("# "):
                buf.append(line)
        flush()
    return passages


class BM25:
    def __init__(self, passages: list[Passage], k1: float = 1.4, b: float = 0.75):
        self.passages = passages
        # Section and document titles count twice: they summarise the passage.
        self.docs = [tokenize(f"{p.doc_title} {p.section} {p.section} {p.text}") for p in passages]
        self.avgdl = sum(map(len, self.docs)) / max(1, len(self.docs))
        self.df = Counter(t for d in self.docs for t in set(d))
        self.tf = [Counter(d) for d in self.docs]
        self.k1, self.b, self.n = k1, b, len(passages)

    def scores(self, query: str) -> list[float]:
        q = tokenize(query)
        out = []
        for tf, doc in zip(self.tf, self.docs):
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                idf = math.log(1 + (self.n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                s += idf * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * len(doc) / self.avgdl))
            out.append(s)
        return out


class DenseRetriever:
    """E5 embeddings, as in the ENSTA RAG. Loaded lazily; the corpus is small enough to keep in memory."""

    def __init__(self, passages: list[Passage], model_name: str):
        from sentence_transformers import SentenceTransformer  # optional dependency
        import numpy as np

        self.np = np
        self.model = SentenceTransformer(model_name)
        texts = [f"passage: {p.doc_title}. {p.section}. {p.text}" for p in passages]
        self.matrix = self.model.encode(texts, normalize_embeddings=True)

    def scores(self, query: str) -> list[float]:
        q = self.model.encode([f"query: {query}"], normalize_embeddings=True)[0]
        return list(self.matrix @ q)


class DocumentIndex:
    def __init__(self, docs_dir: Path, retriever: str = "bm25", emb_model: str = "intfloat/multilingual-e5-base"):
        self.passages = load_passages(docs_dir)
        if not self.passages:
            raise RuntimeError(f"aucun document dans {docs_dir}")
        self.bm25 = BM25(self.passages)
        self.dense = DenseRetriever(self.passages, emb_model) if retriever == "dense" else None
        self.kind = "bm25+e5 (fusion RRF)" if self.dense else "bm25"

    def search(self, query: str, k: int = 4) -> dict:
        if not query or len(query.strip()) < 2:
            raise InvalidRequest("la recherche est vide")
        k = max(1, min(int(k), 8))
        lexical = self.bm25.scores(query)
        if self.dense is None:
            ranked = sorted(range(len(self.passages)), key=lambda i: -lexical[i])
            ranked = [i for i in ranked if lexical[i] > 0]
        else:
            dense = self.dense.scores(query)
            fused: dict[int, float] = {}
            for scores in (lexical, dense):
                order = sorted(range(len(self.passages)), key=lambda i: -scores[i])
                for rank, i in enumerate(order):
                    fused[i] = fused.get(i, 0.0) + 1.0 / (60 + rank)
            ranked = sorted(fused, key=lambda i: -fused[i])
        results = [{"ref": self.passages[i].ref, "document": self.passages[i].doc_title,
                    "section": self.passages[i].section, "text": self.passages[i].text} for i in ranked[:k]]
        out = {"retriever": self.kind, "results": results}
        if not results:
            out["message"] = "Aucun passage ne correspond : la réponse n'est pas dans les documents de l'école."
        return out
