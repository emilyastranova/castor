"""The playbook definition format, parsed from YAML or JSON."""

from __future__ import annotations

import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .command_spec import ID_PATTERN, ArgSpec
from .playbook_transforms import TransformSpec

STEP_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
PLACEHOLDER_RE = re.compile(r"^\{\{\s*(inputs\.[A-Za-z0-9_]+|steps\.[A-Za-z0-9_-]+\.outputs\.[A-Za-z0-9_]+)\s*\}\}$")
INLINE_PLACEHOLDER_RE = re.compile(r"\{\{\s*(inputs\.[A-Za-z0-9_]+|steps\.[A-Za-z0-9_-]+\.outputs\.[A-Za-z0-9_]+)\s*\}\}")


def references(value: Any) -> list[str]:
    """Every `inputs.x` / `steps.y.outputs.z` reference inside a binding value."""
    if isinstance(value, str):
        return INLINE_PLACEHOLDER_RE.findall(value)
    if isinstance(value, list):
        return [ref for item in value for ref in references(item)]
    return []


class PlaybookStepSpec(BaseModel):
    """A command step (a card the agent runs) or a transform step (computed on the server
    from upstream outputs, no card). `kind` is derived from which of the two is given."""

    # Unknown keys are errors, so a typo such as `depend_on:` can't be silently ignored.
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    kind: Literal["command", "transform"] = "command"
    command_ref: str | None = None
    transform: TransformSpec | None = None
    description: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    # Steps it waits for. References in args/transform input are added automatically.
    depends_on: list[str] = Field(default_factory=list)
    # Don't run when a value bound from an upstream step is empty; the step is marked
    # skipped, which still satisfies its dependents.
    skip_if_empty: bool = False

    @model_validator(mode="after")
    def check_kind(self) -> PlaybookStepSpec:
        if (self.command_ref is None) == (self.transform is None):
            raise ValueError(f"step {self.id}: give either command_ref or transform")
        self.kind = "transform" if self.transform is not None else "command"
        if self.transform is not None and self.args:
            raise ValueError(f"step {self.id}: transform steps take no args (use transform.input)")
        return self

    def refs(self) -> list[str]:
        if self.transform is not None:
            return references(self.transform.inputs)
        return references(list(self.args.values()))

    def upstream_steps(self) -> list[str]:
        return [*dict.fromkeys(r.split(".")[1] for r in self.refs() if r.startswith("steps."))]

    @field_validator("id")
    @classmethod
    def check_id(cls, value: str) -> str:
        if not STEP_ID_PATTERN.match(value):
            raise ValueError(
                "step id must be 1-63 chars of lowercase letters, digits, hyphens and underscores"
            )
        return value


class PlaybookSpec(BaseModel):
    # Unknown keys are errors, except `x-...` keys, which are free for YAML anchors/notes.
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    schema_version: int = 1
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    inputs: list[ArgSpec] = Field(default_factory=list)
    steps: list[PlaybookStepSpec] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def drop_extension_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not str(k).startswith("x-")}
        return data

    @field_validator("id")
    @classmethod
    def check_id(cls, value: str) -> str:
        if not ID_PATTERN.match(value):
            raise ValueError(
                "playbook id must be 1-63 chars of lowercase letters, digits and hyphens"
            )
        return value

    @model_validator(mode="after")
    def validate_dag(self) -> PlaybookSpec:
        step_ids = set()
        for step in self.steps:
            if step.id in step_ids:
                raise ValueError(f"duplicate step id: {step.id}")
            step_ids.add(step.id)

        # References: declared inputs, known steps, transform output names; each referenced
        # step becomes a dependency so the graph can't disagree with the bindings.
        by_id = {s.id: s for s in self.steps}
        input_names = {i.name for i in self.inputs}
        for step in self.steps:
            for ref in step.refs():
                parts = ref.split(".")
                if parts[0] == "inputs":
                    if parts[1] not in input_names:
                        raise ValueError(f"step {step.id} uses undeclared input {parts[1]}")
                    continue
                source = by_id.get(parts[1])
                if source is None:
                    raise ValueError(f"step {step.id} references unknown step {parts[1]}")
                if source.transform is not None and parts[3] != source.transform.output.name:
                    raise ValueError(
                        f"step {step.id} references {parts[1]}.outputs.{parts[3]}, but that "
                        f"transform's output is named {source.transform.output.name}"
                    )
            for upstream in step.upstream_steps():
                if upstream not in step.depends_on:
                    step.depends_on.append(upstream)

        # Validate dependencies exist
        for step in self.steps:
            for dep in step.depends_on:
                if dep not in step_ids:
                    raise ValueError(
                        f"step {step.id} depends on unknown step {dep}"
                    )
                if dep == step.id:
                    raise ValueError(f"step {step.id} cannot depend on itself")

        # Cycle detection via topological sort (Kahn's algorithm)
        in_degree = {s.id: len(s.depends_on) for s in self.steps}
        adj: dict[str, list[str]] = {s.id: [] for s in self.steps}
        for step in self.steps:
            for dep in step.depends_on:
                adj[dep].append(step.id)

        queue = [s_id for s_id, deg in in_degree.items() if deg == 0]
        visited_count = 0
        while queue:
            node = queue.pop(0)
            visited_count += 1
            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited_count < len(self.steps):
            raise ValueError("playbook steps contain a cycle or circular dependency")

        return self


def parse_playbook_spec(source: str | dict[str, Any]) -> PlaybookSpec:
    if isinstance(source, str):
        try:
            raw = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("playbook must be a YAML mapping")  # noqa: TRY004 - callers handle ValueError
    else:
        raw = source

    try:
        return PlaybookSpec.model_validate(raw)
    except ValidationError as exc:
        msg = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: "
            + ("unknown key" if err["type"] == "extra_forbidden" else err["msg"].removeprefix("Value error, "))
            for err in exc.errors()
        )
        raise ValueError(msg) from exc


def resolve_output_value(val: Any) -> Any:
    """Format an upstream output for use in command arguments.
    If it is a list of hosts, extract their IPs.
    If it is a list of services, extract their host:port.
    If it is a list of strings, join with comma or return list.
    """
    if isinstance(val, list):
        if not val:
            return ""
        if isinstance(val[0], dict) and "ip" in val[0]:
            # list<host>
            ips = [h["ip"] for h in val if isinstance(h, dict) and h.get("ip")]
            return ",".join(ips)
        if isinstance(val[0], dict) and "host" in val[0]:
            # list<service> or list<smb_host>
            hosts = [h["host"] for h in val if isinstance(h, dict) and h.get("host")]
            return ",".join(dict.fromkeys(hosts))
        if isinstance(val[0], dict) and "url" in val[0]:
            # list<url>
            urls = [u["url"] for u in val if isinstance(u, dict) and u.get("url")]
            return ",".join(dict.fromkeys(urls))
        return ",".join(str(item) for item in val)
    if isinstance(val, dict):
        for key in ("ip", "host", "url"):
            if key in val:
                return str(val[key])
        return str(val)
    return str(val) if val is not None else ""


def resolve_step_args(
    arg_bindings: dict[str, Any],
    inputs: dict[str, Any],
    step_outputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Resolve argument templates for a step from playbook inputs and upstream outputs."""
    resolved: dict[str, Any] = {}
    for arg_name, raw_val in arg_bindings.items():
        if isinstance(raw_val, str):
            # Check exact placeholder match: e.g. {{ inputs.targets }}
            exact = PLACEHOLDER_RE.match(raw_val.strip())
            if exact:
                ref = exact.group(1)
                if ref.startswith("inputs."):
                    input_key = ref.removeprefix("inputs.")
                    resolved[arg_name] = inputs.get(input_key, "")
                elif ref.startswith("steps."):
                    parts = ref.split(".")
                    # steps.<step_id>.outputs.<output_name>
                    if len(parts) == 4 and parts[2] == "outputs":
                        s_id = parts[1]
                        o_name = parts[3]
                        raw_output = step_outputs.get(s_id, {}).get(o_name)
                        resolved[arg_name] = resolve_output_value(raw_output)
                    else:
                        resolved[arg_name] = raw_val
                continue

            # Inline placeholders substitution
            def repl(m: re.Match) -> str:
                ref = m.group(1)
                if ref.startswith("inputs."):
                    return str(inputs.get(ref.removeprefix("inputs."), ""))
                if ref.startswith("steps."):
                    parts = ref.split(".")
                    if len(parts) == 4 and parts[2] == "outputs":
                        raw_output = step_outputs.get(parts[1], {}).get(parts[3])
                        return str(resolve_output_value(raw_output))
                return m.group(0)

            resolved[arg_name] = INLINE_PLACEHOLDER_RE.sub(repl, raw_val)
        else:
            resolved[arg_name] = raw_val
    return resolved


def lookup_ref(ref: str, inputs: dict[str, Any], step_outputs: dict[str, dict[str, Any]]) -> Any:
    """The raw value behind `inputs.x` or `steps.y.outputs.z` (None when not produced)."""
    parts = ref.split(".")
    if parts[0] == "inputs":
        return inputs.get(parts[1])
    return step_outputs.get(parts[1], {}).get(parts[3])


def has_empty_upstream(
    step: PlaybookStepSpec, inputs: dict[str, Any], step_outputs: dict[str, dict[str, Any]]
) -> bool:
    """True when any value this step takes from an upstream step is missing or empty."""
    for ref in step.refs():
        if ref.startswith("steps.") and lookup_ref(ref, inputs, step_outputs) in (None, "", [], {}):
            return True
    return False


# ---------- step status ----------

# A dependency is satisfied by these; any of BLOCKING upstream means the step can't run.
SATISFIED = frozenset({"succeeded", "skipped"})
BLOCKING = frozenset({"failed", "cancelled", "blocked"})
ACTIVE = frozenset({"queued", "running"})


def job_step_status(job_status: str | None) -> str | None:
    """Map a JobStatus value onto a step status."""
    return {
        "queued": "queued",
        "dispatched": "queued",
        "running": "running",
        "succeeded": "succeeded",
        "failed": "failed",
        "lost": "failed",
        "cancelled": "cancelled",
    }.get(job_status or "")


def step_statuses(
    spec: PlaybookSpec,
    job_status_by_step: dict[str, str | None],
    step_results: dict[str, Any],
    run_status: str = "running",
) -> dict[str, str]:
    """Status of every step: pending, queued, running, succeeded, failed, cancelled, skipped
    or blocked (an upstream step failed, was cancelled or is itself blocked).

    A command step's latest job wins over its stored result, so re-running a failed card
    moves the step on; transform and skipped steps only have stored results."""
    by_id = {s.id: s for s in spec.steps}
    out: dict[str, str] = {}

    def status(step_id: str, seen: frozenset[str] = frozenset()) -> str:
        if step_id in out:
            return out[step_id]
        step = by_id[step_id]
        value = job_step_status(job_status_by_step.get(step_id)) if step.kind == "command" else None
        if value is None:
            value = (step_results.get(step_id) or {}).get("status") or "pending"
        if value == "pending":
            deps = [d for d in step.depends_on if d in by_id and d not in seen]
            if run_status == "cancelled":
                value = "cancelled"
            elif any(status(d, seen | {step_id}) in BLOCKING for d in deps):
                value = "blocked"
        out[step_id] = value
        return value

    for step in spec.steps:
        status(step.id)
    return out
