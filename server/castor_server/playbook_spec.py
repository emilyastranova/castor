"""The playbook definition format, parsed from YAML or JSON."""

from __future__ import annotations

import re
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .command_spec import ArgSpec, ID_PATTERN

STEP_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
PLACEHOLDER_RE = re.compile(r"^\{\{\s*(inputs\.[A-Za-z0-9_]+|steps\.[A-Za-z0-9_-]+\.outputs\.[A-Za-z0-9_]+)\s*\}\}$")
INLINE_PLACEHOLDER_RE = re.compile(r"\{\{\s*(inputs\.[A-Za-z0-9_]+|steps\.[A-Za-z0-9_-]+\.outputs\.[A-Za-z0-9_]+)\s*\}\}")


class PlaybookStepSpec(BaseModel):
    id: str
    name: str
    command_ref: str
    description: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def check_id(cls, value: str) -> str:
        if not STEP_ID_PATTERN.match(value):
            raise ValueError(
                "step id must be 1-63 chars of lowercase letters, digits, hyphens and underscores"
            )
        return value


class PlaybookSpec(BaseModel):
    id: str
    name: str
    schema_version: int = 1
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    inputs: list[ArgSpec] = Field(default_factory=list)
    steps: list[PlaybookStepSpec] = Field(default_factory=list)

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
            raise ValueError("playbook must be a YAML mapping")
    else:
        raw = source

    try:
        return PlaybookSpec.model_validate(raw)
    except ValidationError as exc:
        msg = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg'].removeprefix('Value error, ')}"
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
        return ",".join(str(item) for item in val)
    if isinstance(val, dict):
        if "ip" in val:
            return str(val["ip"])
        if "host" in val:
            return str(val["host"])
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
