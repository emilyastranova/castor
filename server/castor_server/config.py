"""Runtime configuration, loaded from environment variables / .env."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CASTOR_", env_file=".env", extra="ignore")

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"
    cors_origins: list[str] = ["http://localhost:5173"]

    # Database. SQLite is fine for local hacking; Postgres for everything else.
    database_url: str = "sqlite+aiosqlite:///./data/castor.db"
    auto_migrate: bool = True  # apply database migrations on startup

    # Auth
    secret_key: str = "change-me-castor-dev-secret"
    session_ttl_hours: int = 24 * 7
    cookie_name: str = "castor_session"
    cookie_secure: bool = False
    admin_username: str = "admin"
    admin_password: str = "castor"

    # Agents
    agent_secret: str = "change-me-agent-secret"
    agent_heartbeat_interval: int = 15
    agent_offline_after: int = 45  # seconds without heartbeat → offline
    dispatch_interval: float = 5.0

    # Storage
    data_dir: Path = Path("./data")

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


settings = Settings()
