"""SQLAlchemy ORM models for milestone 1."""

from __future__ import annotations

import enum
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from .db import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on every backend (SQLite drops tzinfo on read)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


class TaskKind(str, enum.Enum):
    manual = "manual"
    command = "command"


class ColumnRole(str, enum.Enum):
    backlog = "backlog"
    todo = "todo"
    active = "active"
    review = "review"
    done = "done"
    reported = "reported"  # added to the workflow report: the final step


class JobStatus(str, enum.Enum):
    queued = "queued"
    dispatched = "dispatched"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    lost = "lost"

    @property
    def is_terminal(self) -> bool:
        return self in {
            JobStatus.succeeded,
            JobStatus.failed,
            JobStatus.cancelled,
            JobStatus.lost,
        }


class AgentStatus(str, enum.Enum):
    idle = "idle"
    busy = "busy"
    offline = "offline"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow
    )


class UserRole(str, enum.Enum):
    """viewer: read-only. operator: works in their projects and edits the libraries.
    admin: everything, including users, agents and every project."""

    viewer = "viewer"
    operator = "operator"
    admin = "admin"

    @property
    def rank(self) -> int:
        return ["viewer", "operator", "admin"].index(self.value)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=False, length=16),
        default=UserRole.operator,
        server_default=UserRole.operator.value,  # rows inserted by hand get a role too
    )
    # A disabled account can't sign in, and its sessions stop working.
    disabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Profile picture: initials | upload | url | gravatar (see avatar_url).
    avatar_kind: Mapped[str] = mapped_column(String(16), default="initials", server_default="initials")
    avatar_link: Mapped[str] = mapped_column(String(500), default="", server_default="")  # kind url
    gravatar_email: Mapped[str] = mapped_column(String(254), default="", server_default="")
    # kind upload: the file under <data_dir>/avatars/; the version busts browser caches.
    avatar_file: Mapped[str] = mapped_column(String(200), default="", server_default="")
    avatar_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Their Material You seed colour (#rrggbb); empty means the default palette.
    theme_seed: Mapped[str] = mapped_column(String(9), default="", server_default="")

    @property
    def avatar_url(self) -> str | None:
        """What an <img> should load, or None to show initials."""
        if self.avatar_kind == "upload" and self.avatar_file:
            return f"/api/users/{self.id}/avatar?v={self.avatar_version}"
        if self.avatar_kind == "url" and self.avatar_link:
            return self.avatar_link
        if self.avatar_kind == "gravatar" and self.gravatar_email:
            digest = hashlib.sha256(self.gravatar_email.strip().lower().encode()).hexdigest()
            return f"https://gravatar.com/avatar/{digest}?s=160&d=identicon"
        return None

    @property
    def has_upload(self) -> bool:
        return bool(self.avatar_file)

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.admin

    def can(self, role: UserRole) -> bool:
        return self.role.rank >= role.rank

    # The ApiToken a request authenticated with (set by deps, never stored): its scopes
    # and project limit narrow what this user can do in that request.
    api_token = None


class ProjectMember(Base):
    """Who can see a project (admins see every project without being members)."""

    __tablename__ = "project_members"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Client(Base, TimestampMixin):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    projects: Mapped[list[Project]] = relationship(back_populates="client")


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), nullable=True
    )
    codename: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(64), default="pipeline")
    status: Mapped[str] = mapped_column(String(32), default="active")
    description: Mapped[str] = mapped_column(Text, default="")
    start_date: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    client: Mapped[Client | None] = relationship(back_populates="projects")
    columns: Mapped[list[BoardColumn]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="BoardColumn.position"
    )
    tasks: Mapped[list[Task]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class BoardColumn(Base, TimestampMixin):
    __tablename__ = "board_columns"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    position: Mapped[float] = mapped_column(Float, default=0)
    role: Mapped[ColumnRole | None] = mapped_column(
        Enum(ColumnRole, name="column_role", native_enum=False, length=16), nullable=True
    )
    wip_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    project: Mapped[Project] = relationship(back_populates="columns")
    tasks: Mapped[list[Task]] = relationship(back_populates="column", order_by="Task.position")


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    column_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("board_columns.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[float] = mapped_column(Float, default=0)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[TaskKind] = mapped_column(
        Enum(TaskKind, name="task_kind", native_enum=False, length=16), default=TaskKind.manual
    )
    command: Mapped[str] = mapped_column(Text, default="")
    # Set when the card runs a library command; `command` then holds the rendered line.
    command_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    command_args: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    image: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # A tool from the library (tool_definitions) the command runs with, instead of an image.
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    container_flags: Mapped[list[str]] = mapped_column(JSONType, default=list)
    agent_selector: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    run_on_move: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)  # 0 none … 3 high
    tags: Mapped[list[str]] = mapped_column(JSONType, default=list)
    # User ids (strings) of the people the card is assigned to (people.check_assignees).
    assignees: Mapped[list[str]] = mapped_column(JSONType, default=list)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    playbook_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("playbook_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    playbook_step_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # A for_each step's item cards: position of the item in its list (None otherwise).
    playbook_item: Mapped[int | None] = mapped_column(Integer, nullable=True)
    depends_on: Mapped[list[str]] = mapped_column(JSONType, default=list)
    # Library command variable slots → variable set ids: {"creds": "<uuid>"}
    var_bindings: Mapped[dict[str, str]] = mapped_column(JSONType, default=dict)
    # Chat/webhook alerts for this card's runs (or, on a run card, the playbook run):
    # {"channels": [ids], "on": "finish" | "failure" | "none", "long_minutes": n, "decisions": bool}
    alerts: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True, default=None)
    # Run in an interactive terminal (also on when its library command says tty: true).
    tty: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    project: Mapped[Project] = relationship(back_populates="tasks")
    column: Mapped[BoardColumn] = relationship(back_populates="tasks")
    playbook_run: Mapped[PlaybookRun | None] = relationship(back_populates="tasks", foreign_keys=[playbook_run_id])
    jobs: Mapped[list[Job]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="Job.queued_at"
    )
    comments: Mapped[list[Comment]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="Comment.created_at"
    )
    attachments: Mapped[list[Attachment]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class CommandDefinition(Base, TimestampMixin):
    """A reusable command with typed arguments. Built-ins are loaded from commands/*.yaml;
    user definitions are created in the UI. `spec` is the validated CommandSpec as JSON and
    `source_yaml` the text it was parsed from."""

    __tablename__ = "command_definitions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    origin: Mapped[str] = mapped_column(String(16), default="user")  # builtin | user
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSONType, default=list)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    source_yaml: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)


class VariableKind(Base, TimestampMixin):
    """A template of typed fields for variable sets (variables.KindSpec as JSON)."""

    __tablename__ = "variable_kinds"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    origin: Mapped[str] = mapped_column(String(16), default="user")  # builtin | user
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    spec: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class VariableSet(Base, TimestampMixin):
    """One filled-in instance of a kind. Plain values are stored as JSON; secret values
    and every file's contents are Fernet tokens (vault.py)."""

    __tablename__ = "variable_sets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    kind_id: Mapped[str] = mapped_column(ForeignKey("variable_kinds.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    # Empty: usable in every project. Otherwise only in these project ids.
    project_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    values: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    secrets: Mapped[dict[str, str]] = mapped_column(JSONType, default=dict)
    # {field: {filename, size, data: <token>}}
    files: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    # One-off sets entered on a card or in the Run dialog without saving to the library:
    # hidden from listings and usable only by the card (or playbook run) that owns them,
    # and deleted with it. Unowned until first bound.
    ephemeral: Mapped[bool] = mapped_column(Boolean, default=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    playbook_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("playbook_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class PlaybookRunStatus(str, enum.Enum):
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class PlaybookDefinition(Base, TimestampMixin):
    """A reusable playbook DAG. Built-ins are loaded from playbooks/*.yaml;
    user definitions are created in the UI. `spec` is the validated PlaybookSpec as JSON and
    `source_yaml` the text it was parsed from."""

    __tablename__ = "playbook_definitions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    origin: Mapped[str] = mapped_column(String(16), default="user")  # builtin | user
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSONType, default=list)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    source_yaml: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)


class PlaybookRun(Base, TimestampMixin):
    __tablename__ = "playbook_runs"
    # Runs are listed newest first.
    __table_args__ = (Index("ix_playbook_runs_created_at", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    playbook_id: Mapped[str] = mapped_column(String(64), index=True)
    playbook_name: Mapped[str] = mapped_column(String(200))
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[PlaybookRunStatus] = mapped_column(
        Enum(PlaybookRunStatus, name="playbook_run_status", native_enum=False, length=16),
        default=PlaybookRunStatus.running,
        index=True,
    )
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    step_task_ids: Mapped[dict[str, str]] = mapped_column(JSONType, default=dict)
    # Per-step state the jobs don't carry: transform results and steps that never got a job
    # (skipped, or failed to render). {step_id: {status, outputs, type, count, error, ...}}
    step_results: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    spec_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # The run's one card on the board (Phase 3h). Its steps are cards too, but hidden.
    card_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL", use_alter=True, name="fk_playbook_runs_card_id"),
        nullable=True,
        index=True,
    )

    project: Mapped[Project] = relationship()
    tasks: Mapped[list[Task]] = relationship(
        back_populates="playbook_run", cascade="all, delete-orphan", foreign_keys="Task.playbook_run_id"
    )


class Agent(Base, TimestampMixin):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    hostname: Mapped[str] = mapped_column(String(255), default="")
    os: Mapped[str] = mapped_column(String(255), default="")
    username: Mapped[str] = mapped_column(String(128), default="")
    version: Mapped[str] = mapped_column(String(32), default="")
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSONType, default=list)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, name="agent_status", native_enum=False, length=16),
        default=AgentStatus.offline,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    jobs: Mapped[list[Job]] = relationship(back_populates="agent")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    command: Mapped[str] = mapped_column(Text)
    image: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # A tool from the library (tool_definitions) the command runs with, instead of an image.
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    container_flags: Mapped[list[str]] = mapped_column(JSONType, default=list)
    env: Mapped[dict[str, str]] = mapped_column(JSONType, default=dict)
    # The card's variable bindings when the job was queued. Values are resolved (and
    # secrets decrypted) only at dispatch and never stored on the job.
    var_bindings: Mapped[dict[str, str]] = mapped_column(JSONType, default=dict)
    timeout_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", native_enum=False, length=16),
        default=JobStatus.queued,
        index=True,
    )
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    dispatched_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # SHA-256 of the one-off token the agent uses to upload this job's artifacts.
    artifact_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The command definition's outputs at dispatch time, so later edits don't change how
    # this run is parsed. None for free-form commands and jobs from before Phase 2b.
    output_specs: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType, nullable=True)
    # Interactive terminal (pty): its output is raw terminal output, and people can type.
    tty: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # The terminal's size as the person in control last set it: {"cols": n, "rows": n}.
    tty_size: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    # The prompt it's waiting at (an automated run nobody has typed into yet), if any.
    waiting_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Who first typed into it: from then on it's theirs, and prompts don't alert.
    input_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    stdout_bytes: Mapped[int] = mapped_column(Integer, default=0)
    stderr_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    task: Mapped[Task] = relationship(back_populates="jobs")
    agent: Mapped[Agent | None] = relationship(back_populates="jobs")
    log_chunks: Mapped[list[JobLogChunk]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobLogChunk.seq"
    )


class JobOutput(Base):
    """A parsed, typed result of a job (Phase 2b), one row per declared output."""

    __tablename__ = "job_outputs"
    __table_args__ = (UniqueConstraint("job_id", "name", name="uq_job_output_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(32))
    parser: Mapped[str] = mapped_column(String(64))
    # Where the value came from: artifact file names, or "stdout".
    sources: Mapped[list[str]] = mapped_column(JSONType, default=list)
    value: Mapped[Any] = mapped_column(JSONType, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class JobLogChunk(Base):
    __tablename__ = "job_log_chunks"
    __table_args__ = (UniqueConstraint("job_id", "seq", name="uq_job_log_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    stream: Mapped[str] = mapped_column(String(8))  # stdout | stderr | system
    data: Mapped[str] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    job: Mapped[Job] = relationship(back_populates="log_chunks")


class Comment(Base, TimestampMixin):
    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text)
    # Ids (strings) of the people the comment @mentions (people.mentioned).
    mentions: Mapped[list[str]] = mapped_column(JSONType, default=list)
    # Set only when the body is changed after posting (shown as "edited" in the UI).
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    task: Mapped[Task] = relationship(back_populates="comments")
    author: Mapped[User | None] = relationship()


class Asset(Base, TimestampMixin):
    """Something a project found (Phase 3f): a host, service, URL, domain, user, credential
    or share. One record per thing (project + type + identity key); every tool that reports
    on it adds an observation, and `fields` holds the merged current values (assets.py)."""

    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("project_id", "type", "key", name="uq_asset_identity"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(16), index=True)
    key: Mapped[str] = mapped_column(String(400))
    label: Mapped[str] = mapped_column(String(400))
    fields: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    # A credential's secret, encrypted with the vault (never in `fields`).
    secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class AssetObservation(Base):
    """What one source said about an asset, and when: provenance for every field value."""

    __tablename__ = "asset_observations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(16))  # parser | manual | import
    parser: Mapped[str | None] = mapped_column(String(64), nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class AssetLink(Base):
    """A relation between two assets: a service *on* a host, a domain that *resolves to* one."""

    __tablename__ = "asset_links"
    __table_args__ = (UniqueConstraint("from_id", "to_id", "kind", name="uq_asset_link"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    from_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    to_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class AssetTrigger(Base, TimestampMixin):
    """Run a command or playbook when an asset appears or changes (with guards)."""

    __tablename__ = "asset_triggers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    asset_type: Mapped[str] = mapped_column(String(16))
    on_event: Mapped[str] = mapped_column(String(16), default="created")  # created | updated | any
    # [{field, op, value}], all must hold (assets.conditions_met)
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    action: Mapped[str] = mapped_column(String(16))  # command | playbook
    target: Mapped[str] = mapped_column(String(64))  # command or playbook id
    # argument / input name -> template ("{{ asset.ip }}")
    args: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    once_per_asset: Mapped[bool] = mapped_column(Boolean, default=True)
    max_per_hour: Mapped[int] = mapped_column(Integer, default=20)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AssetTriggerFire(Base):
    """One time a trigger matched an asset: started, waiting for approval (out of scope),
    skipped, limited (rate) or failed."""

    __tablename__ = "asset_trigger_fires"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    trigger_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset_triggers.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("playbook_runs.id", ondelete="SET NULL"), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class ProjectNote(Base, TimestampMixin):
    """A pinned markdown note on a project's dashboard (status, objectives, contacts…)."""

    __tablename__ = "project_notes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    # A sticky-note color name (api/notes.COLORS); "default" is the plain surface.
    color: Mapped[str] = mapped_column(String(16), default="default", server_default="default")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Earlier versions, newest first: [{title, body, by, by_name, at}] (capped; see api/notes.py).
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)

    editor: Mapped[User | None] = relationship()


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    uploader_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16), default="file")  # file | screenshot | artifact
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    storage_path: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    task: Mapped[Task | None] = relationship(back_populates="attachments")


class ActivityEvent(Base):
    __tablename__ = "activity_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    actor_type: Mapped[str] = mapped_column(String(16))  # user | agent | system
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_name: Mapped[str] = mapped_column(String(128), default="")
    type: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Notification(Base):
    """Something a person should look at, such as a playbook step waiting for a decision.
    One row per user so each has their own unread state."""

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # e.g. playbook_step.attention, playbook_run.failed
    kind: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), default="info")  # info|success|warning|error
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    # Path in the web UI, e.g. /playbooks?run=<id>&step=<step>
    link: Mapped[str] = mapped_column(String(500), default="")
    # What it is about, e.g. "run:<id>:step:<step>": dealing with it clears the notification.
    subject: Mapped[str] = mapped_column(String(200), default="", index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class AgentToken(Base):
    """A credential for one agent. Created unbound (an enrollment token); the first agent
    to connect with it is bound to it. Only the SHA-256 of the token is stored."""

    __tablename__ = "agent_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    # What it's for, e.g. the name the agent will have; shown in the UI.
    label: Mapped[str] = mapped_column(String(128), default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # The first characters of the token, to recognise it ("castor_ab12…").
    prefix: Mapped[str] = mapped_column(String(16))
    # Applied to the agent when it enrolls.
    tags: Mapped[list[str]] = mapped_column(JSONType, default=list)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    agent: Mapped[Agent | None] = relationship()


class ToolDefinition(Base, TimestampMixin):
    """A tool commands can run with (tool_spec.ToolSpec). Built-ins come from tools/*.yaml,
    user definitions from the UI."""

    __tablename__ = "tool_definitions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    origin: Mapped[str] = mapped_column(String(16), default="user")  # builtin | user
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    spec: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    source_yaml: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)


class ParserDefinition(Base, TimestampMixin):
    """A parser people (or the AI) wrote: declarative YAML (parser_spec.ParserSpec), used by
    name in command outputs like a built-in. Built-in parsers are code in parsers.py."""

    __tablename__ = "parser_definitions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    origin: Mapped[str] = mapped_column(String(16), default="user")
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    spec: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    source_yaml: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)


class ParserProposal(Base, TimestampMixin):
    """A parser the AI drafted for one job output that its parser couldn't read, checked
    against that output. Using it re-reads the job; saving it to the library is separate and
    always a person's choice."""

    __tablename__ = "parser_proposals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    output_name: Mapped[str] = mapped_column(String(64))
    # The parser the output named, and what it said when it couldn't read it.
    failed_parser: Mapped[str] = mapped_column(String(64), default="")
    failure: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="drafting")  # drafting | ready | failed | applied | dismissed
    yaml: Mapped[str] = mapped_column(Text, default="")
    spec: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    preview: Mapped[Any] = mapped_column(JSONType, nullable=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    problems: Mapped[list[str]] = mapped_column(JSONType, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    # Who asked (None: drafted on its own, in a playbook run) and who used it.
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    applied_by: Mapped[str] = mapped_column(String(200), default="")
    saved_as: Mapped[str] = mapped_column(String(64), default="")


class ApiToken(Base):
    """A personal API token (api_tokens.py): acts as its user, narrowed to its scopes and,
    if set, to some projects. Only the SHA-256 of the token is stored."""

    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # The first characters of the token, to recognise it ("castor_pat_ab12…").
    prefix: Mapped[str] = mapped_column(String(24))
    scopes: Mapped[list[str]] = mapped_column(JSONType, default=list)
    # Project ids (strings) it's limited to; null: every project its user can see.
    project_ids: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_used_ip: Mapped[str] = mapped_column(String(64), default="", server_default="")
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class AppSetting(Base):
    """Instance-wide settings admins change in Settings → General (app_settings.py holds the
    keys, their defaults and limits)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONType, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AlertChannel(Base, TimestampMixin):
    """Somewhere alerts go (Settings → Alerts): a preset (Slack, Google Chat, ntfy…) or a
    custom webhook. `fields` are plain options; `secrets` are vault tokens per field (URLs
    with keys in them, tokens, headers). `events` are what it gets for every card and run."""

    __tablename__ = "alert_channels"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    secrets: Mapped[dict[str, str]] = mapped_column(JSONType, default=dict)
    events: Mapped[list[str]] = mapped_column(JSONType, default=list)
    long_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AlertDelivery(Base):
    """One alert sent (or not) to a channel: the history in Settings, and what stops a
    long-running alert from repeating (`subject`)."""

    __tablename__ = "alert_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alert_channels.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(200), default="", index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class AiProvider(Base, TimestampMixin):
    """An LLM provider admins set up in Settings → AI (llm.py `KINDS`): plain `fields` (base
    URL, project, region…) and vault-encrypted `secrets` per field (API keys, service accounts)."""

    __tablename__ = "ai_providers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    secrets: Mapped[dict[str, str]] = mapped_column(JSONType, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AiCall(Base):
    """One attempt at a model (fallbacks are separate rows): what for, which model, how it went."""

    __tablename__ = "ai_calls"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    purpose: Mapped[str] = mapped_column(String(32), index=True)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_providers.id", ondelete="SET NULL"), nullable=True)
    provider_name: Mapped[str] = mapped_column(String(120), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ms: Mapped[int] = mapped_column(Integer, default=0)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
