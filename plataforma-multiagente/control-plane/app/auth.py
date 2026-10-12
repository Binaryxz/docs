"""Login do painel: sessão em cookie assinado (HMAC) + token de API para automações (n8n)."""

import base64
import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request

from . import db
from .config import settings

COOKIE = "mas_session"
SESSION_TTL_S = 7 * 24 * 3600


def auth_enabled() -> bool:
    return bool(settings.admin_password or settings.admin_token)


def _secret() -> bytes:
    if settings.session_secret:
        return settings.session_secret.encode()
    rows = db.query("SELECT value FROM kv WHERE key = 'session_secret'")
    if rows:
        return rows[0]["value"].encode()
    value = secrets.token_hex(32)
    db.execute("INSERT OR IGNORE INTO kv (key, value) VALUES ('session_secret', ?)", (value,))
    return db.query("SELECT value FROM kv WHERE key = 'session_secret'")[0]["value"].encode()


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def make_session(user: str) -> str:
    payload = base64.urlsafe_b64encode(f"{user}|{int(time.time()) + SESSION_TTL_S}".encode()).decode()
    return f"{payload}.{_sign(payload)}"


def read_session(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        user, exp = base64.urlsafe_b64decode(payload.encode()).decode().split("|", 1)
    except (ValueError, UnicodeDecodeError):
        return None
    return user if int(exp) > time.time() else None


def check_password(user: str, password: str) -> bool:
    if not settings.admin_password:
        return False
    ok_user = hmac.compare_digest(user.strip().lower(), settings.admin_user.lower())
    ok_pass = hmac.compare_digest(password, settings.admin_password)
    return ok_user and ok_pass


def current_user(request: Request) -> str | None:
    if not auth_enabled():
        return settings.admin_user
    user = read_session(request.cookies.get(COOKIE))
    if user:
        return user
    header = request.headers.get("authorization", "")
    if settings.admin_token and hmac.compare_digest(header, f"Bearer {settings.admin_token}"):
        return "api"
    return None


def require_user(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise HTTPException(401, "faça login")
    return user
