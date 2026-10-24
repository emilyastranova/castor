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
    # Job artifacts uploaded by agents
    artifact_max_bytes: int = 100 * 1024 * 1024  # per file
    artifact_max_files: int = 200  # per job
    artifact_upload_window_s: int = 600  # accepted for this long after the job finishes
    # Built-in command definitions (commands/*.yaml), synced into the library on startup.
    commands_dir: Path = Path(__file__).resolve().parents[2] / "commands"
    # Built-in playbook definitions (playbooks/*.yaml), synced into the library on startup.
    playbooks_dir: Path = Path(__file__).resolve().parents[2] / "playbooks"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


settings = Settings()
