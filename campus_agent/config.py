"""Settings, read once from the environment (and from a .env file when present)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_DIR = PACKAGE_DIR.parent


def load_dotenv_file(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines, comments and blank lines ignored.

    Values already present in the environment win, as with python-dotenv.
    It avoids a dependency so the core runs with the standard library only.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Storage
    db_path: Path
    runtime_dir: Path
    docs_dir: Path
    # School clock: empty means the real clock; "2026-11-16T09:00" pins it (demo, evaluation)
    now_override: str
    seed: int
    # Claude
    anthropic_api_key: str
    model: str
    max_tokens: int
    effort: str
    max_steps: int
    api_timeout_seconds: float
    # Cost control
    session_budget_usd: float
    daily_budget_usd: float
    # Web
    secret_key: str
    demo_login: bool
    rate_limit_per_minute: int
    pending_action_ttl_minutes: int
    # Documentation search: "bm25" (no dependency) or "dense" (E5 embeddings, as the ENSTA RAG)
    docs_retriever: str
    emb_model: str
    extra: dict = field(default_factory=dict)


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv_file(env_file or REPO_DIR / ".env")
    runtime_dir = Path(_env("CAMPUS_RUNTIME_DIR", str(REPO_DIR / "campus_data"))).resolve()
    return Settings(
        db_path=Path(_env("CAMPUS_DB_PATH", str(runtime_dir / "campus.db"))).resolve(),
        runtime_dir=runtime_dir,
        docs_dir=Path(_env("CAMPUS_DOCS_DIR", str(PACKAGE_DIR / "data" / "docs"))).resolve(),
        now_override=_env("CAMPUS_NOW", ""),
        seed=_env_int("CAMPUS_SEED", 2026),
        anthropic_api_key=_env("ANTHROPIC_API_KEY", ""),
        model=_env("CAMPUS_MODEL", "claude-haiku-5-5"),
        max_tokens=_env_int("CAMPUS_MAX_TOKENS", 4096),
        effort=_env("CAMPUS_EFFORT", ""),
        max_steps=_env_int("CAMPUS_MAX_STEPS", 10),
        api_timeout_seconds=_env_float("CAMPUS_API_TIMEOUT", 60.0),
        session_budget_usd=_env_float("CAMPUS_SESSION_BUDGET_USD", 0.25),
        daily_budget_usd=_env_float("CAMPUS_DAILY_BUDGET_USD", 2.0),
        secret_key=_env("CAMPUS_SECRET_KEY", ""),
        demo_login=_env_bool("CAMPUS_DEMO_LOGIN", True),
        rate_limit_per_minute=_env_int("CAMPUS_RATE_LIMIT_PER_MINUTE", 12),
        pending_action_ttl_minutes=_env_int("CAMPUS_PENDING_TTL_MINUTES", 15),
        docs_retriever=_env("CAMPUS_DOCS_RETRIEVER", "bm25").lower(),
        emb_model=_env("EMB_MODEL", "intfloat/multilingual-e5-base"),
    )
