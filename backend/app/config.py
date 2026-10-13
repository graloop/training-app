from functools import lru_cache

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

    # Optional server-wide fallback for users who haven't saved their own key
    # in the settings menu.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"

    # How many days of history to pull on each Garmin sync.
    garmin_sync_lookback_days: int = 30

    # Comma-separated origins allowed to call the API from another site.
    # Empty = same-origin only (the frontend is served by this app, so that's
    # all it needs). "*" allows any origin.
    cors_allow_origins: str = ""

    # First admin account, created on startup if it doesn't exist yet. The
    # password is only used to *create* the account (or to give a password to
    # an existing account that has none); change it in the app afterwards.
    admin_username: str = ""
    admin_password: str = ""

    # The built-in coach chat calls an AI provider's API. Off by default: the
    # app is meant to be edited directly (by hand or with Claude in Chrome).
    ai_chat_enabled: bool = False

    # Swagger UI at /docs. Off by default so the API isn't advertised when the
    # app is reachable from the internet.
    enable_api_docs: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
