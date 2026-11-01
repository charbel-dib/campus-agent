"""HTTP checks that run without loading the index: the RAG loads in a startup task, which TestClient skips here."""

from fastapi.testclient import TestClient

import app

client = TestClient(app.app)


def test_root_redirects_to_the_chat_page():
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"].startswith("/chat")


def test_health_reports_the_rag_settings():
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["top_k"] == app.TOP_K
    assert "rag_ready" in body
