"""Minimal client for the Claude Messages API, with retries, timeouts and cost accounting.

It talks to the HTTP API directly (``requests``) rather than through the SDK: the agent only
needs one endpoint, and keeping the core dependency-light lets it run anywhere Python runs.
Anything that speaks the same request/response shape (a test double, another provider behind
a proxy) can replace it: the agent only calls ``create(...)``.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger("campus_agent.llm")

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}

# USD per million tokens (input, output, cache write 5 min, cache read).
# Source: platform.claude.com/docs/en/about-claude/pricing (checked November 2026).
# Haiku 5.5 rates are for prompts up to 100K tokens; prompts here stay far below.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.10),
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0

    def add(self, model: str, raw: dict, latency: float) -> None:
        inp = raw.get("input_tokens", 0) or 0
        out = raw.get("output_tokens", 0) or 0
        cw = raw.get("cache_creation_input_tokens", 0) or 0
        cr = raw.get("cache_read_input_tokens", 0) or 0
        self.input_tokens += inp
        self.output_tokens += out
        self.cache_write_tokens += cw
        self.cache_read_tokens += cr
        self.calls += 1
        self.latency_s += latency
        self.cost_usd += cost_of(model, inp, out, cw, cr)

    def merge(self, other: "Usage") -> None:
        for f in ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens", "calls",
                  "cost_usd", "latency_s"):
            setattr(self, f, getattr(self, f) + getattr(other, f))

    def as_dict(self) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cache_write_tokens": self.cache_write_tokens, "cache_read_tokens": self.cache_read_tokens,
                "calls": self.calls, "cost_usd": round(self.cost_usd, 6), "latency_s": round(self.latency_s, 2)}


def cost_of(model: str, inp: int, out: int, cache_write: int = 0, cache_read: int = 0) -> float:
    p_in, p_out, p_cw, p_cr = PRICES.get(model, PRICES["claude-sonnet-5-5"])  # unknown model: assume the dearer
    return (inp * p_in + out * p_out + cache_write * p_cw + cache_read * p_cr) / 1_000_000


class LLMError(RuntimeError):
    pass


class MessagesClient(Protocol):
    model: str

    def create(self, *, system: list[dict], messages: list[dict], tools: list[dict], max_tokens: int) -> dict:
        ...


@dataclass
class ClaudeClient:
    api_key: str
    model: str = "claude-haiku-5-5"
    timeout: float = 60.0
    max_retries: int = 4
    effort: str | None = None          # low / medium / high; None keeps the model default
    session: Any = field(default=None, repr=False)

    def __post_init__(self):
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY manquante : ajoute-la dans le fichier .env")
        if self.session is None:
            import requests

            self.session = requests.Session()

    def create(self, *, system: list[dict], messages: list[dict], tools: list[dict], max_tokens: int) -> dict:
        body: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens, "system": system,
                                "messages": messages, "tools": tools}
        if self.effort:
            body["output_config"] = {"effort": self.effort}
        headers = {"x-api-key": self.api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.post(API_URL, json=body, headers=headers, timeout=self.timeout)
            except Exception as exc:  # network errors: retry like a 5xx
                if attempt == self.max_retries:
                    raise LLMError(f"API Claude injoignable : {exc}") from exc
                log.warning("Claude API network error (%s), retrying", exc)
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code not in RETRY_STATUS or attempt == self.max_retries:
                    raise LLMError(f"API Claude : HTTP {resp.status_code} {resp.text[:300]}")
                retry_after = resp.headers.get("retry-after")
                if retry_after and retry_after.replace(".", "", 1).isdigit():
                    delay = max(delay, float(retry_after))
                log.warning("Claude API HTTP %s, retrying in %.1fs", resp.status_code, delay)
            time.sleep(delay + random.uniform(0, 0.5))
            delay = min(delay * 2, 30)
        raise LLMError("API Claude : nombre d'essais dépassé")
