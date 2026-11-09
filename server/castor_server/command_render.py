"""Fill a command template with argument values.

Every value is validated against its ArgSpec and inserted with shlex.quote, so each value
is always exactly one shell word (or one word per list item) regardless of its content.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

from .command_spec import (
    PLACEHOLDER,
    VAR_PLACEHOLDER,
    WORK_PLACEHOLDER,
    WORK_REF,
    ArgSpec,
    CommandSpec,
)


class ArgumentError(ValueError):
    """One or more argument values are invalid; `errors` maps argument name to message."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{name}: {msg}" for name, msg in errors.items()))
        self.errors = errors


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,\n]", value) if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value)]


def _normalise(spec: ArgSpec, value: Any) -> Any:
    """Coerce a raw value to the argument's type; raise ValueError with a short message."""
    if spec.type == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "yes", "1", "on"):
            return True
        if text in ("false", "no", "0", "off", ""):
            return False
        raise ValueError("must be true or false")
    if spec.type == "number":
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError("must be a number") from None
        return int(number) if number.is_integer() else number
    if spec.type == "list":
        items = _as_list(value)
        for item in items:
            _check_text(spec, item)
        return items
    text = str(value)
    _check_text(spec, text)
    return text


def _check_text(spec: ArgSpec, text: str) -> None:
    if spec.choices and text not in spec.choices:
        raise ValueError(f"must be one of: {', '.join(spec.choices)}")
    if spec.regex and not re.fullmatch(spec.regex, text):
        raise ValueError("does not match the expected format")


def resolve_args(spec: CommandSpec, values: dict[str, Any]) -> dict[str, Any]:
    """Apply defaults, check required args and types. Returns normalised values."""
    errors: dict[str, str] = {}
    resolved: dict[str, Any] = {}
    unknown = set(values) - {a.name for a in spec.args}
    for name in sorted(unknown):
        errors[name] = "is not an argument of this command"
    hidden = _hidden_args(spec, values)
    for arg in spec.args:
        if arg.name in hidden:  # its toggle is off: not shown, not used, not required
            resolved[arg.name] = None
            continue
        raw = values.get(arg.name)
        if raw is None or (isinstance(raw, str) and raw.strip() == "") or raw == []:
            raw = arg.default
        if raw is None or raw == "" or raw == []:
            if arg.required:
                errors[arg.name] = "is required"
            resolved[arg.name] = None
            continue
        try:
            resolved[arg.name] = _normalise(arg, raw)
        except ValueError as exc:
            errors[arg.name] = str(exc)
    if errors:
        raise ArgumentError(errors)
    return resolved


def _hidden_args(spec: CommandSpec, values: dict[str, Any]) -> set[str]:
    """Arguments whose show_if toggle is off."""
    by_name = {a.name: a for a in spec.args}
    hidden = set()
    for arg in spec.args:
        control = by_name.get(arg.show_if or "")
        if control is None:
            continue
        raw = values.get(control.name)
        if raw is None or raw == "":
            raw = control.default
        try:
            on = bool(_normalise(control, raw)) if raw is not None else False
        except ValueError:
            on = False
        if not on:
            hidden.add(arg.name)
    return hidden


def _shell_words(spec: ArgSpec, value: Any) -> str:
    if value is None:
        return ""
    if spec.type == "boolean":
        if spec.flag:
            return spec.flag if value else ""
        return "true" if value else "false"
    if spec.type == "list":
        if spec.separator.strip():
            words = [shlex.quote(spec.separator.join(value))]
        else:
            words = [shlex.quote(item) for item in value]
        if not words or not value:
            return ""
    else:
        words = [shlex.quote(str(value))]
    if not spec.flag:
        return " ".join(words)
    if spec.flag.endswith("="):  # --port='445': one word
        return " ".join(f"{spec.flag}{w}" for w in words[:1]) + "".join(f" {w}" for w in words[1:])
    return f"{spec.flag} {' '.join(words)}"


def render_command(spec: CommandSpec, values: dict[str, Any]) -> str:
    """Return the command line with every placeholder replaced by its quoted value."""
    resolved = resolve_args(spec, values)
    by_name = {a.name: a for a in spec.args}
    # {{work}} first: it's no argument, and it stays a reference the runtime fills in.
    rendered = WORK_PLACEHOLDER.sub(lambda _: WORK_REF, spec.command)
    rendered = PLACEHOLDER.sub(lambda m: _shell_words(by_name[m.group(1)], resolved[m.group(1)]), rendered)
    # Variable fields become references to environment variables the agent sets at run
    # time, so no value (secret or not) is ever part of the stored command line.
    rendered = VAR_PLACEHOLDER.sub(
        lambda m: f'"${{CASTOR_VAR_{m.group(1).upper()}_{m.group(2).upper()}}}"', rendered
    )
    return re.sub(r"[ \t]{2,}", " ", rendered).strip()
