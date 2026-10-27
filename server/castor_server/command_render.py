"""Fill a command template with argument values.

Every value is validated against its ArgSpec and inserted with shlex.quote, so each value
is always exactly one shell word (or one word per list item) regardless of its content.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

from .command_spec import PLACEHOLDER, VAR_PLACEHOLDER, ArgSpec, CommandSpec


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
    for arg in spec.args:
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


def _shell_words(spec: ArgSpec, value: Any) -> str:
    if value is None:
        return ""
    if spec.type == "boolean":
        return "true" if value else "false"
    if spec.type == "list":
        if spec.separator.strip():
            return shlex.quote(spec.separator.join(value))
        return " ".join(shlex.quote(item) for item in value)
    return shlex.quote(str(value))


def render_command(spec: CommandSpec, values: dict[str, Any]) -> str:
    """Return the command line with every placeholder replaced by its quoted value."""
    resolved = resolve_args(spec, values)
    by_name = {a.name: a for a in spec.args}
    rendered = PLACEHOLDER.sub(lambda m: _shell_words(by_name[m.group(1)], resolved[m.group(1)]), spec.command)
    # Variable fields become references to environment variables the agent sets at run
    # time, so no value (secret or not) is ever part of the stored command line.
    rendered = VAR_PLACEHOLDER.sub(
        lambda m: f'"${{CASTOR_VAR_{m.group(1).upper()}_{m.group(2).upper()}}}"', rendered
    )
    return re.sub(r"[ \t]{2,}", " ", rendered).strip()
