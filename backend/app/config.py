from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./dev.db"

    jwt_secret: str = "change-me-in-prod"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 1 week

    # Fernet key used to encrypt the stored Garmin session token at rest.
    # Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    fernet_key: str = ""

    # How far back a sync looks. Activities come in one list; sleep and daily
    # recovery data are only requested for days that aren't stored yet.
    garmin_sync_lookback_days: int = 90

    # Hours between background syncs of every account connected to Garmin
    # (sleep, readiness and finished sessions arrive without pressing Sync).
    # 0 = off: the default, so `npm start` never syncs on its own; the Docker
    # stack turns it on.
    auto_sync_hours: float = 0

    # Comma-separated origins allowed to call the API from another site.
    # Empty = same-origin only (the frontend is served by this app, so that's
    # all it needs). "*" allows any origin.
    cors_allow_origins: str = ""

    # First admin account, created on startup if it doesn't exist yet. The
    # password is only used to *create* the account (or to give a password to
    # an existing account that has none); change it in the app afterwards.
    admin_username: str = ""
    admin_password: str = ""

    # Local testing only: also create the generic accounts admin/adminadmin and
    # user/useruser on startup. `npm start` turns this on; docker-compose never
    # passes it, and it's refused on anything but a local SQLite database,
    # because those passwords are public.
    seed_test_accounts: bool = False

    # How much the app prints (`docker logs`, the `npm start` terminal):
    # DEBUG, INFO, WARNING or ERROR. See app/logs.py.
    log_level: str = "INFO"

    # Swagger UI at /docs. Off by default so the API isn't advertised when the
    # app is reachable from the internet.
    enable_api_docs: bool = False

    # The public name friends open, e.g. "training.example.com". When set, the
    # app only answers on https://<that name>: a visit to any other address, or
    # over plain http, is sent there. Empty = no such check (your own network).
    app_domain: str = ""

    @field_validator("app_domain")
    @classmethod
    def _bare_hostname(cls, value: str) -> str:
        # Forgive a pasted URL: "https://Training.Example.com/" -> "training.example.com"
        return value.strip().lower().split("://")[-1].split("/")[0].split(":")[0]


@lru_cache
def get_settings() -> Settings:
    return Settings()
