import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .accounts import bootstrap_admin, seed_test_accounts
from .config import get_settings
from .database import Base, add_missing_columns, drop_obsolete_tables, engine
from .routers import admin, auth, calendar, garmin, objectives, plan, planned

settings = get_settings()

docs = {} if settings.enable_api_docs else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="Training App", **docs)

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


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.on_event("startup")
def on_startup():
    drop_obsolete_tables()
    Base.metadata.create_all(bind=engine)
    add_missing_columns()
    bootstrap_admin()
    seed_test_accounts()


app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(garmin.router)
app.include_router(calendar.router)
app.include_router(objectives.router)
app.include_router(planned.router)
app.include_router(plan.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


class RevalidatingStaticFiles(StaticFiles):
    """Make browsers re-check the frontend on every load (a cheap 304 when
    unchanged), so an updated app.js/styles.css is never hidden by the cache."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index():
    """index.html with ?v=<mtime> on its assets: any change to app.js or
    styles.css gives a new URL, so no browser can keep running a stale copy."""
    with open(os.path.join(_FRONTEND_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()
    for asset in ("app.js", "styles.css"):
        version = int(os.path.getmtime(os.path.join(_FRONTEND_DIR, asset)))
        html = html.replace(f'"/{asset}"', f'"/{asset}?v={version}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


if os.path.isdir(_FRONTEND_DIR):
    app.mount("/", RevalidatingStaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")
