"""The command definition format (see commands/README.md), parsed from YAML."""

from __future__ import annotations

import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

ArgType = Literal["string", "number", "boolean", "list"]

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# {{ name }} placeholders in a command template
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


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


class OutputSpec(BaseModel):
    name: str
    type: str = "string"
    parser: str | None = None
    source: str | None = None  # path or glob under /work, e.g. /work/scan.xml


class CommandSpec(BaseModel):
    id: str
    name: str
    schema_version: int = 1
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    image: str | None = None
    args: list[ArgSpec] = Field(default_factory=list)
    command: str
    outputs: list[OutputSpec] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def check_id(cls, value: str) -> str:
        if not ID_PATTERN.match(value):
            raise ValueError("id uses lowercase letters, digits and dashes")
        return value

    @model_validator(mode="after")
    def check_template(self) -> CommandSpec:
        names = [a.name for a in self.args]
        if len(names) != len(set(names)):
            raise ValueError("argument names must be unique")
        undefined = sorted(set(PLACEHOLDER.findall(self.command)) - set(names))
        if undefined:
            raise ValueError(f"template uses undefined arguments: {', '.join(undefined)}")
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
