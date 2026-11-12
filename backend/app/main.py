"""The FastAPI application: what runs when the server starts (`uvicorn app.main:app`).

Read top to bottom, this file:
1. runs `startup()` once before the first request (tables, first admin,
   background Garmin sync);
2. adds the security middleware every response goes through (public-address
   redirect, security headers, no caching of /api/*);
3. mounts the API routers (routers/*.py, one per URL prefix under /api), and
   with MCP_ENABLED Claude's connector (mcp/: /mcp and its OAuth login);
4. serves the page itself: index.html with versioned asset URLs, then every
   other file under frontend/ as static files.

There is no separate frontend server: this one process answers both the API
and the page. Settings come from the environment (config.py).
"""

import datetime
import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

from .accounts import bootstrap_admin, seed_test_accounts
from .config import get_settings
from .database import Base, SessionLocal, add_missing_columns, engine
from .garmin import autosync
from .garmin import sync as garmin_sync
from .logs import setup_logging
from .mcp import auth as mcp_auth
from .mcp import oauth as mcp_oauth
from .mcp.server import McpApp
from .routers import admin, athlete, auth, calendar, garmin, objectives, plan, planned, share
from .security import check_jwt_secret
from .version import VERSION
from .visitor import is_https

settings = get_settings()
setup_logging(settings.log_level)
log = logging.getLogger("trainer.main")


# --- Startup ------------------------------------------------------------------


def startup() -> None:
    """Runs once before the app answers: tables, first admin, background sync.
    Every step here must be safe to run on every restart."""
    check_jwt_secret()  # before anything else: a known secret lets anyone sign in as admin
    log.info(
        "Training App version %s starting · database %s · background sync %s · public address %s",
        VERSION,
        engine.url.render_as_string(hide_password=True),
        f"every {settings.auto_sync_hours:g} h" if settings.auto_sync_hours > 0 else "off",
        f"https://{settings.app_domain}" if settings.app_domain else "none (local network only)",
    )
    Base.metadata.create_all(bind=engine)  # new tables only; never alters existing ones
    add_missing_columns()  # ... so new nullable columns are added here (database.py)
    with SessionLocal() as db:
        garmin_sync.trim_stored_sleep(db)  # LEGACY: nights stored whole by versions before 15 (garmin/sync.py)
        garmin_sync.purge_old_traces(db, datetime.date.today())  # a sync may never run: AUTO_SYNC_HOURS=0
    bootstrap_admin()
    seed_test_accounts()
    mcp_auth.log_setup_problems()
    autosync.start(settings.auto_sync_hours)


mcp_app = McpApp()  # Claude's connector; only reachable when mounted further down


@asynccontextmanager
async def lifespan(_app: FastAPI):
    startup()
    if mcp_auth.is_configured():
        async with mcp_app.running():
            yield
    else:
        yield


# --- The app and its middleware -------------------------------------------------

# Swagger UI (/docs) and the OpenAPI schema only exist with ENABLE_API_DOCS=true.
docs = {} if settings.enable_api_docs else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="Training App", version=VERSION, lifespan=lifespan, **docs)
_API_DOCS_PATHS = ("/docs", "/redoc") if settings.enable_api_docs else ()

# Off by default: the page is served by this app, so the browser never needs CORS.
if settings.cors_allow_origins.strip():
    origins = ["*"] if settings.cors_allow_origins.strip() == "*" else [
        o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )


# The page loads its script and stylesheet from this app and nothing else, so
# a script that somehow got into the page can't run or send data elsewhere.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
# How long a browser that has used https://APP_DOMAIN refuses plain http for
# it. A month, not the usual year: long enough for friends who open the app
# every week, short enough to undo if the domain is ever set up differently.
HSTS_SECONDS = 30 * 24 * 3600
# Answers that hold personal data or tokens: the API, and Claude's connector with its login.
_NEVER_CACHED = ("/api/", "/oauth/", "/mcp")
# Names that only work on the server itself (health checks, `npm start`).
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _requested_host(request: Request) -> str:
    """The name the visitor typed, without the port: "training.example.com"."""
    host = request.headers.get("host", "").strip().lower()
    if host.startswith("["):  # [::1]:8000
        return host[1:].split("]")[0]
    return host.split(":")[0]


def _to_public_address(request: Request, domain: str):
    """The answer to a request that didn't come in on https://APP_DOMAIN."""
    if request.method not in ("GET", "HEAD"):
        # Not redirected: the browser would repeat a password that already
        # travelled unencrypted. The page itself was redirected before this.
        return JSONResponse({"detail": f"This app is only available at https://{domain}"}, status_code=400)
    # Always "/..." after the name, so nothing in the request can change which site it points to.
    target = f"https://{domain}/{request.url.path.lstrip('/')}"
    if request.url.query:
        target += f"?{request.url.query}"
    return RedirectResponse(target, status_code=307)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Every request passes here first. With APP_DOMAIN set, anything not on
    https://APP_DOMAIN (or localhost, for health checks) is sent there instead
    of being answered. Every response then gets the security headers."""
    domain = settings.app_domain
    host = _requested_host(request)
    on_public_address = bool(domain) and host == domain and is_https(request)
    if domain and not on_public_address and host not in _LOCAL_HOSTS:
        response = _to_public_address(request, domain)
    else:
        response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")  # a private app: keep it out of search engines
    if not request.url.path.startswith(_API_DOCS_PATHS):  # Swagger UI loads its own scripts from a CDN
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    if on_public_address:
        response.headers.setdefault("Strict-Transport-Security", f"max-age={HSTS_SECONDS}")
    if request.url.path.startswith(_NEVER_CACHED):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# --- The API ------------------------------------------------------------------------
# One router per URL prefix; each file's docstring lists what it answers.

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(garmin.router)
app.include_router(calendar.router)
app.include_router(athlete.router)
app.include_router(objectives.router)
app.include_router(planned.router)
app.include_router(plan.router)
app.include_router(share.router)

# Claude's connector, only with MCP_ENABLED and MCP_USERNAME (mcp/__init__.py).
# Two plain routes rather than a mount, so /mcp answers without a redirect to /mcp/.
if mcp_auth.is_configured():
    app.include_router(mcp_oauth.router)
    _mcp_endpoint = mcp_auth.BearerAuth(mcp_app)
    app.router.routes.append(Route("/mcp", endpoint=_mcp_endpoint))
    app.router.routes.append(Route("/mcp/", endpoint=_mcp_endpoint))


@app.get("/api/health")
def health():
    """For Docker's HEALTHCHECK (Dockerfile): answers as soon as the app is up."""
    return {"status": "ok"}


# --- The page ---------------------------------------------------------------------


class RevalidatingStaticFiles(StaticFiles):
    """Make browsers re-check the frontend on every load (a cheap 304 when
    unchanged), so an updated script or stylesheet is never hidden by the cache."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
# src="/js/main.js", href="/css/base.css": the page's own scripts and stylesheets.
_LOCAL_ASSET = re.compile(r'(src|href)="/([\w/.-]+\.(?:js|css))"')


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index():
    """index.html with ?v=<mtime> on the files it links (js/main.js, css/*.css):
    any change gives a new URL, so no browser can keep running a stale copy.
    The modules main.js imports are revalidated on every load (no-cache).
    The footer gets the app's version."""
    with open(os.path.join(_FRONTEND_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()

    def versioned(match: re.Match) -> str:
        path = match.group(2)
        version = int(os.path.getmtime(os.path.join(_FRONTEND_DIR, path)))
        return f'{match.group(1)}="/{path}?v={version}"'

    html = _LOCAL_ASSET.sub(versioned, html)
    html = html.replace("__APP_VERSION__", VERSION)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


# Last, so every /api route above is matched first. Missing only in odd test setups.
if os.path.isdir(_FRONTEND_DIR):
    app.mount("/", RevalidatingStaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")
