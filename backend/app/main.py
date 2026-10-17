import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import autosync, garmin_details
from .accounts import bootstrap_admin, seed_test_accounts
from .config import get_settings
from .database import Base, SessionLocal, add_missing_columns, engine
from .routers import admin, athlete, auth, calendar, garmin, objectives, plan, planned, share
from .version import VERSION
from .visitor import is_https

settings = get_settings()

docs = {} if settings.enable_api_docs else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="Training App", version=VERSION, **docs)
_API_DOCS_PATHS = ("/docs", "/redoc") if settings.enable_api_docs else ()

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
# Names that only work on the server itself (health checks, `npm start`).
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _requested_host(request: Request) -> str:
    host = request.headers.get("host", "").strip().lower()
    if host.startswith("["):  # [::1]:8000
        return host[1:].split("]")[0]
    return host.split(":")[0]


def _to_public_address(request: Request, domain: str):
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
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    add_missing_columns()
    with SessionLocal() as db:
        garmin_details.trim_stored_sleep(db)  # nights stored whole by versions before 15
    bootstrap_admin()
    seed_test_accounts()
    autosync.start(settings.auto_sync_hours)


app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(garmin.router)
app.include_router(calendar.router)
app.include_router(athlete.router)
app.include_router(objectives.router)
app.include_router(planned.router)
app.include_router(plan.router)
app.include_router(share.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


class RevalidatingStaticFiles(StaticFiles):
    """Make browsers re-check the frontend on every load (a cheap 304 when
    unchanged), so an updated app.js/styles.css is never hidden by the cache.
    Range headers are dropped: the page never needs part of a file, and a
    crafted one makes this Starlette version burn CPU (CVE-2025-62727)."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "headers": [(k, v) for k, v in scope["headers"] if k != b"range"]}
        await super().__call__(scope, receive, send)

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index():
    """index.html with ?v=<mtime> on its assets: any change to app.js or
    styles.css gives a new URL, so no browser can keep running a stale copy.
    The footer gets the app's version."""
    with open(os.path.join(_FRONTEND_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()
    for asset in ("app.js", "styles.css"):
        version = int(os.path.getmtime(os.path.join(_FRONTEND_DIR, asset)))
        html = html.replace(f'"/{asset}"', f'"/{asset}?v={version}"')
    html = html.replace("__APP_VERSION__", VERSION)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


if os.path.isdir(_FRONTEND_DIR):
    app.mount("/", RevalidatingStaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")
