"""Shared fixtures for the campus agent tests: one generated school, copied fresh for each test."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

NOW = "2026-11-16T09:00"   # a Monday, in week 10 of the generated semester


@pytest.fixture(scope="session")
def school_template(tmp_path_factory) -> Path:
    from campus_agent.clock import parse_dt
    from campus_agent.seed import create_database

    path = tmp_path_factory.mktemp("school") / "template.db"
    create_database(path, parse_dt(NOW), seed=2026)
    return path


@pytest.fixture()
def conn(school_template, tmp_path):
    from campus_agent import db

    path = tmp_path / "campus.db"
    shutil.copy(school_template, path)
    connection = db.connect(path)
    yield connection
    connection.close()


@pytest.fixture()
def ctx_for(conn, tmp_path):
    from campus_agent.clock import Clock
    from campus_agent.domain import Context, load_actor

    def make(person_id: str, channel: str = "api", now: str = NOW):
        return Context(conn, load_actor(conn, person_id), Clock(now), channel=channel, runtime_dir=tmp_path)

    return make


@pytest.fixture(scope="session")
def doc_index():
    from campus_agent import tools
    from campus_agent.config import PACKAGE_DIR
    from campus_agent.domain.documents import DocumentIndex

    index = DocumentIndex(PACKAGE_DIR / "data" / "docs")
    tools.set_document_index(index)
    return index
