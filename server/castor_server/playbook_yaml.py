"""Writing a playbook spec back out as YAML, for the visual editor: only what the author
would write (no defaults, no derived `kind`, no `depends_on` the references already
imply), in a readable order, with short mappings and lists on one line."""

from __future__ import annotations

from typing import Any

import yaml

from .command_spec import ArgSpec
from .playbook_spec import PlaybookStepSpec

TOP_ORDER = ["id", "name", "schema_version", "description", "tags", "inputs", "steps", "layout"]
STEP_ORDER = [
    "id", "name", "description", "command_ref", "transform", "for_each", "when", "join",
    "skip_if_empty", "on_failure", "ai", "expect", "vars", "args", "depends_on",
]
STEP_DEFAULTS: dict[str, Any] = {
    "description": "", "command_ref": None, "transform": None, "args": {}, "depends_on": [],
    "skip_if_empty": False, "vars": {}, "when": None, "join": "all", "on_failure": "stop",
    "expect": None, "for_each": None, "ai": None,
}
INPUT_ORDER = ["name", "type", "kind", "description", "required", "default", "example", "choices", "regex"]


def _ordered(d: dict[str, Any], order: list[str]) -> dict[str, Any]:
    out = {k: d[k] for k in order if k in d}
    out.update({k: v for k, v in d.items() if k not in out})
    return out


def _implied(step: dict[str, Any]) -> set[str]:
    """Steps this step references (and so depends on) without listing them."""
    try:
        parsed = PlaybookStepSpec.model_validate({**step, "depends_on": []})
    except ValueError:
        return set()
    refs = [*parsed.refs(), *parsed.condition_refs()]
    return {r.split(".")[1] for r in refs if r.startswith("steps.")} | set(parsed.watched_steps())


def _step(raw: dict[str, Any]) -> dict[str, Any]:
    step = {k: v for k, v in raw.items() if k != "kind" and STEP_DEFAULTS.get(k, object()) != v}
    if "depends_on" in step:
        implied = _implied(raw)
        step["depends_on"] = [d for d in step["depends_on"] if d not in implied]
        if not step["depends_on"]:
            del step["depends_on"]
    fe = step.get("for_each")
    if isinstance(fe, dict):
        fe = {k: v for k, v in fe.items() if not (k == "max_items" and v == 100) and v is not None}
        step["for_each"] = fe["items"] if set(fe) == {"items"} else fe
    ai = step.get("ai")
    if isinstance(ai, dict):
        from .playbook_spec import AiStepConfig

        defaults = AiStepConfig().model_dump()
        # Defaults left out; an all-default block stays as `ai: {}` (it was given).
        step["ai"] = {k: v for k, v in ai.items() if defaults.get(k, object()) != v}
    tr = step.get("transform")
    if isinstance(tr, dict):
        tr = dict(tr)
        out = {k: v for k, v in (tr.get("output") or {}).items() if v is not None}
        if out in ({}, {"name": "items"}):
            tr.pop("output", None)
        else:
            tr["output"] = out
        if not tr.get("ops"):
            tr.pop("ops", None)
        step["transform"] = tr
    return _ordered(step, STEP_ORDER)


def _input(raw: dict[str, Any]) -> dict[str, Any]:
    defaults = {name: f.get_default(call_default_factory=True) for name, f in ArgSpec.model_fields.items()}
    inp = {k: v for k, v in raw.items() if k not in defaults or defaults[k] != v or k == "name"}
    return _ordered(inp, INPUT_ORDER)


def authoring_dict(spec: dict[str, Any]) -> dict[str, Any]:
    """The spec as an author would write it. Works on unvalidated dicts too."""
    d = {k: v for k, v in spec.items() if v not in ("", [], {}, None) or k in ("id", "name", "steps")}
    if d.get("schema_version") == 1:
        del d["schema_version"]
    d["steps"] = [_step(s) for s in spec.get("steps") or [] if isinstance(s, dict)]
    if d.get("inputs"):
        d["inputs"] = [_input(i) for i in d["inputs"] if isinstance(i, dict)]
    if d.get("layout"):
        d["layout"] = {k: {"x": round(float(p["x"])), "y": round(float(p["y"]))} for k, p in d["layout"].items()}
    return _ordered(d, TOP_ORDER)


class _Dumper(yaml.SafeDumper):
    pass


def _short(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _represent_dict(dumper: yaml.SafeDumper, data: dict[str, Any]) -> yaml.Node:
    flow = 0 < len(data) <= 4 and all(_short(v) for v in data.values()) and len(repr(data)) <= 70
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items(), flow_style=flow)


def _represent_list(dumper: yaml.SafeDumper, data: list[Any]) -> yaml.Node:
    flow = 0 < len(data) and all(_short(v) for v in data) and len(repr(data)) <= 70
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flow)


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(dict, _represent_dict)
_Dumper.add_representer(list, _represent_list)
_Dumper.add_representer(str, _represent_str)


def to_yaml(spec: dict[str, Any]) -> str:
    """YAML for a spec dict, blank lines between steps for readability."""
    text = yaml.dump(authoring_dict(spec), Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)
    return text.replace("\n- id: ", "\n\n- id: ").replace("\nsteps:\n\n", "\nsteps:\n")
