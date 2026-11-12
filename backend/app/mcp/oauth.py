"""The login Claude goes through to use /mcp: a minimal OAuth 2.1 authorization
server (PKCE, dynamic client registration), small because one account uses it.

What happens when the connector is added in claude.ai:

1. Claude calls /mcp, gets a 401 (auth.py) and reads the two /.well-known
   documents below to learn where to log in.
2. POST /oauth/register: Claude registers itself and gets a client_id. Only
   Claude's own callback addresses are accepted (ALLOWED_REDIRECT_URIS).
3. GET /oauth/authorize: the browser shows this app's login form.
   POST /oauth/authorize: MCP_USERNAME and its password -> back to Claude with
   a one-time code (CODE_MINUTES).
4. POST /oauth/token: Claude trades the code for an access token
   (ACCESS_MINUTES) and a refresh token (MCP_TOKEN_DAYS), and later each
   refresh token for a new pair.

Codes and tokens are stored hashed (models.py). A password change stops every
token, like it logs out the page's sessions.
"""

import base64
import hashlib
import html
import logging
import re
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from .. import accounts, models, visitor
from ..config import get_settings
from ..database import get_db
from ..security import verify_password
from .auth import expiry, live_token, may_connect, public_base, token_hash

settings = get_settings()
log = logging.getLogger("trainer.mcp")
router = APIRouter(tags=["mcp"], include_in_schema=False)

# Where a login may send its code: Claude's own callback, nothing else. So a
# made-up client can't have the code delivered to another site.
ALLOWED_REDIRECT_URIS = {
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
}
CODE_MINUTES = 5  # a login code is traded for tokens right away
ACCESS_MINUTES = 60
# Registering needs no login, so only this many clients are kept: the oldest
# ones without a working token make room (a stranger can't fill the table).
MAX_CLIENTS = 50

# The page's policy (main.CONTENT_SECURITY_POLICY) with one change: Chrome
# applies form-action to the redirect that follows the login form, so it must
# allow going back to Claude. tests/test_mcp_oauth.py keeps the two in step.
AUTHORIZE_CSP = (
    "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
    "form-action 'self' https://claude.ai https://claude.com"
)
_CHALLENGE = re.compile(r"[A-Za-z0-9._~-]{43,128}")  # RFC 7636: a base64url SHA-256 is 43 characters
STATE_MAX = 1024


# --- Discovery -----------------------------------------------------------------------


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource(request: Request):
    """RFC 9728: /mcp is protected, and this same app is where to log in."""
    base = public_base(request)
    return {"resource": f"{base}/mcp", "authorization_servers": [base]}


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/mcp")
def authorization_server(request: Request):
    """RFC 8414: where each step of the login is."""
    base = public_base(request)
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }


def _oauth_error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    """An error the way OAuth clients read it (RFC 6749, 5.2)."""
    return JSONResponse({"error": error, "error_description": description}, status_code=status_code)


# --- Registration ------------------------------------------------------------------


@router.post("/oauth/register")
async def register(request: Request, db: Session = Depends(get_db)):
    """RFC 7591: Claude registers itself. No secret is given: PKCE protects the login."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    uris = body.get("redirect_uris") if isinstance(body, dict) else None
    if not isinstance(uris, list) or not uris or not all(uri in ALLOWED_REDIRECT_URIS for uri in uris):
        return _oauth_error("invalid_redirect_uri", "Only Claude's own callback addresses can be registered.")

    _make_room_for_a_client(db)
    client = models.McpClient(client_id=secrets.token_urlsafe(24), redirect_uris=sorted(set(uris)))
    db.add(client)
    db.commit()
    return JSONResponse(
        {
            "client_id": client.client_id,
            "redirect_uris": client.redirect_uris,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
        status_code=201,
    )


def _make_room_for_a_client(db: Session) -> None:
    """Keep the table under MAX_CLIENTS: drop the oldest clients no working token belongs to."""
    extra = db.query(models.McpClient).count() - MAX_CLIENTS + 1
    if extra <= 0:
        return
    in_use = (
        db.query(models.McpToken.client_id)
        .filter(models.McpToken.revoked_at.is_(None), models.McpToken.expires_at > models.utcnow())
        .distinct()
    )
    unused = (
        db.query(models.McpClient)
        .filter(models.McpClient.client_id.not_in(in_use))
        .order_by(models.McpClient.created_at, models.McpClient.id)
        .limit(extra)
    )
    for client in unused:
        db.delete(client)


# --- The login page ----------------------------------------------------------------


@dataclass
class _LoginRequest:
    """What Claude asks for when it opens the login page; carried through the form as hidden fields."""

    client_id: str
    redirect_uri: str
    response_type: str
    code_challenge: str
    code_challenge_method: str
    state: str

    def problem(self, db: Session) -> str | None:
        """Why this request can't be answered, in words for the page. Nothing is
        ever redirected to an address that fails here."""
        client = db.query(models.McpClient).filter(models.McpClient.client_id == self.client_id).first()
        if client is None:
            return "This connector isn't registered here. Remove it in Claude's settings and add it again."
        if self.redirect_uri not in client.redirect_uris or self.redirect_uri not in ALLOWED_REDIRECT_URIS:
            return "This login was not started by Claude."
        if self.response_type != "code":
            return "Unsupported login request (response_type must be code)."
        if self.code_challenge_method != "S256" or not _CHALLENGE.fullmatch(self.code_challenge):
            return "Unsupported login request (a PKCE S256 code challenge is required)."
        if len(self.state) > STATE_MAX:
            return "Unsupported login request (state is too long)."
        return None


_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Connect Claude · Training App</title>
  <script src="/js/theme-early.js"></script>
  <link rel="stylesheet" href="/css/base.css" />
  <link rel="stylesheet" href="/css/oauth.css" />
</head>
<body class="oauth-page">
  <main class="oauth-card">
    <h1>Connect Claude</h1>
{body}
  </main>
</body>
</html>
"""

_FORM = """    <p class="hint">Log in to let Claude read and edit your training calendar and plan.</p>
    <form method="post" action="/oauth/authorize">
{hidden}
      <label for="username">Username</label>
      <input type="text" id="username" name="username" required autofocus autocomplete="username" autocapitalize="none" spellcheck="false" />
      <label for="password">Password</label>
      <input type="password" id="password" name="password" required autocomplete="current-password" />
      <button type="submit">Log in and connect</button>
{error}
    </form>"""


def _page(body: str, status_code: int) -> HTMLResponse:
    return HTMLResponse(
        _PAGE.format(body=body),
        status_code=status_code,
        headers={"Content-Security-Policy": AUTHORIZE_CSP},
    )


def _problem_page(message: str) -> HTMLResponse:
    return _page(f'    <p class="error" role="alert">{html.escape(message)}</p>', 400)


def _login_page(asked: _LoginRequest, error: str = "", status_code: int = 200) -> HTMLResponse:
    hidden = "\n".join(
        f'      <input type="hidden" name="{name}" value="{html.escape(value, quote=True)}" />'
        for name, value in vars(asked).items()
    )
    error_line = f'      <p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    return _page(_FORM.format(hidden=hidden, error=error_line), status_code)


@router.get("/oauth/authorize")
def authorize_page(
    client_id: str = "",
    redirect_uri: str = "",
    response_type: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "",
    state: str = "",
    db: Session = Depends(get_db),
):
    """The login form Claude opens in the browser."""
    asked = _LoginRequest(client_id, redirect_uri, response_type, code_challenge, code_challenge_method, state)
    if problem := asked.problem(db):
        return _problem_page(problem)
    return _login_page(asked)


@router.post("/oauth/authorize")
def authorize(
    request: Request,
    client_id: str = Form(""),
    redirect_uri: str = Form(""),
    response_type: str = Form(""),
    code_challenge: str = Form(""),
    code_challenge_method: str = Form(""),
    state: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
):
    """Check the password like /api/auth/login does (same lockout, same
    connection log), then send the browser back to Claude with a one-time code."""
    asked = _LoginRequest(client_id, redirect_uri, response_type, code_challenge, code_challenge_method, state)
    if problem := asked.problem(db):
        return _problem_page(problem)

    name = accounts.normalize_username(username)
    address = visitor.address(request)
    wait = max(accounts.seconds_locked(name), accounts.seconds_address_blocked(address))
    if wait:
        return _login_page(asked, f"Too many failed attempts. Try again in {max(1, wait // 60)} min.", 429)

    user = db.query(models.User).filter(models.User.username == name).first()
    password_ok = verify_password(password, user.hashed_password if user else None)
    if not (password_ok and may_connect(user)):
        if password_ok:
            log.warning("Account %r logged in on the connector page, but only MCP_USERNAME may connect", name)
        accounts.record_failure(name, address)
        accounts.record_login(db, user, success=False)
        # One message for both cases, so the page doesn't tell which account the connector belongs to.
        return _login_page(asked, "Wrong username or password, or this account can't use the connector.", 401)

    accounts.clear_failures(name)
    accounts.record_login(db, user, success=True)
    code = secrets.token_urlsafe(32)
    db.add(
        models.McpAuthCode(
            code_hash=token_hash(code),
            client_id=asked.client_id,
            user_id=user.id,
            redirect_uri=asked.redirect_uri,
            code_challenge=asked.code_challenge,
            expires_at=expiry(minutes=CODE_MINUTES),
        )
    )
    db.commit()
    log.info("Connector login by %r: code sent to Claude", name)
    answer = {"code": code, "state": asked.state} if asked.state else {"code": code}
    return RedirectResponse(f"{asked.redirect_uri}?{urlencode(answer)}", status_code=302)


# --- Tokens ------------------------------------------------------------------------


@router.post("/oauth/token")
def token(
    grant_type: str = Form(""),
    code: str = Form(""),
    redirect_uri: str = Form(""),
    client_id: str = Form(""),
    code_verifier: str = Form(""),
    refresh_token: str = Form(""),
    db: Session = Depends(get_db),
):
    """A login code, or the last refresh token, for a new pair of tokens."""
    if grant_type == "authorization_code":
        user_id = _redeem_code(db, code, client_id, redirect_uri, code_verifier)
        if user_id is None:
            db.commit()  # the code stays used
            return _oauth_error("invalid_grant", "The login code is wrong, expired or already used. Connect again.")
    elif grant_type == "refresh_token":
        old = live_token(db, refresh_token, "refresh")
        if old is None or old.client_id != client_id:
            return _oauth_error("invalid_grant", "The connection has expired. Connect again.")
        old.revoked_at = models.utcnow()  # each refresh token works once
        user_id = old.user_id
    else:
        return _oauth_error("unsupported_grant_type", "Use authorization_code or refresh_token.")

    user = db.get(models.User, user_id)
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    for value, kind, expires_at in (
        (access, "access", expiry(minutes=ACCESS_MINUTES)),
        (refresh, "refresh", expiry(days=settings.mcp_token_days)),
    ):
        db.add(
            models.McpToken(
                token_hash=token_hash(value),
                kind=kind,
                user_id=user.id,
                client_id=client_id,
                token_version=user.token_version or 0,
                expires_at=expires_at,
            )
        )
    _forget_expired(db)
    db.commit()
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": ACCESS_MINUTES * 60,
        "refresh_token": refresh,
    }


def _redeem_code(db: Session, code: str, client_id: str, redirect_uri: str, code_verifier: str) -> int | None:
    """The account a login code was given to, if everything about it matches.
    The code is marked used at the first try, right or wrong. The caller commits."""
    row = (
        db.query(models.McpAuthCode)
        .filter(
            models.McpAuthCode.code_hash == token_hash(code),
            models.McpAuthCode.used_at.is_(None),
            models.McpAuthCode.expires_at > models.utcnow(),
        )
        .first()
    )
    if row is None:
        return None
    row.used_at = models.utcnow()
    # PKCE: only who started this login knows the verifier behind the challenge.
    digest = hashlib.sha256(code_verifier.encode("ascii", "ignore")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    if not secrets.compare_digest(challenge, row.code_challenge):
        return None
    if row.client_id != client_id or row.redirect_uri != redirect_uri:
        return None
    if not may_connect(db.get(models.User, row.user_id)):
        return None
    return row.user_id


def _forget_expired(db: Session) -> None:
    """Expired codes and tokens are of no use to anyone: don't keep them. The caller commits."""
    now = models.utcnow()
    # synchronize_session=False: compare in the database, not against rows already loaded
    # (SQLite hands those back without a timezone, which Python can't compare with `now`).
    db.query(models.McpAuthCode).filter(models.McpAuthCode.expires_at < now).delete(synchronize_session=False)
    db.query(models.McpToken).filter(models.McpToken.expires_at < now).delete(synchronize_session=False)
