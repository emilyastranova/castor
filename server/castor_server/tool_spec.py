"""Tools: what a command needs installed, and how an agent gets it (docs/PHASE3.md, 3a).

A tool provides itself in one or more ways:

    image: docker.io/instrumentisto/nmap          # a published image, used as-is
    build:                                         # a recipe, built once per agent
      base: docker.io/library/python:3.12-slim
      install: pip install --no-cache-dir netexec
      check: nxc --version
    host:                                          # on the agent's own host (opt-in)
      check: command -v nxc
      install: pipx install netexec

For a given agent the server picks the first way it can use: image, then build (both need
a container runtime), then host (needs host jobs; installing needs --allow-host-install).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .command_spec import ID_PATTERN

# Bumped when the Dockerfile Castor renders changes shape, so old builds aren't reused.
RECIPE_FORMAT = 1
TAG_REPO = "castor-tools"


class BuildRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base: str = Field(min_length=1, max_length=300)
    install: str = Field(min_length=1)
    # Run as the build's last step, so a broken install fails the build, not the job.
    check: str = ""

    @field_validator("base")
    @classmethod
    def no_spaces(cls, value: str) -> str:
        if any(c.isspace() for c in value.strip()):
            raise ValueError("base is one image reference, e.g. docker.io/library/python:3.12-slim")
        return value.strip()


class HostInstall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: str = Field(min_length=1)
    install: str = ""


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    homepage: str = ""
    image: str | None = None
    build: BuildRecipe | None = None
    host: HostInstall | None = None

    @field_validator("id")
    @classmethod
    def check_id(cls, value: str) -> str:
        if not ID_PATTERN.match(value):
            raise ValueError("id uses lowercase letters, digits and dashes")
        return value

    @field_validator("image")
    @classmethod
    def check_image(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or any(c.isspace() for c in value.strip())):
            raise ValueError("image is one image reference")
        return value.strip() if value else value

    @model_validator(mode="after")
    def some_way(self) -> ToolSpec:
        if not (self.image or self.build or self.host):
            raise ValueError("a tool needs at least one of image, build or host")
        return self

    # ---------- recipes ----------

    def build_files(self) -> dict[str, str]:
        """The build context for the recipe: Dockerfile plus the scripts it runs. The
        scripts are files (not RUN lines) so any shell text works without escaping."""
        assert self.build is not None
        lines = [
            f"FROM {self.build.base}",
            f'LABEL castor.tool="{self.id}"',
            "COPY castor-install.sh /tmp/castor-install.sh",
            "RUN sh -e /tmp/castor-install.sh && rm -f /tmp/castor-install.sh",
        ]
        files = {"castor-install.sh": self.build.install.rstrip() + "\n"}
        if self.build.check.strip():
            lines += [
                "COPY castor-check.sh /tmp/castor-check.sh",
                "RUN sh -e /tmp/castor-check.sh && rm -f /tmp/castor-check.sh",
            ]
            files["castor-check.sh"] = self.build.check.rstrip() + "\n"
        files["Dockerfile"] = "\n".join(lines) + "\n"
        return files

    def build_tag(self) -> str:
        """castor-tools/<id>:<hash of the recipe>: changes whenever the recipe does."""
        assert self.build is not None
        material = json.dumps(
            {"format": RECIPE_FORMAT, "id": self.id, **self.build.model_dump()}, sort_keys=True
        )
        return f"{TAG_REPO}/{self.id}:{hashlib.sha256(material.encode()).hexdigest()[:12]}"

    # ---------- choosing a way for an agent ----------

    def way_for(self, runtimes: set[str], host_install: bool = False) -> dict[str, Any] | None:
        """How an agent with these runtimes runs this tool, or None if it can't."""
        container = bool(runtimes & {"docker", "podman"})
        if container and self.image:
            return {"id": self.id, "name": self.name, "kind": "image", "ref": self.image}
        if container and self.build:
            return {"id": self.id, "name": self.name, "kind": "build", "ref": self.build_tag(), "files": self.build_files()}
        if "host" in runtimes and self.host:
            return {
                "id": self.id,
                "name": self.name,
                "kind": "host",
                "check": self.host.check,
                "install": self.host.install if host_install else "",
                "install_hint": self.host.install,
            }
        return None

    def needs(self) -> list[Literal["container", "host"]]:
        out: list[Literal["container", "host"]] = []
        if self.image or self.build:
            out.append("container")
        if self.host:
            out.append("host")
        return out


def parse_tool_spec(source: str | dict[str, Any]) -> ToolSpec:
    if isinstance(source, str):
        try:
            raw = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("a tool must be a YAML mapping")  # noqa: TRY004 - callers handle ValueError
    else:
        raw = source
    try:
        return ToolSpec.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(
            "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: "
                + ("unknown key" if e["type"] == "extra_forbidden" else e["msg"].removeprefix("Value error, "))
                for e in exc.errors()
            )
        ) from exc
