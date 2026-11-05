"""SQLAlchemy ORM models for milestone 1."""

from __future__ import annotations

import enum
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


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(256))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True)


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
    container_flags: Mapped[list[str]] = mapped_column(JSONType, default=list)
    agent_selector: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    run_on_move: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)  # 0 none … 3 high
    tags: Mapped[list[str]] = mapped_column(JSONType, default=list)
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

    project: Mapped[Project] = relationship(back_populates="tasks")
    column: Mapped[BoardColumn] = relationship(back_populates="tasks")
    playbook_run: Mapped[PlaybookRun | None] = relationship(back_populates="tasks")
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

    project: Mapped[Project] = relationship()
    tasks: Mapped[list[Task]] = relationship(
        back_populates="playbook_run", cascade="all, delete-orphan"
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
    # Set only when the body is changed after posting (shown as "edited" in the UI).
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    task: Mapped[Task] = relationship(back_populates="comments")
    author: Mapped[User | None] = relationship()


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
