"""Unit tests for the pure helpers of app.py (no model, index or API key needed)."""

import numpy as np
import pytest

import app


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Où se trouve la bibliothèque ?", "fr"),
        ("Quels sont les frais de scolarité pour les étudiants", "fr"),
        ("Where is the library?", "en"),
        ("What are the tuition fees for the master", "en"),
        ("ok", None),
        ("", None),
    ],
)
def test_detect_lang(text, expected):
    assert app.detect_lang(text) == expected


@pytest.mark.parametrize("text", ["Qui es-tu ?", "who are you", "Comment ça marche ?"])
def test_smalltalk_is_answered_without_retrieval(text):
    assert app.is_smalltalk_or_meta(text)


@pytest.mark.parametrize("text", ["", "?", "1234", "xkqzrtpwvb", "%%%%%%%a"])
def test_gibberish_is_rejected(text):
    assert app.looks_like_gibberish(text)


@pytest.mark.parametrize(
    "text", ["Quand commence le stage de 2A ?", "qui es-tu", "contact: jean.dupont@ensta.fr"]
)
def test_real_questions_are_not_gibberish(text):
    assert not app.looks_like_gibberish(text)


def test_extract_email():
    assert app.extract_email("écrivez-moi à Jean.Dupont@ensta-paris.fr merci") == "Jean.Dupont@ensta-paris.fr"
    assert app.extract_email("pas d'adresse ici") is None


def test_prune_history_keeps_the_latest_turns():
    history = [{"role": "user", "content": f"message {i}"} for i in range(40)]
    pruned = app.prune_history(list(history))
    assert len(pruned) <= app.MAX_HISTORY_TURNS * 2
    assert pruned[-1] == history[-1]


def test_prune_history_respects_the_character_budget():
    history = [{"role": "user", "content": "x" * 4000} for _ in range(3)]
    pruned = app.prune_history(list(history))
    assert app.history_chars(pruned) <= app.MAX_HISTORY_CHARS


class FakeIndex:
    """Mimics faiss.Index.search: returns scores and row ids, -1 for missing results."""

    def search(self, query, k):
        return np.array([[0.9, 0.5, 0.1]]), np.array([[2, 0, -1]])


def test_retrieve_maps_ids_to_metadata_and_drops_missing_rows():
    metas = [{"title": "A"}, {"title": "B"}, {"title": "C"}]
    results = app.retrieve(FakeIndex(), metas, np.zeros(4, dtype=np.float32))
    assert [(round(s, 1), m["title"]) for s, m in results] == [(0.9, "C"), (0.5, "A")]


def test_sources_block_numbers_sources_and_returns_best_score():
    results = [(0.82, {"source": "https://x", "title": "Stages", "text": "Le stage dure 12 semaines."})]
    text, best = app.build_sources_block(results)
    assert text.startswith("[S1] score=0.820")
    assert "Le stage dure 12 semaines." in text
    assert best == pytest.approx(0.82)
    assert app.build_sources_block([]) == ("", 0.0)


class FakeEmbedder:
    def encode(self, texts, normalize_embeddings, show_progress_bar):
        return np.zeros((len(texts), 4), dtype=np.float32)


class LengthReranker:
    """Scores a passage by its length, so the expected order is easy to read."""

    def predict(self, pairs):
        return [len(text) for _, text in pairs]


class ThreeHitIndex:
    def search(self, query, k):
        return np.array([[0.9, 0.8, 0.7]]), np.array([[0, 1, 2]])


def test_search_passages_reranks_but_keeps_the_best_dense_score():
    metas = [{"text": "a"}, {"text": "ccc"}, {"text": "bb"}]
    hits, best = app.search_passages(ThreeHitIndex(), metas, FakeEmbedder(), LengthReranker(), "q")
    assert [m["text"] for _, m in hits] == ["ccc", "bb", "a"]
    assert best == pytest.approx(0.9)


def test_search_passages_without_reranker_keeps_dense_order():
    metas = [{"text": "a"}, {"text": "ccc"}, {"text": "bb"}]
    hits, _ = app.search_passages(ThreeHitIndex(), metas, FakeEmbedder(), None, "q")
    assert [m["text"] for _, m in hits] == ["a", "ccc", "bb"]
