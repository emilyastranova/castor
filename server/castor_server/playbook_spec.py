"""The playbook definition format, parsed from YAML or JSON."""

from __future__ import annotations

import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .command_spec import ID_PATTERN, ArgSpec
from .playbook_conditions import FINAL_STATUSES, Condition, Expect
from .playbook_transforms import TransformSpec

STEP_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
PLACEHOLDER_RE = re.compile(r"^\{\{\s*(inputs\.[A-Za-z0-9_]+|steps\.[A-Za-z0-9_-]+\.outputs\.[A-Za-z0-9_]+)\s*\}\}$")
INLINE_PLACEHOLDER_RE = re.compile(r"\{\{\s*(inputs\.[A-Za-z0-9_]+|steps\.[A-Za-z0-9_-]+\.outputs\.[A-Za-z0-9_]+)\s*\}\}")
# {{ item }} / {{ item.field }} in the args of a for_each step: the current item.
ITEM_RE = re.compile(r"\{\{\s*item(?:\.([A-Za-z_][A-Za-z0-9_]*))?\s*\}\}")
MAX_FOR_EACH = 1000
_NO_ITEM = object()


def compact_errors(exc: ValidationError) -> str:
    return "; ".join(
        (".".join(str(p) for p in err["loc"]) + ": " if err["loc"] else "")
        + ("unknown key" if err["type"] == "extra_forbidden" else err["msg"].removeprefix("Value error, "))
        for err in exc.errors()
    )


def references(value: Any) -> list[str]:
    """Every `inputs.x` / `steps.y.outputs.z` reference inside a binding value."""
    if isinstance(value, str):
        return INLINE_PLACEHOLDER_RE.findall(value)
    if isinstance(value, list):
        return [ref for item in value for ref in references(item)]
    return []


class ForEach(BaseModel):
    """Run a command step once per item of a list: one card and one job per item."""

    model_config = ConfigDict(extra="forbid")

    # Exactly one `{{ inputs.x }}` or `{{ steps.y.outputs.z }}` reference to a list.
    items: str
    # Refuse more items than this (filter or `limit` them in a transform first).
    max_items: int = Field(100, ge=1, le=MAX_FOR_EACH)
    # At most this many of the items' jobs queued or running at once (default: no limit).
    concurrency: int | None = Field(None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def short_form(cls, data: Any) -> Any:
        return {"items": data} if isinstance(data, str) else data

    @field_validator("items")
    @classmethod
    def one_reference(cls, value: str) -> str:
        if not PLACEHOLDER_RE.match(value.strip()):
            raise ValueError("items must be one {{ inputs.<name> }} or {{ steps.<id>.outputs.<name> }} reference")
        return value.strip()


def uses_item(value: Any) -> bool:
    if isinstance(value, str):
        return bool(ITEM_RE.search(value))
    if isinstance(value, list):
        return any(uses_item(v) for v in value)
    return False


def item_value(item: Any, field: str | None) -> Any:
    if field is None:
        return item
    return item.get(field) if isinstance(item, dict) else None


def item_label(item: Any) -> str:
    """A short name for an item, for its card title."""
    text = resolve_output_value(item)
    return text if len(text) <= 60 else text[:57] + "…"


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
    # Command variable slots → `{{ inputs.<varset input> }}` or the name of a variable set.
    vars: dict[str, str] = Field(default_factory=dict)
    # Run only when this condition holds (playbook_conditions.Condition); otherwise the step
    # is "not_taken", and so are the steps that need it (unless they join: any/always).
    when: Any = None
    # When the step starts. all: every dependency succeeded or was skipped. any: every
    # dependency has ended and at least one succeeded (where branches meet). always: every
    # dependency has ended, whatever happened (reports, clean-up).
    join: Literal["all", "any", "always"] = "all"
    # What a failure means (a failed job, a step that can't start, a transform error, or an
    # `expect` that doesn't hold): stop (dependents are blocked), continue (dependents go
    # ahead with whatever exists), or ask (pause the step and notify people to decide).
    on_failure: Literal["stop", "continue", "ask"] = "stop"
    # Command steps: what a successful result should look like (playbook_conditions.Expect).
    expect: dict[str, Any] | None = None
    # Command steps: one card and job per item of a list; args use {{ item }} / {{ item.f }}.
    for_each: ForEach | None = None

    @field_validator("when")
    @classmethod
    def check_when(cls, value: Any) -> Any:
        if value is not None:
            try:
                Condition.model_validate(value)
            except ValidationError as exc:
                raise ValueError(compact_errors(exc)) from exc
        return value

    @field_validator("expect")
    @classmethod
    def check_expect(cls, value: Any) -> Any:
        if value is not None:
            try:
                Expect.model_validate(value)
            except ValidationError as exc:
                raise ValueError(compact_errors(exc)) from exc
        return value

    def condition(self) -> Condition | None:
        return Condition.model_validate(self.when) if self.when is not None else None

    def expectation(self) -> Expect | None:
        return Expect.model_validate(self.expect) if self.expect is not None else None

    def watched_steps(self) -> list[str]:
        cond = self.condition()
        return cond.watched_steps() if cond else []

    def condition_refs(self) -> list[str]:
        cond = self.condition()
        return cond.refs() if cond else []

    @model_validator(mode="after")
    def check_kind(self) -> PlaybookStepSpec:
        if (self.command_ref is None) == (self.transform is None):
            raise ValueError(f"step {self.id}: give either command_ref or transform")
        self.kind = "transform" if self.transform is not None else "command"
        if self.transform is not None and not self.transform.inputs:
            raise ValueError(f"step {self.id}: the transform has no input (connect a step to it)")
        if self.transform is not None and self.args:
            raise ValueError(f"step {self.id}: transform steps take no args (use transform.input)")
        if self.transform is not None and self.vars:
            raise ValueError(f"step {self.id}: transform steps take no vars")
        if self.transform is not None and self.for_each is not None:
            raise ValueError(f"step {self.id}: for_each is for command steps (a transform already works on the whole list)")
        if self.for_each is None and uses_item(list(self.args.values())):
            raise ValueError(f"step {self.id}: {{{{ item }}}} can only be used in a step with for_each")
        if self.transform is not None and self.expect is not None:
            raise ValueError(f"step {self.id}: expect is for command steps (a transform's result is what you made it)")
        for slot, value in self.vars.items():
            refs = references(value)
            if refs and (not PLACEHOLDER_RE.match(str(value).strip()) or not refs[0].startswith("inputs.")):
                raise ValueError(
                    f"step {self.id}: vars.{slot} must be {{{{ inputs.<name> }}}} or a variable set name"
                )
        return self

    def refs(self) -> list[str]:
        if self.transform is not None:
            return references(self.transform.inputs)
        refs = references(list(self.args.values()))
        if self.for_each is not None:
            refs = [*references(self.for_each.items), *refs]
        return refs

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


class NodePosition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float
    y: float


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
    # Where the visual editor placed each step ({step_id: {x, y}}); only for drawing.
    layout: dict[str, NodePosition] = Field(default_factory=dict)

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
        inputs_by_name = {i.name: i for i in self.inputs}
        for inp in self.inputs:
            if inp.type == "varset" and not inp.kind:
                raise ValueError(f"input {inp.name}: varset inputs need a kind")
        for step in self.steps:
            for slot, value in step.vars.items():
                for ref in references(value):
                    inp = inputs_by_name.get(ref.split(".")[1])
                    if inp is None:
                        raise ValueError(f"step {step.id} uses undeclared input {ref.split('.')[1]}")
                    if inp.type != "varset":
                        raise ValueError(f"step {step.id}: vars.{slot} needs a varset input, {inp.name} is {inp.type}")
        for step in self.steps:
            for ref in [*step.refs(), *step.condition_refs()]:
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
            # A condition waits for what it looks at: referenced outputs and watched steps.
            cond_steps = [r.split(".")[1] for r in step.condition_refs() if r.startswith("steps.")]
            for upstream in [*cond_steps, *step.watched_steps()]:
                if upstream not in by_id:
                    raise ValueError(f"step {step.id}: when refers to unknown step {upstream}")
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

        # Positions of steps that no longer exist are dropped rather than refused.
        self.layout = {k: v for k, v in self.layout.items() if k in step_ids}

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
        raise ValueError(compact_errors(exc)) from exc


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
    item: Any = _NO_ITEM,
) -> dict[str, Any]:
    """Resolve argument templates for a step from playbook inputs and upstream outputs (and
    the current item of a for_each step)."""
    resolved: dict[str, Any] = {}
    for arg_name, raw_val in arg_bindings.items():
        if isinstance(raw_val, str) and item is not _NO_ITEM and ITEM_RE.search(raw_val):
            raw_val = ITEM_RE.sub(lambda mt: resolve_output_value(item_value(item, mt.group(1))), raw_val)
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


def resolve_value(value: Any, inputs: dict[str, Any], step_outputs: dict[str, dict[str, Any]]) -> Any:
    """A condition's `value`: the raw value behind exactly one reference (so lists stay
    lists), references inside text substituted as in arguments, anything else as written."""
    if not isinstance(value, str):
        return value
    exact = PLACEHOLDER_RE.match(value.strip())
    if exact:
        return lookup_ref(exact.group(1), inputs, step_outputs)
    return INLINE_PLACEHOLDER_RE.sub(
        lambda mt: str(resolve_output_value(lookup_ref(mt.group(1), inputs, step_outputs))), value
    )


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
# Every way a step can end. `attention` (waiting for a person) is not one of them.
FINAL = frozenset({*FINAL_STATUSES, "blocked"})


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


def satisfies(step: PlaybookStepSpec, status: str) -> bool:
    """Whether this step's status lets a step that needs it go ahead."""
    return status in SATISFIED or (status == "failed" and step.on_failure == "continue")


def handled_steps(spec: PlaybookSpec) -> set[str]:
    """Steps another step's condition watches: their failure is planned for."""
    return {w for step in spec.steps for w in step.watched_steps()}


def readiness(
    step: PlaybookStepSpec,
    status_of: Any,
    by_id: dict[str, PlaybookStepSpec],
    handled: set[str] | frozenset[str] = frozenset(),
) -> str:
    """Whether a pending step can start, judging only its dependencies (not its `when`):
    ready, wait, blocked (it never will) or not_taken (a branch it needs wasn't taken).
    A handled failure (another step's condition watches for it) acts like a branch not
    taken: the failure path runs instead, and the normal path is left out, not blocked."""
    deps = {d: status_of(d) for d in step.depends_on if d in by_id}
    if step.join == "always":
        return "ready" if all(s in FINAL for s in deps.values()) else "wait"
    watched = set(step.watched_steps())
    if step.join == "any":
        if not all(s in FINAL for s in deps.values()):
            return "wait"
        if not deps or any(d in watched or satisfies(by_id[d], s) for d, s in deps.items()):
            return "ready"
        return "not_taken" if all(s == "not_taken" for s in deps.values()) else "blocked"
    verdict = "ready"
    for dep, s in deps.items():
        if dep in watched:
            # A status test waits for the step to end, however it ends.
            if s not in FINAL and verdict == "ready":
                verdict = "wait"
        elif satisfies(by_id[dep], s):
            continue
        elif s == "failed" and dep in handled:
            verdict = "not_taken"
        elif s in BLOCKING:
            return "blocked"
        elif s == "not_taken":
            verdict = "not_taken"
        elif verdict == "ready":
            verdict = "wait"
    return verdict


def step_statuses(
    spec: PlaybookSpec,
    job_status_by_step: dict[str, str | None],
    step_results: dict[str, Any],
    run_status: str = "running",
    job_id_by_step: dict[str, str] | None = None,
) -> dict[str, str]:
    """Status of every step: pending, queued, running, succeeded, failed, cancelled, skipped,
    not_taken (its condition was false, or a branch it needs wasn't taken), attention
    (paused for a person to decide) or blocked (an upstream step failed and it never runs).

    A command step's latest job wins over its stored result (re-running a card moves the
    step on), unless the result was recorded for that very job: a judgement on it such as
    an `expect` that didn't hold or a person's decision. Transform and skipped steps only
    have stored results."""
    by_id = {s.id: s for s in spec.steps}
    handled = handled_steps(spec)
    job_ids = job_id_by_step or {}
    out: dict[str, str] = {}

    def own(step: PlaybookStepSpec) -> str:
        result = step_results.get(step.id) or {}
        if step.kind == "command":
            job_value = job_step_status(job_status_by_step.get(step.id))
            if job_value is not None:
                judged = result.get("job_id")
                if judged and result.get("status") and judged == job_ids.get(step.id):
                    return result["status"]
                return job_value
        return result.get("status") or "pending"

    def status(step_id: str, seen: frozenset[str] = frozenset()) -> str:
        if step_id in out:
            return out[step_id]
        if step_id in seen:
            return "pending"
        step = by_id[step_id]
        value = own(step)
        if value == "pending":
            if run_status == "cancelled":
                value = "cancelled"
            else:
                gate = readiness(step, lambda d: status(d, seen | {step_id}), by_id, handled)
                if gate in ("blocked", "not_taken"):
                    value = gate
        out[step_id] = value
        return value

    for step in spec.steps:
        status(step.id)
    return out


def run_outcome(spec: PlaybookSpec, statuses: dict[str, str]) -> str:
    """running while anything can still move (or waits for a person), else succeeded when
    every step ended well (a failure counts as fine when the step continues on failure or
    another step's condition handles it), else failed."""
    if any(s in ACTIVE or s in ("pending", "attention") for s in statuses.values()):
        return "running"
    handled = handled_steps(spec)
    for step in spec.steps:
        s = statuses.get(step.id, "pending")
        if s in SATISFIED or s == "not_taken":
            continue
        if s == "failed" and (step.on_failure == "continue" or step.id in handled):
            continue
        return "failed"
    return "succeeded"
