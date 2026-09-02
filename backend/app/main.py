import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .database import Base, engine
from .routers import auth, calendar, chat, garmin, objectives

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


app.include_router(auth.router)
app.include_router(garmin.router)
app.include_router(calendar.router)
app.include_router(objectives.router)
app.include_router(chat.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(_FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")
