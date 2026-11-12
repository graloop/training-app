"""Who is calling /mcp: the bearer token of each request -> the connector's account.

BearerAuth wraps the MCP server (main.py). Without a valid access token it
answers 401 with a WWW-Authenticate header naming the metadata URL: that
header is how Claude finds the login (oauth.py). With one, the account's id
is put in `current_user_id`, which the tools read (server.py).

Also here, because oauth.py needs the same answers: which account may use the
connector, how tokens are hashed, and the app's public address.
"""

import contextvars
import datetime
import hashlib
import logging

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .. import models
from ..config import get_settings
from ..database import SessionLocal

settings = get_settings()
log = logging.getLogger("trainer.mcp")

# Set by BearerAuth for the length of one /mcp request; tools read it through server.py.
current_user_id: contextvars.ContextVar[int | None] = contextvars.ContextVar("mcp_user_id", default=None)


def is_configured() -> bool:
    """Whether main.py mounts /mcp and the OAuth pages at all."""
    return settings.mcp_enabled and bool(settings.mcp_username)


def may_connect(user: models.User | None) -> bool:
    """Only MCP_USERNAME, and only while it is a regular account (admins have no calendar)."""
    return user is not None and user.username == settings.mcp_username and not user.is_admin


def log_setup_problems() -> None:
    """Startup (main.py): say in the log why the connector can't work, without stopping the app."""
    if not settings.mcp_enabled:
        return
    if not settings.mcp_username:
        log.error("MCP_ENABLED is set but MCP_USERNAME is empty: /mcp is not available. Set it to your username.")
        return
    with SessionLocal() as db:
        user = db.query(models.User).filter(models.User.username == settings.mcp_username).first()
    if user is None:
        log.error("MCP_USERNAME %r is not an account yet: the connector can't log in until it exists.", settings.mcp_username)
    elif user.is_admin:
        log.error(
            "MCP_USERNAME %r is an admin account, which has no calendar: the connector refuses it. "
            "Use a regular account.",
            settings.mcp_username,
        )
    else:
        log.info("Claude connector on at /mcp for account %r", settings.mcp_username)


def public_base(request: Request) -> str:
    """ "https://training.example.com": the address Claude reaches the app at.
    Without APP_DOMAIN (local testing) it is whatever address the request used."""
    if settings.app_domain:
        return f"https://{settings.app_domain}"
    return str(request.base_url).rstrip("/")


def token_hash(value: str) -> str:
    """What is stored instead of a code or token (they are long random strings, so a plain hash is enough)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def live_token(db, value: str, kind: str) -> models.McpToken | None:
    """The stored token if it still works: right kind, not expired or replaced,
    and its account may still connect with the password it had then."""
    now = models.utcnow()
    row = (
        db.query(models.McpToken)
        .filter(
            models.McpToken.token_hash == token_hash(value),
            models.McpToken.kind == kind,
            models.McpToken.revoked_at.is_(None),
            models.McpToken.expires_at > now,
        )
        .first()
    )
    if row is None:
        return None
    user = db.get(models.User, row.user_id)
    if not may_connect(user) or (user.token_version or 0) != row.token_version:
        return None
    return row


def _user_id_for(access_token: str) -> int | None:
    with SessionLocal() as db:
        row = live_token(db, access_token, "access")
        return row.user_id if row else None


class BearerAuth:
    """ASGI wrapper around the MCP server: lets a request through only with a valid access token."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope)
        # Some clients probe before logging in: answer without asking for a token.
        if request.method == "OPTIONS":
            await Response(status_code=204, headers={"Allow": "GET, POST, DELETE, OPTIONS"})(scope, receive, send)
            return
        if request.method == "HEAD":
            await Response(status_code=200)(scope, receive, send)
            return

        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        user_id = None
        if scheme.lower() == "bearer" and token.strip():
            user_id = await anyio.to_thread.run_sync(_user_id_for, token.strip())  # database work, off the event loop
        if user_id is None:
            metadata = f"{public_base(request)}/.well-known/oauth-protected-resource"
            response = JSONResponse(
                {"error": "invalid_token", "error_description": "Log in to the Training App to connect."},
                status_code=401,
                headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata}"'},
            )
            await response(scope, receive, send)
            return

        marker = current_user_id.set(user_id)
        try:
            await self.app(scope, receive, send)
        finally:
            current_user_id.reset(marker)


def expiry(**delta) -> datetime.datetime:
    """A moment from now, for expires_at columns: expiry(minutes=5)."""
    return models.utcnow() + datetime.timedelta(**delta)
