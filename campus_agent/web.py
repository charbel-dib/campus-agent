"""HTTP service and demo interface of the campus agent (Starlette, the ASGI toolkit FastAPI is built on).

    uvicorn campus_agent.web:app --port 8002

Authentication: the demo signs in by choosing a persona and receives a signed, expiring token.
In production, this endpoint is replaced by the school's single sign-on (OIDC); everything
behind it only trusts the token's person id, never anything the model says.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta
from pathlib import Path

import anyio
from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import tools
from .cli import PERSONAS
from .clock import monday_of
from .domain import DomainError, NotFound, academics, audit, load_actor
from .llm import LLMError
from .runtime import Runtime, boot

log = logging.getLogger("campus_agent.web")

STATIC_DIR = Path(__file__).resolve().parent / "static"
TOKEN_TTL_SECONDS = 8 * 3600
MAX_BODY_BYTES = 16_000


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


# ---------------------------------------------------------------------------------------
# Tokens and rate limiting
# ---------------------------------------------------------------------------------------

class Signer:
    def __init__(self, secret: str):
        if not secret:
            secret = secrets.token_hex(32)
            log.warning("CAMPUS_SECRET_KEY is empty: using a random key (tokens reset at each restart)")
        self.key = secret.encode()

    def issue(self, person_id: str, ttl: int = TOKEN_TTL_SECONDS) -> str:
        payload = f"{person_id}|{int(time.time()) + ttl}"
        sig = hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()[:32]
        return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode().rstrip("=")

    def verify(self, token: str) -> str:
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
            person_id, expiry, sig = raw.split("|")
        except Exception as exc:  # noqa: BLE001
            raise ApiError(401, "jeton invalide") from exc
        expected = hmac.new(self.key, f"{person_id}|{expiry}".encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            raise ApiError(401, "jeton invalide")
        if int(expiry) < time.time():
            raise ApiError(401, "session expirée, reconnecte-toi")
        return person_id


class RateLimiter:
    """Sliding window per key, in memory (one process). Behind several workers, use a shared store."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            raise ApiError(429, "trop de requêtes, patiente une minute")
        q.append(now)


# ---------------------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------------------

def create_app(rt: Runtime | None = None, llm_factory=None) -> Starlette:
    """``llm_factory`` lets tests inject a scripted model; by default the Claude client is used."""
    rt = rt or boot()
    signer = Signer(rt.settings.secret_key)
    limiter = RateLimiter(rt.settings.rate_limit_per_minute)
    make_llm = llm_factory or (lambda: rt.llm())

    def actor_id(request: Request) -> str:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise ApiError(401, "connexion requise")
        return signer.verify(header[7:].strip())

    async def body(request: Request) -> dict:
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            raise ApiError(413, "requête trop volumineuse")
        try:
            data = json.loads(raw or b"{}")
        except ValueError as exc:
            raise ApiError(400, "JSON invalide") from exc
        if not isinstance(data, dict):
            raise ApiError(400, "un objet JSON est attendu")
        return data

    def with_conn(fn, *args):
        conn = rt.connect()
        try:
            return fn(conn, *args)
        finally:
            conn.close()

    def endpoint(handler):
        async def wrapped(request: Request) -> Response:
            try:
                return await handler(request)
            except ApiError as exc:
                return JSONResponse({"error": exc.message}, status_code=exc.status)
            except DomainError as exc:
                status = {"denied": 403, "not_found": 404, "conflict": 409}.get(exc.code, 400)
                return JSONResponse({"error": exc.message, "code": exc.code}, status_code=status)
            except LLMError as exc:
                log.error("model unavailable: %s", exc)
                return JSONResponse({"error": "le service d'IA est indisponible ou n'est pas configuré"},
                                    status_code=503)
            except Exception:  # noqa: BLE001
                log.exception("unhandled error on %s", request.url.path)
                return JSONResponse({"error": "erreur interne"}, status_code=500)
        return wrapped

    # ---------------------------------------------------------------- public

    async def index(_request: Request) -> Response:
        return FileResponse(STATIC_DIR / "index.html", headers={"cache-control": "no-cache"})

    async def health(_request: Request) -> Response:
        def check(conn):
            agent_ok = bool(rt.settings.anthropic_api_key)
            spent = conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM agent_sessions WHERE substr(updated_at,1,10)=?",
                                 (rt.clock.now().date().isoformat(),)).fetchone()[0]
            return {"ok": True, "model": rt.settings.model, "agent_configured": agent_ok,
                    "now": rt.clock.now().isoformat(timespec="minutes"),
                    "spent_today_usd": round(spent, 4), "daily_budget_usd": rt.settings.daily_budget_usd}
        return JSONResponse(await anyio.to_thread.run_sync(with_conn, check))

    async def personas(_request: Request) -> Response:
        if not rt.settings.demo_login:
            raise ApiError(404, "connexion de démonstration désactivée")

        def load(conn):
            out = []
            for pid, label in PERSONAS:
                a = load_actor(conn, pid)
                out.append({"id": a.id, "name": a.name, "role": a.role, "label": label})
            return out
        return JSONResponse({"personas": await anyio.to_thread.run_sync(with_conn, load)})

    async def login(request: Request) -> Response:
        if not rt.settings.demo_login:
            raise ApiError(404, "connexion de démonstration désactivée")
        data = await body(request)
        pid = str(data.get("person_id", ""))
        if pid not in {p for p, _ in PERSONAS}:
            raise ApiError(400, "profil de démonstration inconnu")
        actor = await anyio.to_thread.run_sync(with_conn, lambda c: load_actor(c, pid))
        return JSONResponse({"token": signer.issue(pid), "actor": _actor_json(actor)})

    # ---------------------------------------------------------------- authenticated

    async def me(request: Request) -> Response:
        pid = actor_id(request)
        actor = await anyio.to_thread.run_sync(with_conn, lambda c: load_actor(c, pid))
        return JSONResponse({"actor": _actor_json(actor)})

    async def new_session(request: Request) -> Response:
        pid = actor_id(request)
        sid = await anyio.to_thread.run_sync(
            with_conn, lambda c: rt.agent(c, llm=_NoLLM(rt.settings.model)).new_session(pid))
        return JSONResponse({"session_id": sid})

    async def post_message(request: Request) -> Response:
        pid = actor_id(request)
        limiter.check(f"{pid}:{request.client.host if request.client else '-'}")
        text = str((await body(request)).get("text", "")).strip()
        if not text:
            raise ApiError(400, "message vide")
        sid = request.path_params["sid"]

        def run(conn):
            try:
                return rt.agent(conn, llm=make_llm()).run(sid, pid, text).as_dict()
            except KeyError as exc:
                raise ApiError(404, "conversation inconnue") from exc
        return JSONResponse(await anyio.to_thread.run_sync(with_conn, run))

    async def decide(request: Request) -> Response:
        pid = actor_id(request)
        limiter.check(f"{pid}:{request.client.host if request.client else '-'}")
        data = await body(request)
        if not isinstance(data.get("confirm"), bool):
            raise ApiError(400, "« confirm » doit valoir true ou false")
        sid, aid = request.path_params["sid"], request.path_params["aid"]

        def run(conn):
            try:
                return rt.agent(conn, llm=make_llm()).decide(sid, pid, aid, data["confirm"]).as_dict()
            except KeyError as exc:
                raise ApiError(404, "conversation inconnue") from exc
        return JSONResponse(await anyio.to_thread.run_sync(with_conn, run))

    async def history(request: Request) -> Response:
        pid = actor_id(request)
        sid = request.path_params["sid"]

        def run(conn):
            agent = rt.agent(conn, llm=_NoLLM(rt.settings.model))
            try:
                msgs = agent.history(sid, pid)
            except KeyError as exc:
                raise ApiError(404, "conversation inconnue") from exc
            ctx = rt.context(conn, pid, session_id=sid)
            return {"messages": msgs, "pending_actions": tools.list_pending(ctx, sid)}
        return JSONResponse(await anyio.to_thread.run_sync(with_conn, run))

    async def calendar_view(request: Request) -> Response:
        pid = actor_id(request)
        start = request.query_params.get("start") or monday_of(rt.clock.now().date()).isoformat()

        def run(conn):
            from .clock import parse_date
            end = (parse_date(start) + timedelta(days=4)).isoformat()
            res = tools.execute(rt.context(conn, pid), "get_schedule", {"start_date": start, "end_date": end})
            return {**res.content, "now": rt.clock.now().isoformat(timespec="minutes")}
        return JSONResponse(await anyio.to_thread.run_sync(with_conn, run))

    async def inbox(request: Request) -> Response:
        pid = actor_id(request)
        res = await anyio.to_thread.run_sync(
            with_conn, lambda c: tools.execute(rt.context(c, pid), "get_messages", {"limit": 15}).content)
        return JSONResponse(res)

    async def certificate(request: Request) -> Response:
        pid = actor_id(request)
        ref = request.path_params["ref"]
        path = await anyio.to_thread.run_sync(
            with_conn, lambda c: academics.certificate_path(rt.context(c, pid), ref))
        if not Path(path).is_file():
            raise NotFound("fichier introuvable")
        return FileResponse(path, media_type="application/pdf", filename=f"attestation-{ref}.pdf")

    async def audit_log(request: Request) -> Response:
        pid = actor_id(request)

        def run(conn):
            ctx = rt.context(conn, pid)
            if not ctx.actor.is_staff:
                return {"entries": audit.recent(ctx, 30, actor_id=pid)}   # one's own trail only
            return {"entries": audit.recent(ctx, 50)}
        return JSONResponse(await anyio.to_thread.run_sync(with_conn, run))

    routes = [
        Route("/", endpoint(index)),
        Route("/api/health", endpoint(health)),
        Route("/api/personas", endpoint(personas)),
        Route("/api/login", endpoint(login), methods=["POST"]),
        Route("/api/me", endpoint(me)),
        Route("/api/sessions", endpoint(new_session), methods=["POST"]),
        Route("/api/sessions/{sid}/messages", endpoint(post_message), methods=["POST"]),
        Route("/api/sessions/{sid}/actions/{aid:int}", endpoint(decide), methods=["POST"]),
        Route("/api/sessions/{sid}/history", endpoint(history)),
        Route("/api/calendar", endpoint(calendar_view)),
        Route("/api/messages", endpoint(inbox)),
        Route("/api/certificates/{ref}", endpoint(certificate)),
        Route("/api/audit", endpoint(audit_log)),
        Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
    ]
    app = Starlette(routes=routes, middleware=[Middleware(SecurityHeaders)])
    return app


class SecurityHeaders:
    """Pure ASGI middleware adding conservative security headers to every response."""

    HEADERS = {
        "x-content-type-options": "nosniff",
        "referrer-policy": "no-referrer",
        "content-security-policy": ("default-src 'self'; style-src 'self' https://fonts.googleapis.com; "
                                    "font-src https://fonts.gstatic.com; img-src 'self' data:; frame-ancestors 'none'"),
    }

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in self.HEADERS.items():
                    headers.setdefault(key, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _NoLLM:
    """Stands in for the model client where no model call happens (creating a session, reading history)."""

    def __init__(self, model: str):
        self.model = model

    def create(self, **_kwargs):
        raise LLMError("no model call expected here")


def _actor_json(actor) -> dict:
    return {"id": actor.id, "name": actor.name, "role": actor.role, "group": actor.group_id,
            "department": actor.department, "title": actor.title}


def __getattr__(name: str):
    """``uvicorn campus_agent.web:app`` builds the app on first access, not at import time."""
    if name == "app":
        logging.basicConfig(level=logging.INFO)
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
