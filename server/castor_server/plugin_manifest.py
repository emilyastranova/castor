"""A plugin's manifest, `castor-plugin.yaml` (docs/PLUGINS.md): who it is, what it runs, what it
asks for. Castor refuses a manifest it doesn't understand rather than guessing, and refuses what
this version can't do yet (hooks, library contents) with a clear reason."""

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


SLOTS = ("asset.panel", "card.tab", "project.page", "page.script")


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

    @model_validator(mode="after")
    def _check(self) -> SettingSpec:
        if self.type == "select" and not self.options:
            raise ValueError(f"{self.name}: a select needs options")
        if self.type == "secret" and self.default not in (None, ""):
            raise ValueError(f"{self.name}: a secret can't have a default")
        return self


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
    main: str = Field("main.ts", pattern=r"^[A-Za-z0-9_./-]+\.(ts|js|mts|mjs)$")
    # Its icon, a file in the plugin: SVG, PNG or WebP, square, up to 256 KB.
    icon: str | None = Field(None, pattern=r"^[A-Za-z0-9_./-]+\.(svg|png|webp)$")
    runtime: Literal["sandboxed", "unrestricted"] = "sandboxed"
    permissions: list[str] = Field(default_factory=list)
    network: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    actions: list[ActionSpec] = Field(default_factory=list)
    hooks: list[dict[str, Any]] = Field(default_factory=list)
    ui: list[UiSpec] = Field(default_factory=list)
    settings: list[SettingSpec] = Field(default_factory=list)
    contents: list[str] = Field(default_factory=list)

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
                            ("ui", [u.id for u in self.ui])):
            if len(names) != len(set(names)):
                raise ValueError(f"{what}: two have the same id")
        for u in self.ui:
            script = u.slot == "page.script"
            if script != u.path.endswith(("js", "mjs")) or (not script and not u.path.endswith(("html", "htm"))):
                raise ValueError(f"ui.{u.id}: page.script takes a .js file, the other slots an .html page")
            if script and self.runtime != "unrestricted":
                raise ValueError(f"ui.{u.id}: page.script runs in Castor's own page, which only unrestricted plugins may do")
        return self

    def not_yet(self) -> list[str]:
        """What this manifest asks for that this Castor can't do yet (phase A)."""
        out = []
        for field in ("hooks", "contents"):
            if getattr(self, field):
                out.append(f"{field}: not supported yet (see docs/PLUGINS.md)")
        return out

    def action(self, action_id: str) -> ActionSpec | None:
        return next((a for a in self.actions if a.id == action_id), None)

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
    try:
        return Manifest.model_validate(data)
    except ValidationError as exc:
        raise ValueError("; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'manifest'}: {e['msg'].removeprefix('Value error, ')}" for e in exc.errors()
        )) from exc


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.match(r"^(\d+)\.(\d+)\.(\d+)", v).groups())  # type: ignore[union-attr]
