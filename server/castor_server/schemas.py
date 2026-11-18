"""Pydantic request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import AgentStatus, ColumnRole, JobStatus, TaskKind, UserRole


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
    # Set by admins (or verified by a sign-on provider); how single sign-on links accounts.
    email: str = ""
    role: UserRole
    is_admin: bool
    disabled_at: datetime | None = None
    created_at: datetime
    avatar_url: str | None = None
    avatar_kind: str = "initials"
    avatar_link: str = ""
    gravatar_email: str = ""
    # Whether an uploaded picture exists (to switch back to it).
    has_upload: bool = False
    # Their theme colour (#rrggbb), or "" for the default palette.
    theme_seed: str = ""
    # Their theme (a theme_definitions id; "" for Castor's own; null: the instance default).
    theme: str | None = None
    # The Companion on their timelines (off by default): their own picture (None: the built-in
    # one) and its seat line (0–1 from the top).
    companion: bool = False
    companion_url: str | None = None
    companion_seat: float = 0.605
    # Only on /api/auth/me: the groups they're in (agents limited to some people count them).
    group_ids: list[str] = []


class UserBrief(ORM):
    """A user as others see them (pickers, member lists)."""

    id: uuid.UUID
    username: str
    display_name: str
    role: UserRole
    avatar_url: str | None = None


class AvatarIn(BaseModel):
    kind: str = Field(pattern=r"^(initials|upload|url|gravatar)$")
    url: str = Field("", max_length=500)
    gravatar_email: str = Field("", max_length=254)


USERNAME = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"


class UserIn(BaseModel):
    username: str = Field(pattern=USERNAME)
    display_name: str = Field("", max_length=128)
    role: UserRole = UserRole.operator
    password: str = Field(min_length=8, max_length=256)
    email: str = Field("", max_length=254)
    # Make them a member of every project that exists now.
    all_projects: bool = False


class UserPatch(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=128)
    role: UserRole | None = None
    disabled: bool | None = None
    # An admin setting a new password for them.
    password: str | None = Field(None, min_length=8, max_length=256)
    email: str | None = Field(None, max_length=254)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=256)


class ProfilePatch(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=128)
    theme_seed: str | None = Field(None, pattern=r"^(#[0-9a-fA-F]{6})?$")
    # A theme_definitions id, "" for Castor's own, or null to follow the instance default.
    theme: str | None = Field(None, max_length=64)
    companion: bool | None = None
    companion_seat: float | None = Field(None, ge=0.05, le=0.95)


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
    # Board cards that run it, in the projects the caller can see; playbooks with a step that runs it.
    card_count: int = 0
    playbooks: list[str] = Field(default_factory=list)


class CommandUseCard(BaseModel):
    id: uuid.UUID
    title: str
    project_id: uuid.UUID
    project_codename: str


class CommandUsePlaybook(BaseModel):
    id: str
    name: str


class CommandUsageOut(BaseModel):
    cards: list[CommandUseCard]
    card_count: int
    playbooks: list[CommandUsePlaybook]


class CommandYamlIn(BaseModel):
    yaml: str


class RenderIn(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)


class RequestPreview(BaseModel):
    """An HTTP action as it will be sent: arguments filled in, variables as written."""

    method: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None


class RenderOut(BaseModel):
    command: str
    image: str | None
    tool: str | None = None
    container_flags: list[str] = Field(default_factory=list)
    kind: str = "command"
    request: RequestPreview | None = None


class AlertRules(BaseModel):
    """Chat/webhook alerts for one card's runs, or a playbook run on its card (Phase 3k)."""

    channels: list[uuid.UUID] = Field(default_factory=list)
    on: Literal["finish", "failure", "none"] = "failure"
    long_minutes: int | None = Field(default=None, ge=1, le=7 * 24 * 60)
    decisions: bool = True
    # People told in Castor (and on their desktop) when it finishes, either way.
    people: list[uuid.UUID] = Field(default_factory=list)
    # Groups told the same way: everyone in them who can see the project.
    groups: list[uuid.UUID] = Field(default_factory=list)

    def stored(self) -> dict[str, Any] | None:
        return self.model_dump(mode="json") if self.channels or self.people or self.groups else None


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
    # Its network route: a route id, "none" (direct), or null (the project's default).
    route: str | None = None
    run_on_move: bool = True
    timeout_s: int | None = None
    priority: int = 0
    tags: list[str] = Field(default_factory=list)
    assignees: list[str] = Field(default_factory=list)
    group_assignees: list[str] = Field(default_factory=list)  # group ids (or @handles)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    due_at: datetime | None = None
    # Cards of the project this one waits on (blocked until each is done, reported or archived).
    waits_on: list[uuid.UUID] = Field(default_factory=list)
    var_bindings: dict[str, uuid.UUID | None] = Field(default_factory=dict)
    tty: bool = False
    # Runs until stopped (command_spec.ServiceSpec), for a typed command.
    service: dict[str, Any] | None = None
    # HTTP actions: the most requests this card makes ("60/minute"); none: the definition's.
    rate_limit: str | None = None
    # Ask before each run (a person approves every job).
    confirm: bool = False
    # Groups whose members (and admins) approve its runs (ids or @handles); none: the project's.
    approvers: list[str] = Field(default_factory=list)
    # A subtask of this card (an activity in the same project): it stays off the board.
    parent_id: uuid.UUID | None = None

    @field_validator("rate_limit")
    @classmethod
    def rate(cls, value: str | None) -> str | None:
        from .http_spec import check_rate

        return check_rate(value)

    @field_validator("service")
    @classmethod
    def service_spec(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        from .command_spec import ServiceSpec

        return ServiceSpec.model_validate(value).model_dump() if value else None


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
    # A route id, "none" (direct) or "" (back to the project's default).
    route: str | None = None
    run_on_move: bool | None = None
    timeout_s: int | None = None
    priority: int | None = None
    tags: list[str] | None = None
    assignees: list[str] | None = None
    group_assignees: list[str] | None = None  # group ids (or @handles)
    custom_fields: dict[str, Any] | None = None
    due_at: datetime | None = None  # null clears it
    waits_on: list[uuid.UUID] | None = None  # replaces the list; [] clears it
    # Variable slot → set id; null unbinds a slot. Replaces all bindings when given.
    var_bindings: dict[str, uuid.UUID | None] | None = None
    # Alerts for this card's runs; channels: [] turns them off.
    alerts: AlertRules | None = None
    tty: bool | None = None
    service: dict[str, Any] | None = None  # {} or null: an ordinary job again
    rate_limit: str | None = None  # null or "" clears it
    confirm: bool | None = None
    approvers: list[str] | None = None  # group ids (or @handles); [] uses the project's

    @field_validator("rate_limit")
    @classmethod
    def rate(cls, value: str | None) -> str | None:
        from .http_spec import check_rate

        return check_rate(value)

    @field_validator("service")
    @classmethod
    def service_spec(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        from .command_spec import ServiceSpec

        return ServiceSpec.model_validate(value).model_dump() if value else None


class TaskMove(BaseModel):
    column_id: uuid.UUID
    position: float | None = None  # None: the top of the column
    # Start it although cards it waits on aren't finished (otherwise that's a 409).
    force: bool = False


class JobOut(ORM):
    id: uuid.UUID
    task_id: uuid.UUID
    agent_id: uuid.UUID | None
    # One of a run on several agents at once: the batch, and the agent it must go to.
    batch_id: uuid.UUID | None = None
    target_agent_id: uuid.UUID | None = None
    command: str
    image: str | None
    tool: str | None = None
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
    tty: bool = False
    tty_size: dict[str, Any] | None = None
    # A service (runs until stopped): its settings; progress has ready_at, stopping, matches.
    service: dict[str, Any] | None = None
    waiting_input: str | None = None
    input_by: uuid.UUID | None = None
    # HTTP actions: run by the Castor server ("server"); waiting for a rate limit until resume_at.
    kind: str = "command"
    runner: str | None = None
    via_agent_id: uuid.UUID | None = None
    # The network route it went through (no secrets) and how the agent applied it.
    route_info: dict[str, Any] | None = None
    resume_at: datetime | None = None
    approved_by: uuid.UUID | None = None
    approved_at: datetime | None = None
    created_by: uuid.UUID | None = None
    progress: dict[str, Any] | None = None


class BatchSummary(BaseModel):
    """A card's latest run when it ran on several agents at once (one job each)."""

    total: int
    active: int  # queued, dispatched or running
    succeeded: int
    failed: int  # failed, lost or cancelled
    # running while any job is; then failed if any job didn't succeed, else succeeded
    status: str
    job_ids: list[uuid.UUID]


class SubtaskSummary(BaseModel):
    """An activity card's subtasks, as its card on the board shows them."""

    total: int = 0
    done: int = 0  # in Review, Done or Reported, or archived
    running: int = 0  # started (In Progress, or a run on its way), not finished
    failed: int = 0  # latest run failed, not finished
    approvals: int = 0  # a run waiting for approval
    next_due: datetime | None = None  # the soonest due date of the unfinished ones
    queued: int = 0  # waiting their turn in a Run all with a limit (subtasks.pump)
    at_once: int | None = None  # that Run all's limit


class RunSummary(BaseModel):
    """A playbook run, as its card on the board shows it (Phase 3h)."""

    run_id: uuid.UUID
    playbook_id: str
    status: str  # the run's: running, succeeded, failed, cancelled
    done: int
    total: int
    running: int
    attention: int
    failed: int
    # step id -> its (hidden) card's id
    step_task_ids: dict[str, str] = Field(default_factory=dict)


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
    tool: str | None = None
    container_flags: list[str] = Field(default_factory=list)
    agent_selector: dict[str, Any]
    route: str | None = None
    run_on_move: bool
    timeout_s: int | None
    priority: int
    tags: list[str]
    assignees: list[str]
    group_assignees: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any]
    due_at: datetime | None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None  # off the board until restored
    archived_by: uuid.UUID | None = None
    playbook_run_id: uuid.UUID | None = None
    playbook_step_id: str | None = None
    playbook_item: int | None = None
    depends_on: list[str] = Field(default_factory=list)
    waits_on: list[str] | None = None  # cards it waits on (ids)
    # Of those, the ones not finished yet: [{id, title}]. Empty: it can start.
    blocked_by: list[dict[str, str]] = Field(default_factory=list)
    var_bindings: dict[str, str] = Field(default_factory=dict)
    alerts: dict[str, Any] | None = None
    tty: bool = False
    service: dict[str, Any] | None = None
    rate_limit: str | None = None
    confirm: bool = False
    approvers: list[str] = Field(default_factory=list)

    @field_validator("approvers", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return value or []

    # A subtask: its activity. An activity: its subtasks' progress.
    parent_id: uuid.UUID | None = None
    subtasks: SubtaskSummary | None = None
    latest_job: JobOut | None = None
    batch: BatchSummary | None = None
    # Set on a playbook run's card: where the run is.
    run: RunSummary | None = None
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
    author_avatar: str | None = None
    body: str
    mentions: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    edited_at: datetime | None = None
    via: str | None = None  # "mcp": through an AI assistant
    # [{emoji, count, user_ids, names}] (reactions.py)
    reactions: list[dict[str, Any]] = Field(default_factory=list)


class AttachmentOut(ORM):
    id: uuid.UUID
    project_id: uuid.UUID
    task_id: uuid.UUID | None
    job_id: uuid.UUID | None
    kind: str
    filename: str
    content_type: str
    size: int
    snapshot: dict[str, Any] | None = None
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


class ParserProposalOut(ORM):
    """A parser the AI drafted for a job output its parser couldn't read."""

    id: uuid.UUID
    job_id: uuid.UUID
    project_id: uuid.UUID
    output_name: str
    failed_parser: str
    failure: str
    status: str
    yaml: str
    preview: Any = None
    count: int
    problems: list[str]
    notes: str
    model: str
    applied_by: str
    saved_as: str
    automatic: bool = False
    # After applying: what happened to the card's command (see use_in_command).
    command_note: str = ""
    created_at: datetime
    updated_at: datetime


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
    # The projects whose jobs it may run; null: any project.
    project_ids: list[uuid.UUID] | None = None
    # Who may run work on it: {users: [ids], groups: [ids]}; null: everyone (admins always may).
    people: dict[str, list[str]] | None = None
    # Its network segment ("VLAN 20 / user LAN"), its vantage point; empty: not said.
    segment: str = ""
    # Its network route (everything it runs goes that way); null: none.
    route: str | None = None
    max_concurrency: int
    status: AgentStatus
    last_seen_at: datetime | None
    created_at: datetime
    running_jobs: int = 0
    connected: bool = False
    # Set for an SSH host Castor connects to (Agents → Add SSH host), not an agent that dials in.
    ssh_host_id: uuid.UUID | None = None
    # A machine a plugin's agent type reaches: {id (the plugin), type, machine (its id there)}, and
    # whether that system answered when last asked ({ok, message, checked_at}).
    plugin: dict[str, Any] | None = None
    plugin_status: dict[str, Any] | None = None


class AgentPeople(BaseModel):
    users: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    groups: list[uuid.UUID] = Field(default_factory=list, max_length=200)


class AgentPatch(BaseModel):
    tags: list[str] | None = None
    segment: str | None = Field(None, max_length=80)
    # A route id; "" takes it off. Left out: unchanged.
    route: str | None = None
    max_concurrency: int | None = None
    # Limit it to these projects' jobs; null lifts the limit (any project). Left out: unchanged.
    project_ids: list[uuid.UUID] | None = None
    # Only these people (and groups' members) may run work on it; null: everyone. Admins always may.
    # Left out: unchanged.
    people: AgentPeople | None = None


class AgentTokenIn(BaseModel):
    label: str = Field("", max_length=128)
    tags: list[str] = Field(default_factory=list)
    # The agent that enrolls with it runs only these projects' jobs; null: any project.
    project_ids: list[uuid.UUID] | None = None


class AgentTokenOut(ORM):
    id: uuid.UUID
    label: str
    prefix: str
    tags: list[str]
    project_ids: list[uuid.UUID] | None = None
    agent_id: uuid.UUID | None
    agent_name: str | None = None
    created_at: datetime
    last_used_at: datetime | None
    rotated_at: datetime | None
    revoked_at: datetime | None


class AgentTokenCreated(AgentTokenOut):
    # The token itself: returned only here, when it's created or rotated.
    token: str
    # How to start an agent with it (the server URL from CASTOR_PUBLIC_URL, if set).
    command: str = ""


class AgentTokenList(BaseModel):
    tokens: list[AgentTokenOut]
    # Whether agents may still connect with the shared CASTOR_AGENT_SECRET.
    shared_secret_allowed: bool


class ApiTokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str]
    # Limit it to these projects (ids); null: every project its owner can see.
    project_ids: list[uuid.UUID] | None = None
    # Days until it stops working; null: never.
    expires_in_days: int | None = Field(None, ge=1, le=3650)


class ApiTokenOut(ORM):
    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    project_ids: list[str] | None
    projects: list[str] = []  # their codenames, for the list
    user: UserBrief | None = None
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    last_used_ip: str
    revoked_at: datetime | None


class ApiTokenCreated(ApiTokenOut):
    # The token itself: returned only here, when it's created.
    token: str


class ApiScopeOut(BaseModel):
    name: str
    label: str
    description: str
    group: str
    role: UserRole


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
    alerts: AlertRules | None = None
    # Where its steps run: {agent_id}, {tags} (one agent with them), or {tags, all: true}
    # (one run per connected agent with them).
    agent_selector: dict[str, Any] = Field(default_factory=dict)
    # The network route for steps that don't name their own (a route id, or "none": direct).
    route: str | None = None


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
    # The run's card on the board.
    card_id: uuid.UUID | None = None
    tasks: list[TaskOut] = Field(default_factory=list)
    agent_selector: dict[str, Any] = Field(default_factory=dict)
    route: str | None = None
    # Started together with it on other agents (agent_selector all: one run per agent).
    sibling_run_ids: list[uuid.UUID] = Field(default_factory=list)



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
