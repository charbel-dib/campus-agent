"""Wiring shared by the web service, the MCP server, the CLI and the evaluation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from . import db, tools
from .agent import Agent
from .clock import Clock
from .config import Settings, load_settings
from .domain import Context, load_actor
from .domain.documents import DocumentIndex
from .llm import ClaudeClient
from .seed import create_database

log = logging.getLogger("campus_agent")


@dataclass
class Runtime:
    settings: Settings
    clock: Clock
    db_path: Path

    def connect(self):
        return db.connect(self.db_path)

    def context(self, conn, person_id: str, channel: str = "api", session_id: str | None = None) -> Context:
        return Context(conn, load_actor(conn, person_id), self.clock, channel=channel, session_id=session_id,
                       runtime_dir=self.settings.runtime_dir)

    def llm(self, model: str | None = None) -> ClaudeClient:
        s = self.settings
        return ClaudeClient(api_key=s.anthropic_api_key, model=model or s.model, timeout=s.api_timeout_seconds,
                            effort=s.effort or None)

    def agent(self, conn, llm=None, channel: str = "agent") -> Agent:
        s = self.settings
        return Agent(conn, self.clock, llm or self.llm(), max_steps=s.max_steps, max_tokens=s.max_tokens,
                     session_budget_usd=s.session_budget_usd, daily_budget_usd=s.daily_budget_usd,
                     channel=channel, runtime_dir=s.runtime_dir, pending_ttl_minutes=s.pending_action_ttl_minutes)


def boot(settings: Settings | None = None, *, reset: bool = False) -> Runtime:
    """Load settings, create the school database if needed, load the document index."""
    settings = settings or load_settings()
    clock = Clock(settings.now_override)
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    if reset or not settings.db_path.exists():
        info = create_database(settings.db_path, clock.now(), settings.seed)
        log.info("school database created: %s", info)
    else:
        conn = db.connect(settings.db_path)
        db.init_schema(conn)          # idempotent: adds tables introduced since the file was created
        conn.close()
    tools.set_document_index(DocumentIndex(settings.docs_dir, settings.docs_retriever, settings.emb_model))
    return Runtime(settings, clock, settings.db_path)
