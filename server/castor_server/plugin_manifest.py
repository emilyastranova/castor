"""A plugin's manifest, `castor-plugin.yaml` (docs/PLUGINS.md): who it is, what it runs, what it
asks for. Castor refuses a manifest it doesn't understand rather than guessing."""

from __future__ import annotations

import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .api_tokens import SCOPES

MANIFEST = "castor-plugin.yaml"
ID = r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$"
VERSION = r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$"
# Scopes a plugin can't have: administering Castor stays with people.
NEVER = {"admin", "agents:write"}
# What this version runs; the rest of the manifest format arrives in later phases.
SUPPORTED_RUNTIMES = {"sandboxed", "unrestricted"}
CONTEXTS = ("project", "card", "asset")


class FileSpec(BaseModel):
    """An action that takes a file."""

    model_config = ConfigDict(extra="forbid")
    accept: list[str] = Field(default_factory=list)  # extensions (".nessus") or media types
    max_mb: int = Field(50, ge=1, le=1024)
    required: bool = True


class ActionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    label: str = Field(min_length=1, max_length=60)
    description: str = Field("", max_length=300)
    icon: str = Field("extension", pattern=r"^[a-z0-9_]{1,40}$")  # a Material Symbols name
    on: list[Literal["project", "card", "asset"]] = Field(default_factory=lambda: ["project"])
    # Asset types it applies to (on: asset); empty: any.
    asset_types: list[str] = Field(default_factory=list)
    file: FileSpec | None = None
    timeout_minutes: int = Field(30, ge=1, le=720)


class LibraryActionSpec(BaseModel):
    """An action that works as a library definition (docs/ACTIONS.md phase 4): usable on cards and
    as playbook steps, with typed arguments, variable sets and outputs read by parsers. Castor
    writes it into the library as `<plugin id>-<id>` (kind: plugin) while the plugin is installed;
    its handler is `ctx.actions.on(id, ...)`, like a button action's."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=20)
    # The command format's (commands/README.md): args, vars (variable slots) and outputs.
    args: list[dict[str, Any]] = Field(default_factory=list, max_length=40)
    vars: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    outputs: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    timeout_minutes: int = Field(30, ge=1, le=720)
    rate_limit: str | None = None  # a suggestion, as an HTTP action's
    # As on a command definition (commands/README.md): a dry run first, retrying only what failed,
    # a person approving each run.
    preview: dict[str, Any] | None = None
    retry_failed: dict[str, Any] | None = None
    confirm: bool = False


SLOTS = ("asset.panel", "card.tab", "project.page", "page.script")


def mcp_tool_name(plugin_id: str, name: str) -> str:
    """What assistants call a plugin's MCP tool: `<plugin id>__<name>` (two underscores: Castor's own
    tool names have one at most, so the two never meet)."""
    return f"{plugin_id}__{name}"


class McpToolSpec(BaseModel):
    """A tool the plugin adds to Castor's MCP server, answered by `ctx.mcp.on(name, handler)`.
    Assistants see it as `<plugin id>__<name>`, in the plugin's projects (a `project_id` input is
    added to its schema), once an admin approved it."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    description: str = Field(min_length=1, max_length=1000)
    # JSON Schema of its arguments (an object); `project_id` is Castor's and added for it.
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    # Changes nothing: viewers and read-only API tokens may call it.
    read_only: bool = False
    # Removes or overwrites something (told to the assistant's client, which may ask first).
    destructive: bool = False
    timeout_seconds: int = Field(60, ge=1, le=600)

    @field_validator("input_schema")
    @classmethod
    def object_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        import json

        if value.get("type", "object") != "object" or not isinstance(value.get("properties", {}), dict):
            raise ValueError("input_schema: a JSON Schema of type object with properties")
        if "project_id" in (value.get("properties") or {}):
            raise ValueError("input_schema: project_id is Castor's (it's added for the tool)")
        if len(json.dumps(value)) > 20_000:
            raise ValueError("input_schema: too large")
        return value


class UiSpec(BaseModel):
    """A panel the plugin shows in Castor: an HTML page in the plugin, in a sandboxed iframe. Or,
    for unrestricted plugins only, `page.script`: a script that runs in Castor's own page."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    # Where: a panel in an asset's dialog, a tab on a card, or a page of its own in a project.
    slot: Literal["asset.panel", "card.tab", "project.page", "page.script"]
    title: str = Field(min_length=1, max_length=60)
    path: str = Field(pattern=r"^[A-Za-z0-9_./-]+\.(html?|m?js)$")  # the page (or page.script's script), in the plugin
    icon: str = Field("extension", pattern=r"^[a-z0-9_]{1,40}$")  # a Material Symbols name
    # asset.panel: the asset types it's shown for (empty: every type).
    asset_types: list[str] = Field(default_factory=list)
    # Its height to start with, in pixels; the page can resize itself.
    height: int = Field(320, ge=80, le=2000)

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        if v.startswith("/") or ".." in v.split("/"):
            raise ValueError("a path inside the plugin")
        return v


class SettingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=80)
    description: str = Field("", max_length=300)
    type: Literal["text", "number", "bool", "select", "secret"] = "text"
    default: Any = None
    options: list[str] = Field(default_factory=list)  # for select
    required: bool = False
    # Whether an admin can make it each person's own (an API key everyone brings): allowed (the
    # admin chooses), required (always each person's), never (one value for everyone).
    per_user: Literal["allowed", "required", "never"] = "allowed"
    # The address of a system the plugin talks to (a Salt master's API): a sandboxed plugin may reach
    # the host an admin sets here (`network_host`), on top of its manifest's `network`. Shared only.
    network: bool = False

    @model_validator(mode="after")
    def _check(self) -> SettingSpec:
        if self.network and (self.type != "text" or self.per_user == "required"):
            raise ValueError(f"{self.name}: a network setting is a text setting everyone shares")
        if self.type == "select" and not self.options:
            raise ValueError(f"{self.name}: a select needs options")
        if self.type == "secret" and self.default not in (None, ""):
            raise ValueError(f"{self.name}: a secret can't have a default")
        return self


# Names fit the database columns (assets.type is 16 characters, asset_links.kind 24).
TYPE_NAME = r"^[a-z][a-z0-9_]{1,15}$"
KIND_NAME = r"^[a-z][a-z0-9_]{1,23}$"


class HookSpec(BaseModel):
    """A decision Castor asks the plugin's code for while it works (plugin_hooks.py, SDK
    `ctx.hooks.on(kind, …)`), with a deadline. `job.prepare`: just before one of the plugin's own
    commands is sent, its input files and environment (an inventory from the project's assets).
    `assets.map`: after one of its own parsers read a run, the assets and relationships that is."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["job.prepare", "assets.map"]
    # job.prepare: which of the plugin's commands (its library/commands ids); empty: all of them.
    commands: list[str] = Field(default_factory=list, max_length=50)
    # assets.map: which of the plugin's parsers (library/parsers ids); empty: all of them.
    parsers: list[str] = Field(default_factory=list, max_length=50)
    timeout_seconds: int = Field(15, ge=1, le=60)


class AgentTypeSpec(BaseModel):
    """A kind of agent the plugin adds (plugin_agents.py): the machines a system it talks to already
    manages (Salt minions, Puppet nodes), each one a Castor agent. The plugin lists them and runs
    jobs on them (SDK `ctx.agents.on`); the plugin's settings say how to reach the system."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    label: str = Field(min_length=1, max_length=60)
    description: str = Field("", max_length=300)
    icon: str = Field("dns", pattern=r"^[a-z0-9_]{1,40}$")  # a Material Symbols name
    # What it runs: shell commands always; containers when its machines have podman or docker.
    containers: bool = False
    # Jobs at once on each machine, to start with (admins change it per agent).
    max_concurrency: int = Field(1, ge=1, le=64)
    # How often Castor asks it for its machines (their tags, whether they're there).
    refresh_minutes: int = Field(10, ge=1, le=1440)


class AssetFieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=60)
    type: Literal["text", "number", "bool", "list"] = "text"


class AssetTypeSpec(BaseModel):
    """A kind of asset the plugin adds (a source file, a cloud account…): it's an asset like any
    other, in lists, search, the graph, triggers and arguments."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=TYPE_NAME)
    label: str = Field(min_length=1, max_length=40)
    plural: str = Field("", max_length=40)
    icon: str = Field("category", pattern=r"^[a-z0-9_]{1,40}$")  # a Material Symbols name
    description: str = Field("", max_length=300)
    fields: list[AssetFieldSpec] = Field(min_length=1, max_length=40)
    # The fields that identify one (all must be set): the same values are the same asset.
    identity: list[str] = Field(min_length=1, max_length=4)
    # How its label reads, from its fields: "{repo}/{path}" (default: the identity, joined).
    label_template: str = Field("", max_length=200)

    @model_validator(mode="after")
    def _check(self) -> AssetTypeSpec:
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError(f"{self.name}: two fields have the same name")
        for n in self.identity:
            if n not in names:
                raise ValueError(f"{self.name}: identity names {n}, which isn't one of its fields")
        for n in re.findall(r"\{(\w+)\}", self.label_template):
            if n not in names:
                raise ValueError(f"{self.name}: label_template names {n}, which isn't one of its fields")
        return self


class LinkDetailSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=80)


class LinkKindSpec(BaseModel):
    """A kind of relationship the plugin adds ("imports", read back as "imported by"), or, with the
    name of one Castor has and only `pairs`, more types that kind connects ("affects" a source file)."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=KIND_NAME)
    forward: str = Field("", max_length=40)
    reverse: str = Field("", max_length=40)
    pairs: list[tuple[str, str]] = Field(default_factory=list, max_length=60)  # (from type, to type); none: any two
    description: str = Field("", max_length=300)
    details: list[LinkDetailSpec] = Field(default_factory=list, max_length=12)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=ID)
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(pattern=VERSION)
    castor: str = Field(">=0.1", max_length=40)  # the Castor versions it works with
    publisher: str = Field("", max_length=80)
    description: str = Field("", max_length=500)
    homepage: str = Field("", max_length=300)
    license: str = Field("", max_length=60)
    # Its code, run by Deno. None: a plugin with no code (a pack of library content); a release
    # with a main.ts and no `main:` runs that.
    main: str | None = Field(None, pattern=r"^[A-Za-z0-9_./-]+\.(ts|js|mts|mjs)$")
    # Its icon, a file in the plugin: SVG, PNG or WebP, square, up to 256 KB.
    icon: str | None = Field(None, pattern=r"^[A-Za-z0-9_./-]+\.(svg|png|webp)$")
    runtime: Literal["sandboxed", "unrestricted"] = "sandboxed"
    permissions: list[str] = Field(default_factory=list)
    network: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    actions: list[ActionSpec] = Field(default_factory=list)
    # Actions that are library definitions (kind: plugin), for cards and playbook steps.
    library_actions: list[LibraryActionSpec] = Field(default_factory=list, max_length=50)
    mcp_tools: list[McpToolSpec] = Field(default_factory=list, max_length=40)
    agent_types: list[AgentTypeSpec] = Field(default_factory=list, max_length=10)
    hooks: list[HookSpec] = Field(default_factory=list, max_length=10)
    ui: list[UiSpec] = Field(default_factory=list)
    settings: list[SettingSpec] = Field(default_factory=list)
    # Library content it ships in library/<kind>/*.yaml (plugin_library.py): read-only, removed with it.
    contents: list[Literal["commands", "tools", "parsers", "playbooks", "themes", "variable_kinds"]] = Field(default_factory=list)
    # Kinds of asset and of relationship it adds (they stay registered while it's installed).
    asset_types: list[AssetTypeSpec] = Field(default_factory=list, max_length=20)
    link_kinds: list[LinkKindSpec] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def _library_actions(self) -> Manifest:
        ids = [a.id for a in self.actions] + [a.id for a in self.library_actions]
        if len(ids) != len(set(ids)):
            raise ValueError("actions and library_actions share one set of ids (ctx.actions.on)")
        for a in self.library_actions:
            if len(f"{self.id}-{a.id}") > 63:
                raise ValueError(f"library_actions.{a.id}: “{self.id}-{a.id}” (its library id) is over 63 characters")
        names = [t.name for t in self.mcp_tools]
        if len(names) != len(set(names)):
            raise ValueError("mcp_tools: each name once")
        for t in self.mcp_tools:
            if len(mcp_tool_name(self.id, t.name)) > 64:
                raise ValueError(f"mcp_tools.{t.name}: “{mcp_tool_name(self.id, t.name)}” (its tool name) is over 64 characters")
        return self

    @field_validator("main", "icon")
    @classmethod
    def _main(cls, v: str | None) -> str | None:
        if v is not None and (v.startswith("/") or ".." in v.split("/")):
            raise ValueError("a path inside the plugin")
        return v

    @field_validator("homepage")
    @classmethod
    def _homepage(cls, v: str) -> str:
        if v and not re.match(r"^https://\S+$", v):
            raise ValueError("an https:// URL")
        return v

    @field_validator("network")
    @classmethod
    def _network(cls, v: list[str]) -> list[str]:
        """Exact hosts (with a port if it needs one): no schemes, paths or wildcards."""
        if len(v) > 20:
            raise ValueError("at most 20 hosts")
        out = []
        for host in v:
            h = str(host).strip().lower()
            if not re.fullmatch(r"(\[[0-9a-f:]+\]|[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?)*)(:\d{1,5})?", h):
                raise ValueError(f"{host}: a host name or address, optionally with :port (no https://, paths or *)")
            out.append(h)
        return sorted(set(out))

    @field_validator("events")
    @classmethod
    def _events(cls, v: list[str]) -> list[str]:
        from .plugin_events import EVENTS

        for name in v:
            if name not in EVENTS:
                raise ValueError(f"{name}: not an event plugins can get ({', '.join(EVENTS)})")
        return sorted(set(v))

    @field_validator("permissions")
    @classmethod
    def _permissions(cls, v: list[str]) -> list[str]:
        for name in v:
            if name not in SCOPES:
                raise ValueError(f"{name}: not a Castor scope ({', '.join(sorted(SCOPES))})")
            if name in NEVER:
                raise ValueError(f"{name}: plugins can't have it")
        return sorted(set(v))

    @model_validator(mode="after")
    def _unique(self) -> Manifest:
        for what, names in (("actions", [a.id for a in self.actions]), ("settings", [s.name for s in self.settings]),
                            ("ui", [u.id for u in self.ui]), ("agent_types", [t.name for t in self.agent_types])):
            if len(names) != len(set(names)):
                raise ValueError(f"{what}: two have the same id")
        for u in self.ui:
            script = u.slot == "page.script"
            if script != u.path.endswith(("js", "mjs")) or (not script and not u.path.endswith(("html", "htm"))):
                raise ValueError(f"ui.{u.id}: page.script takes a .js file, the other slots an .html page")
            if script and self.runtime != "unrestricted":
                raise ValueError(f"ui.{u.id}: page.script runs in Castor's own page, which only unrestricted plugins may do")
        if self.agent_types and self.main is None:
            raise ValueError("agent_types: a plugin with agent types needs code to run jobs (main)")
        kinds = [h.kind for h in self.hooks]
        if len(kinds) != len(set(kinds)):
            raise ValueError("hooks: each kind once")
        if self.hooks and self.main is None:
            raise ValueError("hooks: a plugin with hooks needs code to answer them (main)")
        for h in self.hooks:
            if h.kind == "job.prepare" and "commands" not in self.contents:
                raise ValueError("hooks: job.prepare prepares the plugin's own commands (contents: commands)")
            if h.kind == "assets.map" and "parsers" not in self.contents:
                raise ValueError("hooks: assets.map maps what the plugin's own parsers read (contents: parsers)")
        self._check_kinds()
        return self

    def _check_kinds(self) -> None:
        from .assets import ALIASES, CORE_LINK_KINDS, CORE_TYPES

        own = [t.name for t in self.asset_types]
        for what, names in (("asset_types", own), ("link_kinds", [k.name for k in self.link_kinds])):
            if len(names) != len(set(names)):
                raise ValueError(f"{what}: two have the same name")
        for n in own:
            if n in CORE_TYPES or n in ALIASES:
                raise ValueError(f"asset_types.{n}: Castor has its own {n}; give the plugin's another name")
        known = set(CORE_TYPES) | set(own)
        for k in self.link_kinds:
            for a, b in k.pairs:
                for t in (a, b):
                    if t not in known:
                        raise ValueError(f"link_kinds.{k.name}: {t} is neither Castor's type nor one of the plugin's")
            if k.name in CORE_LINK_KINDS:
                if k.forward or k.reverse or k.details or k.description or not k.pairs:
                    raise ValueError(f"link_kinds.{k.name}: Castor has this kind; a plugin can only add pairs to it")
                if not CORE_LINK_KINDS[k.name].pairs:
                    raise ValueError(f"link_kinds.{k.name}: it already connects any two assets")
                for a, b in k.pairs:
                    if a not in own and b not in own:
                        raise ValueError(f"link_kinds.{k.name}: {a} → {b} needs one of the plugin's own types")
            elif not k.forward:
                raise ValueError(f"link_kinds.{k.name}: how it reads (forward, and reverse from the other end)")

    def not_yet(self) -> list[str]:
        """What this manifest asks for that this Castor can't do yet (nothing, for now)."""
        return []

    def hook(self, kind: str) -> HookSpec | None:
        return next((h for h in self.hooks if h.kind == kind), None)

    def action(self, action_id: str) -> ActionSpec | None:
        return next((a for a in self.actions if a.id == action_id), None)

    def mcp_tool(self, name: str) -> McpToolSpec | None:
        return next((t for t in self.mcp_tools if t.name == name), None)

    def panel(self, ui_id: str) -> UiSpec | None:
        return next((u for u in self.ui if u.id == ui_id), None)


def parse_manifest(text: str) -> Manifest:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{MANIFEST} isn't valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{MANIFEST} is a YAML mapping (id, name, version…)")  # noqa: TRY004 (callers catch ValueError)
    for action in data.get("actions") or []:
        if isinstance(action, dict) and True in action:  # YAML 1.1 reads a bare `on:` key as true
            action["on"] = action.pop(True)
    for kind in data.get("link_kinds") or []:
        if isinstance(kind, dict) and kind.get("name") is True:  # `name: on` (Castor's "on" kind) reads as true too
            kind["name"] = "on"
    try:
        return Manifest.model_validate(data)
    except ValidationError as exc:
        raise ValueError("; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'manifest'}: {e['msg'].removeprefix('Value error, ')}" for e in exc.errors()
        )) from exc


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.match(r"^(\d+)\.(\d+)\.(\d+)", v).groups())  # type: ignore[union-attr]
