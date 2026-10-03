import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .database import Base, add_missing_columns, engine
from .routers import ai_settings, auth, calendar, chat, garmin, objectives, planned

settings = get_settings()

app = FastAPI(title="AI Training Coach")

origins = ["*"] if settings.cors_allow_origins == "*" else settings.cors_allow_origins.split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    add_missing_columns()


app.include_router(auth.router)
app.include_router(garmin.router)
app.include_router(calendar.router)
app.include_router(objectives.router)
app.include_router(chat.router)
app.include_router(ai_settings.router)
app.include_router(planned.router)


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
