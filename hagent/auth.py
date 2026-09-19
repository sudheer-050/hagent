"""Accounts, tokens and the request gate for using Hagent from more than one device.

Trust model
-----------
* With no users, Hagent behaves as before: it serves only loopback clients and refuses anything
  that arrived through a proxy (a proxy on this machine would otherwise make remote traffic
  look local).
* Once a user exists, every HTTP and WebSocket request needs a valid browser session or an
  `Authorization: Bearer` token, except login, health, static files and token-protected webhooks.
* Accounts are instance-wide. A logged-in user can use everything the dashboard offers,
  including agent terminals, so treat a token like shell access to this machine. Serve it over
  HTTPS (or a private network such as Tailscale); Hagent does not terminate TLS itself.
"""

import hashlib
import hmac
import ipaddress
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from hagent import db as hagent_db
from hagent.models import AuthToken, User

SESSION_COOKIE = "hagent_session"
SESSION_DAYS = 30
MIN_PASSWORD_LENGTH = 10
TOKEN_PREFIX = "hag_"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}  # "testclient" is Starlette's fake peer
_FORWARDING_HEADERS = (b"x-forwarded-for", b"x-forwarded-host", b"forwarded", b"x-real-ip")
_CACHE_TTL = 15.0  # seconds a validated token is trusted before the database is asked again
_ENABLED_TTL = 5.0


class AuthError(ValueError):
    """A user-facing account or credential problem."""


@dataclass(frozen=True)
class AuthUser:
    id: str
    username: str
    role: str
    kind: str  # "api", "session" or "worker"
    token_id: str
    token_name: str = ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _session():
    # Unscoped: accounts are not tied to a workspace.
    return hagent_db.SessionLocal()


# --- passwords --------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=len(digest_hex) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


_DUMMY_HASH = hash_password(secrets.token_hex(8))  # burns the same time for unknown usernames


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")


def _validate_username(username: str) -> str:
    username = username.strip()
    if not username or len(username) > 64 or any(ch.isspace() for ch in username):
        raise AuthError("Username must be 1-64 characters with no spaces")
    return username


# --- state caches -----------------------------------------------------------

_lock = threading.Lock()
_enabled_cache: tuple[bool, float] | None = None
_token_cache: dict[str, tuple[AuthUser | None, float]] = {}
_last_used_written: dict[str, float] = {}


def invalidate_caches() -> None:
    global _enabled_cache
    with _lock:
        _enabled_cache = None
        _token_cache.clear()


def auth_enabled() -> bool:
    """True once any user exists (or HAGENT_REQUIRE_AUTH is set). Cached for a few seconds."""
    global _enabled_cache
    if os.environ.get("HAGENT_REQUIRE_AUTH", "").lower() in {"1", "true", "yes"}:
        return True
    with _lock:
        cached = _enabled_cache
    if cached and cached[1] > time.monotonic():
        return cached[0]
    try:
        with _session() as s:
            enabled = bool(s.scalar(select(func.count()).select_from(User)))
    except OperationalError:
        return False  # accounts table not created yet: no accounts, so loopback-only; retry next request
    with _lock:
        _enabled_cache = (enabled, time.monotonic() + _ENABLED_TTL)
    return enabled


# --- users and tokens -------------------------------------------------------

def create_user(session, username: str, password: str, role: str = "member") -> User:
    username = _validate_username(username)
    validate_password(password)
    if role not in {"owner", "member"}:
        raise AuthError("Role must be 'owner' or 'member'")
    if session.scalar(select(User).where(func.lower(User.username) == username.lower())):
        raise AuthError(f"User '{username}' already exists")
    first_user = not session.scalar(select(func.count()).select_from(User))
    user = User(username=username, password_hash=hash_password(password), role="owner" if first_user else role)
    session.add(user)
    session.commit()
    invalidate_caches()
    return user


def set_password(session, username: str, password: str) -> None:
    validate_password(password)
    user = session.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if not user:
        raise AuthError(f"User '{username}' not found")
    user.password_hash = hash_password(password)
    # A password change signs out every browser session and leaves API tokens alone.
    for token in session.scalars(select(AuthToken).where(AuthToken.user_id == user.id, AuthToken.kind == "session", AuthToken.revoked_at.is_(None))):
        token.revoked_at = _now()
    session.commit()
    invalidate_caches()


def remove_user(session, username: str) -> None:
    user = session.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if not user:
        raise AuthError(f"User '{username}' not found")
    if user.role == "owner" and (session.scalar(select(func.count()).select_from(User).where(User.role == "owner")) or 0) <= 1:
        raise AuthError("Cannot remove the last owner")
    for token in session.scalars(select(AuthToken).where(AuthToken.user_id == user.id)):
        session.delete(token)
    session.delete(user)
    session.commit()
    invalidate_caches()


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def issue_token(session, user: User, kind: str = "api", name: str = "", ttl_days: int | None = None) -> tuple[str, AuthToken]:
    """Create a credential. The returned secret is shown once; only its hash is stored."""
    if kind not in {"api", "session", "worker"}:
        raise AuthError("Unknown token kind")
    if kind == "worker" and not name.strip():
        raise AuthError("A worker token needs --name: the name of the worker device it may act as")
    secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = AuthToken(
        user_id=user.id, kind=kind, name=name, token_hash=_hash_secret(secret), prefix=secret[: len(TOKEN_PREFIX) + 4],
        expires_at=_now() + timedelta(days=ttl_days) if ttl_days else None,
    )
    session.add(row)
    session.commit()
    return secret, row


def revoke_token(session, token_id: str) -> None:
    row = session.get(AuthToken, token_id)
    if not row:
        matches = session.scalars(select(AuthToken).where(AuthToken.id.like(f"{token_id}%"))).all()
        row = matches[0] if len(matches) == 1 else None
    if not row:
        raise AuthError(f"Token '{token_id}' not found")
    row.revoked_at = row.revoked_at or _now()
    session.commit()
    invalidate_caches()


def authenticate_secret(secret: str) -> AuthUser | None:
    """Resolve a bearer/session secret to a user, or None. Cached briefly for speed."""
    if not secret or len(secret) > 256:
        return None
    token_hash = _hash_secret(secret)
    now = time.monotonic()
    with _lock:
        cached = _token_cache.get(token_hash)
    if cached and cached[1] > now:
        return cached[0]
    try:
        return _lookup_token(secret, token_hash, now)
    except OperationalError:
        return None


def _lookup_token(secret: str, token_hash: str, now: float) -> AuthUser | None:
    with _session() as s:
        row = s.execute(
            select(AuthToken, User)
            .join(User, User.id == AuthToken.user_id)
            .where(
                AuthToken.token_hash == token_hash,
                AuthToken.revoked_at.is_(None),
                (AuthToken.expires_at.is_(None)) | (AuthToken.expires_at > _now()),
            )
        ).first()
        user = AuthUser(row[1].id, row[1].username, row[1].role, row[0].kind, row[0].id, row[0].name) if row else None
        if row and _last_used_written.get(row[0].id, 0) + 60 < now:
            row[0].last_used_at = _now()
            s.commit()
            _last_used_written[row[0].id] = now
    with _lock:
        if len(_token_cache) > 1000:
            _token_cache.clear()
        _token_cache[token_hash] = (user, now + _CACHE_TTL)
    return user


def authenticate_password(username: str, password: str) -> User | None:
    with _session() as s:
        user = s.scalar(select(User).where(func.lower(User.username) == username.strip().lower()))
        ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)
        return user if user and ok else None


# --- login throttling -------------------------------------------------------

_failures: dict[tuple[str, str], list[float]] = {}
MAX_FAILURES = 5
FAILURE_WINDOW = 300.0


def login_blocked(ip: str, username: str) -> bool:
    key = (ip, username.strip().lower())
    with _lock:
        recent = [t for t in _failures.get(key, []) if t > time.monotonic() - FAILURE_WINDOW]
        _failures[key] = recent
        return len(recent) >= MAX_FAILURES


def record_login_failure(ip: str, username: str) -> None:
    with _lock:
        _failures.setdefault((ip, username.strip().lower()), []).append(time.monotonic())


def clear_login_failures(ip: str, username: str) -> None:
    with _lock:
        _failures.pop((ip, username.strip().lower()), None)


# --- the request gate -------------------------------------------------------

_EXEMPT_EXACT = {"/login", "/healthz", "/.well-known/agent-card.json"}
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _is_exempt(path: str) -> bool:
    # Webhook URLs carry their own secret token.
    return path in _EXEMPT_EXACT or path.startswith("/static/") or path.startswith("/webhooks/")


def _bearer(headers: dict[bytes, bytes]) -> str | None:
    value = headers.get(b"authorization", b"").decode("latin-1")
    scheme, _, secret = value.partition(" ")
    return secret.strip() if scheme.lower() == "bearer" and secret.strip() else None


def _cookie(headers: dict[bytes, bytes], name: str) -> str | None:
    for part in headers.get(b"cookie", b"").decode("latin-1").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name and value:
            return value
    return None


def safe_next(target: str | None) -> str:
    """Only allow same-site paths as a post-login destination."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target and "\n" not in target and "\r" not in target:
        return target
    return "/"


class AuthMiddleware:
    """Pure ASGI middleware so it covers WebSockets (the agent terminal) as well as HTTP."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path == "/healthz" or (path.startswith("/static/") and scope["type"] == "http"):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])

        if not auth_enabled():
            client = scope.get("client")
            local = bool(client) and client[0] in _LOOPBACK_HOSTS
            proxied = any(name in headers for name in _FORWARDING_HEADERS)
            if local and not proxied:
                return await self.app(scope, receive, send)
            return await self._deny(scope, receive, send, 403, "No account exists yet. Create one on the machine running Hagent: hagent auth user-create")

        if _is_exempt(path) and scope["type"] == "http":
            return await self.app(scope, receive, send)

        secret = _bearer(headers)
        via_cookie = False
        if secret is None:
            secret, via_cookie = _cookie(headers, SESSION_COOKIE), True
        user = authenticate_secret(secret) if secret else None
        if user is None:
            return await self._unauthenticated(scope, receive, send, headers, path)
        if user.kind == "worker" and not path.startswith("/api/worker/"):
            return await self._deny(scope, receive, send, 403, "Worker tokens can only be used for worker endpoints")
        if user.kind != "worker" and path.startswith("/api/worker/") and path != "/api/worker/ping":
            return await self._deny(scope, receive, send, 403, "Worker endpoints need a worker token")
        if via_cookie and scope["type"] == "http" and scope.get("method") in _UNSAFE_METHODS:
            origin = headers.get(b"origin", b"").decode("latin-1")
            host = headers.get(b"host", b"").decode("latin-1")
            if origin and urlsplit(origin).netloc != host:
                return await self._deny(scope, receive, send, 403, "Cross-origin request refused")
        scope.setdefault("state", {})["user"] = user
        return await self.app(scope, receive, send)

    async def _unauthenticated(self, scope, receive, send, headers, path):
        wants_json = path.startswith("/api/") or b"authorization" in headers or b"application/json" in headers.get(b"accept", b"")
        if scope["type"] == "http" and not wants_json:
            query = scope.get("query_string", b"").decode("latin-1")
            target = quote(path + (f"?{query}" if query else ""), safe="/")
            response = RedirectResponse(f"/login?next={target}", status_code=303)
            return await response(scope, receive, send)
        return await self._deny(scope, receive, send, 401, "Authentication required")

    async def _deny(self, scope, receive, send, status, message):
        if scope["type"] == "websocket":
            await receive()  # the connect message
            return await send({"type": "websocket.close", "code": 1008})
        response = JSONResponse({"error": message}, status_code=status, headers={"WWW-Authenticate": "Bearer"} if status == 401 else None)
        return await response(scope, receive, send)


# --- login / logout routes --------------------------------------------------

router = APIRouter()

_LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in - Hagent</title><style>
:root{color-scheme:light dark;--bg:#f6f7f9;--fg:#15181d;--card:#fff;--line:#d9dde3;--accent:#3a5bd9}
@media(prefers-color-scheme:dark){:root{--bg:#101215;--fg:#e9ebee;--card:#1a1d22;--line:#2c3037;--accent:#7b93ff}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);font:16px system-ui,sans-serif}
form{width:min(360px,calc(100vw - 32px));background:var(--card);border:1px solid var(--line);border-radius:12px;padding:24px;display:grid;gap:12px}
h1{margin:0 0 4px;font-size:1.25rem}label{font-size:.85rem}input{width:100%;box-sizing:border-box;padding:10px;border:1px solid var(--line);border-radius:8px;background:transparent;color:inherit;font:inherit}
button{padding:10px;border:0;border-radius:8px;background:var(--accent);color:#fff;font:inherit;cursor:pointer}.err{color:#c62828;font-size:.9rem}
</style></head><body><form method="post" action="/login"><h1>Sign in to Hagent</h1>__ERROR__
<div><label for="u">Username</label><input id="u" name="username" autocomplete="username" required autofocus></div>
<div><label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" required></div>
<input type="hidden" name="next" value="__NEXT__"><button type="submit">Sign in</button></form></body></html>"""


def _page(next_url: str, error: str = "", status: int = 200) -> HTMLResponse:
    from html import escape

    body = _LOGIN_PAGE.replace("__ERROR__", f'<div class="err" role="alert">{escape(error)}</div>' if error else "").replace("__NEXT__", escape(safe_next(next_url), quote=True))
    return HTMLResponse(body, status_code=status)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/login")
def login_page(next: str = "/"):
    if not auth_enabled():
        return RedirectResponse("/", status_code=303)
    return _page(next)


@router.post("/login")
def login_submit(request: Request, username: str = Form(""), password: str = Form(""), next: str = Form("/")):
    if not auth_enabled():
        return RedirectResponse("/", status_code=303)
    ip = _client_ip(request)
    if login_blocked(ip, username):
        return _page(next, "Too many failed attempts. Try again in a few minutes.", 429)
    user = authenticate_password(username, password)
    if not user:
        record_login_failure(ip, username)
        return _page(next, "Incorrect username or password.", 401)
    clear_login_failures(ip, username)
    with _session() as s:
        secret, _row = issue_token(s, s.get(User, user.id), kind="session", name=request.headers.get("user-agent", "")[:80], ttl_days=SESSION_DAYS)
    response = RedirectResponse(safe_next(next), status_code=303)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(SESSION_COOKIE, secret, max_age=SESSION_DAYS * 86400, httponly=True, samesite="strict", secure=secure, path="/")
    return response


@router.post("/logout")
def logout(request: Request):
    secret = request.cookies.get(SESSION_COOKIE)
    if secret:
        with _session() as s:
            row = s.scalar(select(AuthToken).where(AuthToken.token_hash == _hash_secret(secret)))
            if row and not row.revoked_at:
                row.revoked_at = _now()
                s.commit()
        invalidate_caches()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


def is_loopback_host(host: str) -> bool:
    """Used by `hagent serve` to decide whether a bind address needs accounts first."""
    if host in {"localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
