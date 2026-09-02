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

    anthropic_api_key: str = ""
    # NOTE: confirm this is a live model id in the Anthropic docs before deploying;
    # the project brief specified this value but model ids are updated over time.
    anthropic_model: str = "claude-sonnet-4-6"

    # How many days of history to pull on each Garmin sync.
    garmin_sync_lookback_days: int = 30

    cors_allow_origins: str = "*"


@lru_cache
def get_settings() -> Settings:
    return Settings()
