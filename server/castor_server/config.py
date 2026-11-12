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

    # Database. SQLite is fine for local development; Postgres for everything else.
    database_url: str = "sqlite+aiosqlite:///./data/castor.db"
    auto_migrate: bool = True  # apply database migrations on startup
    db_null_pool: bool = False  # no connection pool (tests that run on several event loops)

    # Auth
    secret_key: str = "change-me-castor-dev-secret"
    session_ttl_hours: int = 24 * 7
    cookie_name: str = "castor_session"
    cookie_secure: bool = False
    admin_username: str = "admin"
    admin_password: str = "castor"

    # Plugins. Restricted mode on here locks it on (admins can't turn it off in Settings), so
    # unrestricted plugins can't be installed or run. Safe mode starts with every plugin stopped,
    # to recover from a bad one.
    plugins_restricted: bool = False
    plugins_safe_mode: bool = False

    # Agents
    agent_secret: str = "change-me-agent-secret"
    # Let agents connect with the shared secret above as well as with their own tokens.
    # Turn off once every agent has a token (Agents → Enroll agent). Agents that have a
    # token can never connect with the shared secret.
    allow_shared_agent_secret: bool = True
    agent_heartbeat_interval: int = 15
    agent_offline_after: int = 45  # seconds without any message → offline (keep it a few heartbeats long)
    dispatch_interval: float = 5.0

    # Storage
    data_dir: Path = Path("./data")
    # Job artifacts uploaded by agents
    artifact_max_bytes: int = 100 * 1024 * 1024  # per file
    artifact_max_files: int = 200  # per job
    artifact_upload_window_s: int = 600  # accepted for this long after the job finishes
    # Variable sets: secrets and files are encrypted with this Fernet key (urlsafe base64,
    # 32 bytes). Unset: generated once into <data_dir>/vault.key. Losing it makes every
    # stored secret unreadable, so back it up with the database.
    vault_key: str | None = None
    variable_file_max_bytes: int = 1024 * 1024  # per file field
    # Notifications (a playbook step waiting for a decision, a run finishing) also go to these
    # webhook URLs, comma separated. Slack and Discord webhooks and ntfy topics get their own
    # format; anything else receives JSON. public_url makes the links in them absolute.
    notify_webhooks: str = ""
    public_url: str = ""
    # Built-in command definitions (commands/*.yaml), synced into the library on startup.
    commands_dir: Path = Path(__file__).resolve().parents[2] / "commands"
    # Built-in tool definitions (tools/*.yaml), synced into the library on startup.
    tools_dir: Path = Path(__file__).resolve().parents[2] / "tools"
    # Built-in playbook definitions (playbooks/*.yaml), synced into the library on startup.
    playbooks_dir: Path = Path(__file__).resolve().parents[2] / "playbooks"
    # Built-in themes for the web UI (theme_spec.py).
    themes_dir: Path = Path(__file__).resolve().parents[2] / "themes"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


settings = Settings()
