"""Wiring shared by the web service, the MCP server, the CLI and the evaluation.

In the public demo (``CAMPUS_SANDBOX=1``) every visitor gets a private copy of the school, so
nobody sees what another visitor booked and the demo never degrades. One spend ledger is shared
by all copies, so the daily budget holds for the whole demo.
"""

from __future__ import annotations

import logging
import secrets
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import date
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


class Ledger:
    """Model spend per day, across every visitor's database."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS spend (day TEXT NOT NULL, cost_usd REAL NOT NULL)")

    def record(self, day: date, cost: float) -> None:
        with self._lock, sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO spend(day, cost_usd) VALUES (?, ?)", (day.isoformat(), cost))

    def spent(self, day: date) -> float:
        with sqlite3.connect(self.path) as conn:
            return conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM spend WHERE day = ?",
                                (day.isoformat(),)).fetchone()[0]


class Sandboxes:
    """One directory per visitor: a copy of the school database and the files it generates."""

    def __init__(self, root: Path, template: Path, ttl_hours: float, max_count: int):
        self.root = root
        self.template = template
        self.ttl_seconds = ttl_hours * 3600
        self.max_count = max_count
        self._lock = threading.Lock()
        root.mkdir(parents=True, exist_ok=True)

    def _dirs(self) -> list[Path]:
        return [p for p in self.root.iterdir() if p.is_dir() and (p / "campus.db").exists()]

    @staticmethod
    def _last_use(d: Path) -> float:
        return max((p.stat().st_mtime for p in d.glob("campus.db*")), default=d.stat().st_mtime)

    def cleanup(self) -> None:
        now = time.time()
        for d in self._dirs():
            if now - self._last_use(d) > self.ttl_seconds:
                shutil.rmtree(d, ignore_errors=True)

    def create(self) -> str:
        with self._lock:
            self.cleanup()
            existing = sorted(self._dirs(), key=self._last_use)
            for d in existing[: max(0, len(existing) - self.max_count + 1)]:   # make room: drop the least recent
                shutil.rmtree(d, ignore_errors=True)
            sid = secrets.token_hex(12)
            d = self.root / sid
            d.mkdir()
            shutil.copy(self.template, d / "campus.db")
            return sid

    def directory(self, sid: str) -> Path | None:
        if not sid or not all(c in "0123456789abcdef" for c in sid):
            return None
        d = self.root / sid
        if not (d / "campus.db").exists() or time.time() - self._last_use(d) > self.ttl_seconds:
            return None
        return d


@dataclass
class Runtime:
    settings: Settings
    clock: Clock
    db_path: Path
    ledger: Ledger | None = None
    sandboxes: Sandboxes | None = None
    providers: object = None

    def _dir(self, sandbox: str | None) -> Path:
        if self.sandboxes is None:
            return self.settings.runtime_dir
        d = self.sandboxes.directory(sandbox or "")
        if d is None:
            raise KeyError("sandbox expired")
        return d

    def connect(self, sandbox: str | None = None):
        if self.sandboxes is None:
            return db.connect(self.db_path)
        return db.connect(self._dir(sandbox) / "campus.db")

    def context(self, conn, person_id: str, channel: str = "api", session_id: str | None = None,
                sandbox: str | None = None) -> Context:
        return Context(conn, load_actor(conn, person_id), self.clock, channel=channel, session_id=session_id,
                       runtime_dir=self._dir(sandbox))

    def llm(self, model: str | None = None) -> ClaudeClient:
        s = self.settings
        return ClaudeClient(api_key=s.anthropic_api_key, model=model or s.model, timeout=s.api_timeout_seconds,
                            effort=s.effort or None)

    def agent(self, conn, llm=None, channel: str = "agent", sandbox: str | None = None) -> Agent:
        s = self.settings
        return Agent(conn, self.clock, llm or self.llm(), max_steps=s.max_steps, max_tokens=s.max_tokens,
                     session_budget_usd=s.session_budget_usd, daily_budget_usd=s.daily_budget_usd,
                     channel=channel, runtime_dir=self._dir(sandbox),
                     pending_ttl_minutes=s.pending_action_ttl_minutes, ledger=self.ledger,
                     escalation_llm=self.llm(s.escalation_model) if s.escalation_model else None,
                     escalate_after_steps=s.escalate_after_steps, cache_ttl=s.cache_ttl,
                     otlp_endpoint=s.otlp_endpoint)

    def spent_today(self, conn=None) -> float:
        if self.ledger is not None:
            return self.ledger.spent(self.clock.now().date())
        own = conn or self.connect()
        try:
            return own.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM agent_sessions WHERE substr(updated_at,1,10)=?",
                               (self.clock.now().date().isoformat(),)).fetchone()[0]
        finally:
            if conn is None:
                own.close()


def boot(settings: Settings | None = None, *, reset: bool = False) -> Runtime:
    """Load settings, create the school database if needed, load the document index."""
    settings = settings or load_settings()
    clock = Clock(settings.now_override)
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    tools.set_document_index(DocumentIndex(settings.docs_dir, settings.docs_retriever, settings.emb_model))
    from .integrations.providers import from_settings

    providers = from_settings(settings)
    tools.set_providers(providers)
    tools.configure({"clarify": settings.clarify_choices, "skills": settings.skills,
                     "moodle": providers.assignments is not None}, set(settings.disabled_tools))
    if settings.sandbox:
        # The template is regenerated at each start, so the semester always sits around today.
        template = settings.runtime_dir / "template.db"
        create_database(template, clock.now(), settings.seed)
        return Runtime(settings, clock, template, ledger=Ledger(settings.runtime_dir / "ledger.db"),
                       sandboxes=Sandboxes(settings.runtime_dir / "sandboxes", template, settings.sandbox_ttl_hours,
                                           settings.sandbox_max), providers=providers)
    if reset or not settings.db_path.exists():
        info = create_database(settings.db_path, clock.now(), settings.seed)
        log.info("school database created: %s", info)
    else:
        conn = db.connect(settings.db_path)
        db.init_schema(conn)          # idempotent: adds tables introduced since the file was created
        conn.close()
    return Runtime(settings, clock, settings.db_path, providers=providers)
