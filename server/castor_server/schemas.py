"""Pydantic request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import AgentStatus, ColumnRole, JobStatus, TaskKind


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- auth / users ----------


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(ORM):
    id: uuid.UUID
    username: str
    display_name: str
    is_admin: bool


class LoginResponse(BaseModel):
    token: str
    user: UserOut


# ---------- clients / projects ----------


class ClientIn(BaseModel):
    name: str
    notes: str = ""


class ClientOut(ORM):
    id: uuid.UUID
    name: str
    notes: str
    created_at: datetime


class ProjectIn(BaseModel):
    codename: str
    name: str
    kind: str = "pipeline"
    client_id: uuid.UUID | None = None
    description: str = ""
    start_date: datetime | None = None
    end_date: datetime | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class ProjectPatch(BaseModel):
    codename: str | None = None
    name: str | None = None
    kind: str | None = None
    status: str | None = None
    client_id: uuid.UUID | None = None
    description: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    settings: dict[str, Any] | None = None


class ProjectOut(ORM):
    id: uuid.UUID
    codename: str
    name: str
    kind: str
    status: str
    client_id: uuid.UUID | None
    description: str
    start_date: datetime | None
    end_date: datetime | None
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ProjectSummary(ProjectOut):
    client_name: str | None = None
    task_count: int = 0
    done_count: int = 0
    running_count: int = 0


# ---------- columns ----------


class ColumnIn(BaseModel):
    name: str
    role: ColumnRole | None = None
    position: float | None = None
    wip_limit: int | None = None


class ColumnPatch(BaseModel):
    name: str | None = None
    role: ColumnRole | None = None
    position: float | None = None
    wip_limit: int | None = None


class ColumnOut(ORM):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    position: float
    role: ColumnRole | None
    wip_limit: int | None


# ---------- tasks ----------


class CommandDefinitionOut(ORM):
    id: str
    origin: str
    name: str
    description: str
    tags: list[str]
    spec: dict[str, Any]
    source_yaml: str
    version: int
    updated_at: datetime
    in_use: bool = False


class CommandYamlIn(BaseModel):
    yaml: str


class RenderIn(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)


class RenderOut(BaseModel):
    command: str
    image: str | None
    container_flags: list[str] = Field(default_factory=list)


class TaskIn(BaseModel):
    title: str
    column_id: uuid.UUID | None = None
    description: str = ""
    kind: TaskKind = TaskKind.manual
    command: str = ""
    command_ref: str | None = None
    command_args: dict[str, Any] = Field(default_factory=dict)
    image: str | None = None
    container_flags: list[str] = Field(default_factory=list)
    agent_selector: dict[str, Any] = Field(default_factory=dict)
    run_on_move: bool = True
    timeout_s: int | None = None
    priority: int = 0
    tags: list[str] = Field(default_factory=list)
    assignees: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    due_at: datetime | None = None
    var_bindings: dict[str, uuid.UUID | None] = Field(default_factory=dict)


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    kind: TaskKind | None = None
    command: str | None = None
    command_ref: str | None = None
    command_args: dict[str, Any] | None = None
    image: str | None = None
    container_flags: list[str] | None = None
    agent_selector: dict[str, Any] | None = None
    run_on_move: bool | None = None
    timeout_s: int | None = None
    priority: int | None = None
    tags: list[str] | None = None
    assignees: list[str] | None = None
    custom_fields: dict[str, Any] | None = None
    due_at: datetime | None = None
    # Variable slot → set id; null unbinds a slot. Replaces all bindings when given.
    var_bindings: dict[str, uuid.UUID | None] | None = None


class TaskMove(BaseModel):
    column_id: uuid.UUID
    position: float


class JobOut(ORM):
    id: uuid.UUID
    task_id: uuid.UUID
    agent_id: uuid.UUID | None
    command: str
    image: str | None
    container_flags: list[str] = Field(default_factory=list)
    status: JobStatus
    exit_code: int | None
    error: str | None
    queued_at: datetime
    dispatched_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    stdout_bytes: int
    stderr_bytes: int
    # Declared parsed outputs (from the command definition at dispatch), if any.
    output_specs: list[dict[str, Any]] | None = None


class TaskOut(ORM):
    id: uuid.UUID
    project_id: uuid.UUID
    column_id: uuid.UUID
    position: float
    title: str
    description: str
    kind: TaskKind
    command: str
    command_ref: str | None
    command_args: dict[str, Any]
    image: str | None
    container_flags: list[str] = Field(default_factory=list)
    agent_selector: dict[str, Any]
    run_on_move: bool
    timeout_s: int | None
    priority: int
    tags: list[str]
    assignees: list[str]
    custom_fields: dict[str, Any]
    due_at: datetime | None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    playbook_run_id: uuid.UUID | None = None
    playbook_step_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    var_bindings: dict[str, str] = Field(default_factory=dict)
    latest_job: JobOut | None = None
    comment_count: int = 0
    attachment_count: int = 0


class BoardOut(BaseModel):
    project: ProjectOut
    columns: list[ColumnOut]
    tasks: list[TaskOut]


# ---------- comments / attachments / activity ----------


class CommentIn(BaseModel):
    body: str


class CommentOut(ORM):
    id: uuid.UUID
    task_id: uuid.UUID
    author_id: uuid.UUID | None
    author_name: str = ""
    body: str
    created_at: datetime
    updated_at: datetime
    edited_at: datetime | None = None


class AttachmentOut(ORM):
    id: uuid.UUID
    project_id: uuid.UUID
    task_id: uuid.UUID | None
    job_id: uuid.UUID | None
    kind: str
    filename: str
    content_type: str
    size: int
    created_at: datetime


class ActivityOut(ORM):
    id: int
    project_id: uuid.UUID | None
    task_id: uuid.UUID | None
    actor_type: str
    actor_id: str | None
    actor_name: str
    type: str
    message: str
    payload: dict[str, Any]
    created_at: datetime


# ---------- jobs / logs ----------


class LogChunkOut(ORM):
    seq: int
    stream: str
    data: str
    ts: datetime


class JobOutputOut(ORM):
    name: str
    type: str
    parser: str
    sources: list[str]
    value: Any
    error: str | None
    created_at: datetime


class JobDetail(JobOut):
    logs: list[LogChunkOut] = Field(default_factory=list)
    outputs: list[JobOutputOut] = Field(default_factory=list)


# ---------- agents ----------


class AgentOut(ORM):
    id: uuid.UUID
    name: str
    hostname: str
    os: str
    username: str
    version: str
    capabilities: dict[str, Any]
    tags: list[str]
    max_concurrency: int
    status: AgentStatus
    last_seen_at: datetime | None
    created_at: datetime
    running_jobs: int = 0
    connected: bool = False


class AgentPatch(BaseModel):
    tags: list[str] | None = None
    max_concurrency: int | None = None


# ---------- playbooks ----------


class PlaybookDefinitionOut(ORM):
    id: str
    origin: str
    name: str
    description: str
    tags: list[str]
    spec: dict[str, Any]
    source_yaml: str
    version: int
    created_at: datetime
    updated_at: datetime


class PlaybookDefinitionIn(BaseModel):
    yaml: str


class PlaybookRunIn(BaseModel):
    playbook_id: str
    project_id: uuid.UUID
    inputs: dict[str, Any] = Field(default_factory=dict)


class PlaybookRunOut(ORM):
    id: uuid.UUID
    playbook_id: str
    playbook_name: str
    project_id: uuid.UUID
    status: str
    inputs: dict[str, Any]
    step_task_ids: dict[str, str]
    # Transform results and steps that never got a job (skipped / failed to start)
    step_results: dict[str, Any] = Field(default_factory=dict)
    # Every step's status: pending, queued, running, succeeded, failed, cancelled,
    # skipped or blocked
    step_status: dict[str, str] = Field(default_factory=dict)
    spec_snapshot: dict[str, Any]
    finished_at: datetime | None = None
    created_at: datetime
    tasks: list[TaskOut] = Field(default_factory=list)



# ---------- notifications ----------


class NotificationOut(ORM):
    id: uuid.UUID
    user_id: uuid.UUID
    project_id: uuid.UUID | None
    kind: str
    severity: str
    title: str
    body: str
    link: str
    subject: str
    payload: dict[str, Any] = Field(default_factory=dict)
    read_at: datetime | None = None
    created_at: datetime


class NotificationList(BaseModel):
    items: list[NotificationOut]
    unread: int
