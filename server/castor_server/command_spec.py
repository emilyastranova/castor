"""The command definition format (see commands/README.md), parsed from YAML."""

from __future__ import annotations

import re
import shlex
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from . import parsers
from .http_spec import Batch, Expect, HttpRequest, Paginate, Retry, check_rate

# "varset" is for playbook inputs only (pick a variable set of `kind`); commands take
# variable sets through `vars` slots instead.
ArgType = Literal["string", "number", "boolean", "list", "varset"]

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# {{ name }} placeholders in a command template
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
# {{ slot.field }}: a field of the variable set bound to a `vars` slot
VAR_PLACEHOLDER = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)\s*\}\}")
SLOT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
# {{ work }}: the job's work directory wherever it runs (/work in a container, the job's own
# directory in a host shell or over SSH). It renders as "${CASTOR_WORK}", which every
# runtime sets, so a command doesn't need to know where it will run.
WORK = "work"
WORK_PLACEHOLDER = re.compile(r"\{\{\s*work\s*\}\}")
WORK_REF = '"${CASTOR_WORK}"'


def work_path(source: str) -> str:
    """An output source as the job-relative /work/... path artifact collection uses:
    {{ work }}/scan.xml and /work/scan.xml are the same file."""
    return WORK_PLACEHOLDER.sub("/work", source, count=1) if WORK_PLACEHOLDER.match(source) else source
# A command-line option: -k, --dns-server, or --port= (value attached to the flag).
FLAG = re.compile(r"^--?[A-Za-z0-9][A-Za-z0-9_.-]*=?$")


class ChoicesFrom(BaseModel):
    """Take an argument's dropdown options from a variable set's text or list field."""

    model_config = ConfigDict(extra="forbid")

    set: str  # the variable set's name
    field: str


class ArgSpec(BaseModel):
    name: str
    type: ArgType = "string"
    description: str = ""
    required: bool = False
    default: Any = None
    example: Any = None
    regex: str | None = None
    choices: list[str] | None = None
    # List args only: " " renders separate words, "," renders one comma-joined word.
    separator: str = " "
    # varset inputs only: the variable kind the chosen set must have.
    kind: str | None = None
    # Options read from a variable set at use time (filled into `choices` when the command
    # is loaded), so editing the set updates every command that uses it.
    choices_from: ChoicesFrom | None = None
    # Render as "<flag> <value>" (or just "<flag>" for a boolean that is on), and as nothing
    # at all when the value is empty or the boolean is off. "--x=" attaches the value.
    flag: str | None = None
    # Name of a boolean argument: this one is only shown, and only used, while it is on.
    show_if: str | None = None

    @model_validator(mode="after")
    def one_choice_source(self) -> ArgSpec:
        if self.choices and self.choices_from:
            raise ValueError(f"{self.name}: give choices or choices_from, not both")
        if self.flag is not None and not FLAG.match(self.flag):
            raise ValueError(f"{self.name}: flag must look like -k, --dns-server or --port=")
        if self.flag and self.flag.endswith("=") and self.type == "boolean":
            raise ValueError(f"{self.name}: a boolean flag can't take a value (drop the =)")
        return self

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        if not NAME_PATTERN.match(value):
            raise ValueError("argument names use letters, digits and underscores")
        return value

    @field_validator("regex")
    @classmethod
    def check_regex(cls, value: str | None) -> str | None:
        if value is not None:
            re.compile(value)
        return value


class VarSlot(BaseModel):
    """A variable set the command needs, e.g. {name: creds, kind: credential}; the
    template uses {{ creds.username }}."""

    name: str
    kind: str
    description: str = ""
    required: bool = True

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        if not SLOT_NAME.match(value):
            raise ValueError("slot names are lowercase letters, digits and underscores")
        return value


class OutputSpec(BaseModel):
    name: str
    # Defaults to the parser's type; without a parser the output is just a collected file.
    type: str | None = None
    parser: str | None = None
    # Path or glob in the job's work directory: {{ work }}/scan.xml (or /work/scan.xml, the
    # same); none = stdout. Stored as /work/... (work_path).
    source: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)  # parser options (Parsers page)
    # What each value of a list<string> output is, so it becomes one of the project's assets
    # (assets.py `_map_hint`): one line per domain, host, URL, `host:port` service, `host share
    # access` share, NetExec `[+]` login, or vulnerability (a CVE, a nuclei line, a check id).
    asset: Literal["host", "domain", "url", "service", "share", "login", "vulnerability"] | None = None
    # A parser definition carried inline (parser_spec.ParserSpec as JSON): how a job reads an
    # output with a draft that isn't in the library. Only set on a job's output_specs.
    parser_spec: dict[str, Any] | None = Field(None, exclude=True)

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        if not NAME_PATTERN.match(value):
            raise ValueError("output names use letters, digits and underscores")
        return value

    @field_validator("source")
    @classmethod
    def check_source(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = work_path(value.strip())
        if WORK_PLACEHOLDER.search(value):
            raise ValueError("{{ work }} can only start a source path, as in {{ work }}/scan.xml")
        return value

    @model_validator(mode="after")
    def check_parser(self) -> OutputSpec:
        if self.parser is None:
            if self.options:
                raise ValueError(f"output {self.name}: options need a parser")
            if self.source is None:
                raise ValueError(f"output {self.name}: needs a parser, a source file, or both")
            return self
        if self.parser_spec is not None:
            from .parser_spec import ParserSpec

            ParserSpec.model_validate(self.parser_spec)
            return self
        found = parsers.get(self.parser)
        if found is None:
            known = ", ".join(sorted(p.name for p in parsers.all_parsers()))
            raise ValueError(f"output {self.name}: unknown parser {self.parser!r} (known: {known})")
        try:
            found.configure(self.options)
        except parsers.OptionsError as exc:
            raise ValueError(f"output {self.name}: {exc}") from exc
        # "list" was the pre-2b spelling; the parser knows the precise type.
        if self.type in (None, "list"):
            self.type = found.type
        elif self.type != found.type:
            raise ValueError(
                f"output {self.name}: parser {self.parser} produces {found.type}, not {self.type}"
            )
        return self


def template_strings(value: Any) -> list[str]:
    """Every string in a request template (dict values, list items, nested), for checking
    which placeholders it uses."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in template_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in template_strings(v)]
    return []


ActionKind = Literal["command", "http", "mcp", "plugin"]


class PluginCall(BaseModel):
    """kind: plugin: an action a plugin defines (its manifest's `library_actions`). Castor writes
    these definitions itself when the plugin is installed; they're read-only."""

    model_config = ConfigDict(extra="forbid")

    id: str  # the plugin's id
    action: str
    timeout_minutes: int = Field(30, ge=1, le=720)

    @field_validator("id", "action")
    @classmethod
    def check_ids(cls, value: str) -> str:
        if not ID_PATTERN.match(value):
            raise ValueError("lowercase letters, digits and dashes")
        return value


class McpCall(BaseModel):
    """kind: mcp: a tool on another MCP server (an MCP connection in Settings → MCP servers)."""

    model_config = ConfigDict(extra="forbid")

    server: str  # the connection's id
    tool: str
    # The tool's arguments, as templates (a field that is only a placeholder keeps its type).
    # None: every argument of the definition, as it is.
    arguments: dict[str, Any] | None = None
    timeout_s: int = Field(120, ge=1, le=3600)

    @field_validator("server")
    @classmethod
    def check_server(cls, value: str) -> str:
        if not ID_PATTERN.match(value):
            raise ValueError("server is an MCP connection's id (lowercase letters, digits and dashes)")
        return value

    @field_validator("tool")
    @classmethod
    def check_tool(cls, value: str) -> str:
        if not value.strip() or len(value) > 128:
            raise ValueError("tool is the name of one of the server's tools")
        return value


class CommandSpec(BaseModel):
    id: str
    name: str
    schema_version: int = 1
    # What running it does (docs/ACTIONS.md): a shell command on an agent, or an HTTP request
    # the Castor server makes.
    kind: ActionKind = "command"
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    image: str | None = None
    # A tool from the library (tools/*.yaml) to run with, instead of naming an image.
    tool: str | None = None
    container_flags: list[str] = Field(default_factory=list)
    # Run in an interactive terminal (a pty): people can type into it, full-screen tools
    # (tmux, vim, htop) work, and an automated run that stops at a prompt alerts people.
    tty: bool = False
    args: list[ArgSpec] = Field(default_factory=list)
    vars: list[VarSlot] = Field(default_factory=list)
    command: str = ""
    outputs: list[OutputSpec] = Field(default_factory=list)
    # kind: http (http_spec.py).
    request: HttpRequest | None = None
    paginate: Paginate | None = None
    batch: Batch | None = None
    expect: Expect | None = None
    retry: Retry | None = None
    # A suggested limit for the API (60/minute): a card that has none of its own uses it.
    rate_limit: str | None = None
    # Ask before each run: a person approves every job of it (actions that change things outside
    # Castor, like posting to a customer's channel). A card can ask too; it can't stop asking.
    confirm: bool = False
    # kind: mcp.
    mcp: McpCall | None = None
    # kind: plugin.
    plugin: PluginCall | None = None

    @field_validator("rate_limit")
    @classmethod
    def check_rate_limit(cls, value: str | None) -> str | None:
        return check_rate(value)

    def template_text(self) -> list[str]:
        """Where placeholders can be: the command line, or every string of the request."""
        if self.kind == "http" and self.request is not None:
            return template_strings(self.request.model_dump(mode="json", by_alias=True, exclude={"timeout_s", "method", "save_as"}))
        if self.kind == "mcp" and self.mcp is not None:
            return template_strings(self.mcp.arguments or {})
        return [self.command]

    @model_validator(mode="before")
    @classmethod
    def alias_container_args(cls, data: Any) -> Any:
        if isinstance(data, dict) and "container_args" in data and "container_flags" not in data:
            data["container_flags"] = data.pop("container_args")
        return data

    @field_validator("container_flags", mode="before")
    @classmethod
    def parse_container_flags(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return shlex.split(value)
        if isinstance(value, list):
            result = []
            for item in value:
                result.extend(shlex.split(str(item)))
            return result
        return value

    @field_validator("id")
    @classmethod
    def check_id(cls, value: str) -> str:
        if not ID_PATTERN.match(value):
            raise ValueError("id uses lowercase letters, digits and dashes")
        return value

    @model_validator(mode="after")
    def image_or_tool(self) -> CommandSpec:
        if self.image and self.tool:
            raise ValueError("give image or tool, not both (a tool says which image to use)")
        return self

    @model_validator(mode="after")
    def fits_kind(self) -> CommandSpec:
        http_parts = [n for n in ("request", "paginate", "batch", "expect") if getattr(self, n) is not None]
        if self.kind == "command":
            if not self.command.strip():
                raise ValueError("command: a command needs its command line")
            if http_parts or self.retry is not None:
                raise ValueError(f"{(http_parts or ['retry'])[0]}: only for kind: http or mcp")
            if self.mcp is not None:
                raise ValueError("mcp: only for kind: mcp")
            if self.plugin is not None:
                raise ValueError("plugin: only for kind: plugin")
            return self
        shell = [n for n, on in (("command", self.command.strip()), ("image", self.image), ("tool", self.tool),
                                 ("container_flags", self.container_flags), ("tty", self.tty)) if on]
        if self.kind == "plugin":
            if self.plugin is None:
                raise ValueError("plugin: a plugin action names its plugin and action")
            if shell or http_parts or self.mcp is not None:
                what = (shell or http_parts or ["mcp"])[0]
                raise ValueError(f"{what}: a plugin action runs in its plugin; it has no {what}")
            return self
        if self.plugin is not None:
            raise ValueError("plugin: only for kind: plugin")
        if self.kind == "mcp":
            if self.mcp is None:
                raise ValueError("mcp: an MCP action needs mcp: {server: <connection>, tool: <name>}")
            if shell or http_parts:
                what = (shell or http_parts)[0]
                raise ValueError(f"{what}: an MCP action calls a tool; it has no {what}")
            return self
        if self.mcp is not None:
            raise ValueError("mcp: only for kind: mcp")
        if self.request is None:
            raise ValueError("request: an http action needs its request (method and url)")
        if shell:
            raise ValueError(f"{shell[0]}: an http action makes a request; it has no {shell[0]}")
        if self.batch is not None:
            arg = next((a for a in self.args if a.name == self.batch.arg), None)
            if arg is None or arg.type != "list":
                raise ValueError(f"batch.arg: {self.batch.arg} isn't a list argument")
        if any(WORK_PLACEHOLDER.search(t) for t in self.template_text()):
            raise ValueError("{{ work }} is for commands; an http action has no work directory")
        return self

    @model_validator(mode="after")
    def check_template(self) -> CommandSpec:
        names = [a.name for a in self.args]
        if len(names) != len(set(names)):
            raise ValueError("argument names must be unique")
        outputs = [o.name for o in self.outputs]
        if len(outputs) != len(set(outputs)):
            raise ValueError("output names must be unique")
        if WORK in names:
            raise ValueError("work is reserved: {{ work }} is the job's work directory")
        text = "\n".join(self.template_text())
        undefined = sorted(set(PLACEHOLDER.findall(text)) - set(names) - {WORK})
        if undefined:
            raise ValueError(f"template uses undefined arguments: {', '.join(undefined)}")
        varsets = [a.name for a in self.args if a.type == "varset"]
        if varsets:
            raise ValueError(f"{varsets[0]}: commands take variable sets through vars, not args")
        slots = [v.name for v in self.vars]
        if len(slots) != len(set(slots)):
            raise ValueError("vars slot names must be unique")
        if set(slots) & set(names):
            raise ValueError("a vars slot can't have the same name as an argument")
        unknown = sorted({s for s, _ in VAR_PLACEHOLDER.findall(text)} - set(slots))
        if unknown:
            raise ValueError(f"template uses undeclared vars slots: {', '.join(unknown)}")
        by_name = {a.name: a for a in self.args}
        for arg in self.args:
            if arg.show_if is None:
                continue
            control = by_name.get(arg.show_if)
            if control is None or control.name == arg.name:
                raise ValueError(f"{arg.name}: show_if names no other argument {arg.show_if}")
            if control.type != "boolean":
                raise ValueError(f"{arg.name}: show_if must name a boolean argument, {control.name} is {control.type}")
            if control.show_if:
                raise ValueError(f"{arg.name}: show_if can't point at an argument that has show_if itself")
        return self


def parse_spec(text: str) -> CommandSpec:
    """Parse YAML text into a validated CommandSpec; raises ValueError with a short message."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("expected a YAML mapping")  # noqa: TRY004 - callers handle ValueError
    try:
        return CommandSpec.model_validate(data)
    except ValidationError as exc:
        err = exc.errors()[0]
        location = ".".join(str(part) for part in err["loc"])
        message = err["msg"].removeprefix("Value error, ")
        raise ValueError(f"{location}: {message}" if location else message) from exc
