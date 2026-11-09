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

from . import db, tools
from .cli import PERSONAS
from .clock import monday_of, parse_date
from .db import get_meta, set_meta
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
    """Signed, expiring tokens carrying the person and, in the public demo, the visitor's school copy."""

    def __init__(self, secret: str):
        if not secret:
            secret = secrets.token_hex(32)
            log.warning("CAMPUS_SECRET_KEY is empty: using a random key (tokens reset at each restart)")
        self.key = secret.encode()

    def _sign(self, payload: str) -> str:
        return hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()[:32]

    def issue(self, person_id: str, sandbox: str = "", ttl: int = TOKEN_TTL_SECONDS) -> str:
        payload = f"{person_id}|{sandbox}|{int(time.time()) + ttl}"
        return base64.urlsafe_b64encode(f"{payload}|{self._sign(payload)}".encode()).decode().rstrip("=")

    def verify(self, token: str) -> tuple[str, str]:
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
            person_id, sandbox, expiry, sig = raw.split("|")
        except Exception as exc:  # noqa: BLE001
            raise ApiError(401, "jeton invalide") from exc
        if not hmac.compare_digest(sig, self._sign(f"{person_id}|{sandbox}|{expiry}")):
            raise ApiError(401, "jeton invalide")
        if int(expiry) < time.time():
            raise ApiError(401, "session expirée, reconnecte-toi")
        return person_id, sandbox


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
    settings = rt.settings
    signer = Signer(settings.secret_key)
    limiter = RateLimiter(settings.rate_limit_per_minute)
    make_llm = llm_factory or (lambda: rt.llm())
    demo = rt.sandboxes is not None

    def visitor(request: Request) -> tuple[str, str]:
        """(person id, sandbox id) from the bearer token; refuses an expired school copy."""
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise ApiError(401, "connexion requise")
        pid, sandbox = signer.verify(header[7:].strip())
        if demo and rt.sandboxes.directory(sandbox) is None:
            raise ApiError(401, "ta session de démonstration a expiré : choisis à nouveau un profil")
        return pid, sandbox

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

    async def run_with(sandbox: str | None, fn):
        def work():
            conn = rt.connect(sandbox)
            try:
                return fn(conn)
            finally:
                conn.close()
        return await anyio.to_thread.run_sync(work)

    def quota(conn) -> dict | None:
        """Per-visitor allowance in the public demo: a number of messages and a cost ceiling."""
        if not demo:
            return None
        used = int(get_meta(conn, "visitor_messages", "0") or 0)
        cost = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM agent_sessions").fetchone()[0]
        exhausted = used >= settings.visitor_message_limit or cost >= settings.visitor_budget_usd
        return {"messages_used": used, "messages_limit": settings.visitor_message_limit,
                "remaining": 0 if exhausted else settings.visitor_message_limit - used}

    def check_quota(conn) -> None:
        q = quota(conn)
        if q is not None and q["remaining"] <= 0:
            raise ApiError(429, f"Tu as utilisé les {q['messages_limit']} messages de cette démonstration. "
                                "Le code complet est sur GitHub pour aller plus loin.")

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
            except KeyError:
                return JSONResponse({"error": "ta session de démonstration a expiré : choisis à nouveau un profil"},
                                    status_code=401)
            except Exception:  # noqa: BLE001
                log.exception("unhandled error on %s", request.url.path)
                return JSONResponse({"error": "erreur interne"}, status_code=500)
        return wrapped

    # ---------------------------------------------------------------- public

    async def index(_request: Request) -> Response:
        return FileResponse(STATIC_DIR / "index.html", headers={"cache-control": "no-cache"})

    async def health(_request: Request) -> Response:
        def check():
            return {"ok": True, "model": settings.model, "agent_configured": bool(settings.anthropic_api_key),
                    "now": rt.clock.now().isoformat(timespec="minutes"), "demo": demo,
                    "spent_today_usd": round(rt.spent_today(), 4), "daily_budget_usd": settings.daily_budget_usd}
        return JSONResponse(await anyio.to_thread.run_sync(check))

    async def personas(_request: Request) -> Response:
        if not settings.demo_login:
            raise ApiError(404, "connexion de démonstration désactivée")

        def load(conn):
            return [{"id": a.id, "name": a.name, "role": a.role, "label": label}
                    for a, label in ((load_actor(conn, pid), label) for pid, label in PERSONAS)]
        conn = db.connect(rt.db_path)          # the template (or the only database outside the demo)
        try:
            return JSONResponse({"personas": load(conn)})
        finally:
            conn.close()

    async def login(request: Request) -> Response:
        if not settings.demo_login:
            raise ApiError(404, "connexion de démonstration désactivée")
        data = await body(request)
        pid = str(data.get("person_id", ""))
        if pid not in {p for p, _ in PERSONAS}:
            raise ApiError(400, "profil de démonstration inconnu")
        sandbox = ""
        if demo:
            # Changing persona keeps the same school copy, so a teacher's change shows up for the student.
            try:
                _, previous = signer.verify(str(data.get("previous_token", "")))
                sandbox = previous if rt.sandboxes.directory(previous) else ""
            except ApiError:
                sandbox = ""
            if not sandbox:
                sandbox = await anyio.to_thread.run_sync(rt.sandboxes.create)
        ttl = int(settings.sandbox_ttl_hours * 3600) if demo else TOKEN_TTL_SECONDS
        actor = await run_with(sandbox, lambda c: load_actor(c, pid))
        return JSONResponse({"token": signer.issue(pid, sandbox, ttl), "actor": _actor_json(actor)})

    # ---------------------------------------------------------------- authenticated

    async def me(request: Request) -> Response:
        pid, sandbox = visitor(request)
        return JSONResponse(await run_with(sandbox, lambda c: {"actor": _actor_json(load_actor(c, pid)),
                                                               "quota": quota(c)}))

    async def new_session(request: Request) -> Response:
        pid, sandbox = visitor(request)
        sid = await run_with(sandbox, lambda c: rt.agent(c, llm=_NoLLM(settings.model), sandbox=sandbox)
                             .new_session(pid))
        return JSONResponse({"session_id": sid})

    async def post_message(request: Request) -> Response:
        pid, sandbox = visitor(request)
        limiter.check(f"{pid}:{sandbox}:{request.client.host if request.client else '-'}")
        text = str((await body(request)).get("text", "")).strip()
        if not text:
            raise ApiError(400, "message vide")
        sid = request.path_params["sid"]

        def run(conn):
            check_quota(conn)
            try:
                turn = rt.agent(conn, llm=make_llm(), sandbox=sandbox).run(sid, pid, text).as_dict()
            except KeyError as exc:
                raise ApiError(404, "conversation inconnue") from exc
            if demo:
                set_meta(conn, "visitor_messages", str(int(get_meta(conn, "visitor_messages", "0") or 0) + 1))
                conn.commit()
            return {**turn, "quota": quota(conn)}
        return JSONResponse(await run_with(sandbox, run))

    async def decide(request: Request) -> Response:
        pid, sandbox = visitor(request)
        limiter.check(f"{pid}:{sandbox}:{request.client.host if request.client else '-'}")
        data = await body(request)
        if not isinstance(data.get("confirm"), bool):
            raise ApiError(400, "« confirm » doit valoir true ou false")
        sid, aid = request.path_params["sid"], request.path_params["aid"]

        def run(conn):
            try:
                turn = rt.agent(conn, llm=make_llm(), sandbox=sandbox).decide(sid, pid, aid, data["confirm"])
            except KeyError as exc:
                raise ApiError(404, "conversation inconnue") from exc
            return {**turn.as_dict(), "quota": quota(conn)}
        return JSONResponse(await run_with(sandbox, run))

    async def history(request: Request) -> Response:
        pid, sandbox = visitor(request)
        sid = request.path_params["sid"]

        def run(conn):
            agent = rt.agent(conn, llm=_NoLLM(settings.model), sandbox=sandbox)
            try:
                msgs = agent.history(sid, pid)
            except KeyError as exc:
                raise ApiError(404, "conversation inconnue") from exc
            ctx = rt.context(conn, pid, session_id=sid, sandbox=sandbox)
            return {"messages": msgs, "pending_actions": tools.list_pending(ctx, sid), "quota": quota(conn)}
        return JSONResponse(await run_with(sandbox, run))

    async def calendar_view(request: Request) -> Response:
        pid, sandbox = visitor(request)
        start = request.query_params.get("start") or monday_of(rt.clock.now().date()).isoformat()

        def run(conn):
            end = (parse_date(start) + timedelta(days=4)).isoformat()
            res = tools.execute(rt.context(conn, pid, sandbox=sandbox), "get_schedule",
                                {"start_date": start, "end_date": end})
            return {**res.content, "now": rt.clock.now().isoformat(timespec="minutes")}
        return JSONResponse(await run_with(sandbox, run))

    async def inbox(request: Request) -> Response:
        pid, sandbox = visitor(request)
        return JSONResponse(await run_with(sandbox, lambda c: tools.execute(
            rt.context(c, pid, sandbox=sandbox), "get_messages", {"limit": 15}).content))

    async def certificate(request: Request) -> Response:
        pid, sandbox = visitor(request)
        ref = request.path_params["ref"]
        path = await run_with(sandbox, lambda c: academics.certificate_path(rt.context(c, pid, sandbox=sandbox), ref))
        if not Path(path).is_file():
            raise NotFound("fichier introuvable")
        return FileResponse(path, media_type="application/pdf", filename=f"attestation-{ref}.pdf")

    async def audit_log(request: Request) -> Response:
        pid, sandbox = visitor(request)

        def run(conn):
            ctx = rt.context(conn, pid, sandbox=sandbox)
            if not ctx.actor.is_staff:
                return {"entries": audit.recent(ctx, 30, actor_id=pid)}   # one's own trail only
            return {"entries": audit.recent(ctx, 50)}
        return JSONResponse(await run_with(sandbox, run))

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
    return Starlette(routes=routes, middleware=[Middleware(SecurityHeaders, frame_ancestors=settings.frame_ancestors)])


class SecurityHeaders:
    """Pure ASGI middleware adding conservative security headers to every response."""

    def __init__(self, app, frame_ancestors: str = "'none'"):
        self.app = app
        # frame-ancestors is configurable so a host page (e.g. a Hugging Face Space) may embed the demo.
        self.HEADERS = {
            "x-content-type-options": "nosniff",
            "referrer-policy": "no-referrer",
            "content-security-policy": ("default-src 'self'; style-src 'self' https://fonts.googleapis.com; "
                                        "font-src https://fonts.gstatic.com; img-src 'self' data:; "
                                        f"frame-ancestors {frame_ancestors}"),
        }

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
