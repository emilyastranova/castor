"""Library content a plugin ships (a pack, docs/PLUGINS.md): commands, tools, parsers, playbooks,
themes and variable kinds in `library/<kind>/*.yaml`. They join the library with
`origin = "plugin:<id>"`, read-only like built-ins (duplicate one to change it), are replaced by the
plugin's updates and leave with it. Nothing a person made is ever overwritten: an id the library
already has from someone else stops the install."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import yaml
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m

log = logging.getLogger(__name__)

# In the order they're added: kinds before the commands that use them, tools and parsers before
# commands, commands before playbooks.
KINDS = ("variable_kinds", "tools", "parsers", "commands", "playbooks", "themes")
LABELS = {"variable_kinds": "variable kind", "tools": "tool", "parsers": "parser", "commands": "command",
          "playbooks": "playbook", "themes": "theme"}
MODELS: dict[str, Any] = {"variable_kinds": m.VariableKind, "tools": m.ToolDefinition, "parsers": m.ParserDefinition,
                          "commands": m.CommandDefinition, "playbooks": m.PlaybookDefinition, "themes": m.ThemeDefinition}
MAX_ITEMS = 200


@dataclass
class Item:
    kind: str
    id: str
    name: str
    spec: Any  # the kind's pydantic spec
    text: str  # the YAML it was read from
    path: str


def origin_of(plugin_id: str) -> str:
    return f"plugin:{plugin_id}"


def plugin_of(origin: str | None) -> str | None:
    return origin[7:] if origin and origin.startswith("plugin:") else None


def read_only(origin: str | None) -> bool:
    """Built-ins and what plugins ship can't be changed in place (duplicate to customise)."""
    return origin != "user"


def why_read_only(origin: str, what: str) -> str:
    plugin = plugin_of(origin)
    if plugin:
        return f"This {what} comes with the plugin {plugin}; duplicate it to change it"
    return f"Built-in {what}s can't be changed; duplicate it to change it"


def cant_delete(origin: str, what: str) -> str:
    plugin = plugin_of(origin)
    if plugin:
        return f"This {what} comes with the plugin {plugin}; it goes when the plugin is removed"
    return f"Built-in {what}s cannot be deleted"


def _parse(kind: str, text: str) -> Any:
    if kind == "commands":
        from .command_spec import parse_spec

        return parse_spec(text)
    if kind == "tools":
        from .tool_spec import parse_tool_spec

        return parse_tool_spec(text)
    if kind == "parsers":
        from .parser_spec import parse_parser_spec

        return parse_parser_spec(text)
    if kind == "playbooks":
        from .playbook_spec import parse_playbook_spec

        return parse_playbook_spec(text)
    if kind == "themes":
        from .theme_spec import parse_theme_spec

        return parse_theme_spec(text)
    from .variables import KindSpec

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"not valid YAML: {exc}") from exc
    try:
        return KindSpec.model_validate(data)
    except Exception as exc:
        raise ValueError(str(exc)) from exc


def read(files: dict[str, bytes], declared: list[str]) -> list[Item]:
    """The pack in a release's files. Every file under library/ must be YAML in a kind the
    manifest's `contents` declares, every declared kind needs a file, and each must parse (its
    commands may use its own parsers)."""
    from .parser_library import overlay

    found: list[tuple[str, str, str]] = []  # kind, path, text
    for path in sorted(files):
        if not path.startswith("library/"):
            continue
        parts = path.split("/")
        if len(parts) != 3 or parts[1] not in KINDS or not parts[2].endswith((".yaml", ".yml")):
            raise ValueError(f"{path}: library files are library/<{'|'.join(KINDS)}>/<name>.yaml")
        if parts[1] not in declared:
            raise ValueError(f"{path}: the manifest's contents don't list {parts[1]}")
        found.append((parts[1], path, files[path].decode("utf-8", "replace")))
    if len(found) > MAX_ITEMS:
        raise ValueError(f"contents: more than {MAX_ITEMS} library items")
    for kind in declared:
        if not any(k == kind for k, _, _ in found):
            raise ValueError(f"contents: {kind} is listed but there's nothing in library/{kind}/")
    items: list[Item] = []
    found.sort(key=lambda f: (KINDS.index(f[0]), f[1]))

    def parse(kind: str, path: str, text: str) -> None:
        try:
            spec = _parse(kind, text)
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
        items.append(Item(kind, spec.id, spec.name, spec, text, path))

    for kind, path, text in found:
        if kind in ("variable_kinds", "tools", "parsers"):
            parse(kind, path, text)
    with overlay([i.spec for i in items if i.kind == "parsers"]):
        for kind, path, text in found:
            if kind not in ("variable_kinds", "tools", "parsers"):
                parse(kind, path, text)
    for kind in KINDS:
        ids = [i.id for i in items if i.kind == kind]
        if len(ids) != len(set(ids)):
            raise ValueError(f"library/{kind}: two have the same id")
    return sorted(items, key=lambda i: (KINDS.index(i.kind), i.id))


async def conflicts(db: AsyncSession, plugin_id: str, items: list[Item]) -> list[str]:
    """Items whose id the library already has from someone else: installing would take it over."""
    out = []
    mine = origin_of(plugin_id)
    for item in items:
        row = await db.get(MODELS[item.kind], item.id)
        if item.kind == "parsers" and row is None:
            from . import parsers

            if item.id in parsers.PARSERS:
                out.append(f"library/parsers/{item.id}: Castor has a built-in parser with this name")
            continue
        if row is not None and row.origin != mine:
            whose = plugin_of(row.origin)
            owner = f"the plugin {whose}" if whose else "Castor (built-in)" if row.origin == "builtin" else "someone here"
            out.append(f"library/{item.kind}/{item.id}: the library already has one, from {owner}")
    return out


async def warnings(db: AsyncSession, items: list[Item]) -> list[str]:
    """What the pack names that neither it nor the library has (it still installs, like a built-in)."""
    from . import parsers

    tools = {i.id for i in items if i.kind == "tools"} | set((await db.execute(select(m.ToolDefinition.id))).scalars())
    parser_ids = {i.id for i in items if i.kind == "parsers"} | {p.name for p in parsers.all_parsers()}
    commands = {i.id for i in items if i.kind == "commands"} | set((await db.execute(select(m.CommandDefinition.id))).scalars())
    out = []
    for item in items:
        if item.kind == "commands":
            if item.spec.tool and item.spec.tool not in tools:
                out.append(f"command {item.id}: no tool {item.spec.tool}")
            for o in item.spec.outputs:
                if o.parser and o.parser not in parser_ids:
                    out.append(f"command {item.id}: no parser {o.parser}")
        if item.kind == "playbooks":
            for step in item.spec.steps:
                ref = step.command_ref
                if step.kind == "command" and ref and ref not in commands:
                    out.append(f"playbook {item.id}: step {step.id} runs {ref}, which isn't in the library")
    return out


def summary(items: list[Item]) -> list[dict[str, str]]:
    return [{"kind": i.kind, "id": i.id, "name": i.name} for i in items]


async def apply(db: AsyncSession, plugin_id: str, items: list[Item]) -> None:
    """Add or update the plugin's items and retire the ones its new release no longer has.
    The caller checked `conflicts` and commits."""
    from . import command_library, parser_library, playbook_library, theme_library, tool_library
    from .variables import drop_removed_fields

    origin = origin_of(plugin_id)
    for item in items:
        model = MODELS[item.kind]
        row = await db.get(model, item.id)
        new = row is None
        if new:
            row = model(id=item.id, origin=origin)
            db.add(row)
        if item.kind == "variable_kinds":
            row.name, row.description, row.spec = item.spec.name, item.spec.description, item.spec.model_dump(mode="json")
            await drop_removed_fields(db, item.spec)
            continue
        definition_from = {"tools": tool_library.definition_from_spec, "parsers": parser_library.definition_from_spec,
                           "commands": command_library.definition_from_spec,
                           "playbooks": playbook_library.definition_from_spec,
                           "themes": theme_library.definition_from_spec}[item.kind]
        definition_from(row, item.spec, item.text)
        if new:
            row.version = 1  # counts changes from here on
        if item.kind == "parsers":
            parser_library.register(item.spec, origin)
    await db.flush()
    await retire(db, plugin_id, keep={(i.kind, i.id) for i in items})


async def retire(db: AsyncSession, plugin_id: str, keep: set[tuple[str, str]] | None = None) -> list[str]:
    """Remove what the plugin shipped (all of it, or what isn't in `keep`), as Castor retires a
    built-in whose file is gone: cards keep their command lines; a tool, parser or variable kind
    something else still uses stays, as an ordinary library item. Returns what stayed."""
    from . import parser_library, theme_library

    origin = origin_of(plugin_id)
    keep = keep or set()
    kept: list[str] = []
    for kind in reversed(KINDS):  # playbooks before commands before tools…
        model = MODELS[kind]
        rows = (await db.execute(select(model).where(model.origin == origin))).scalars().all()
        for row in rows:
            if (kind, row.id) in keep:
                continue
            if kind == "commands":
                await db.execute(update(m.Task).where(m.Task.command_ref == row.id).values(command_ref=None))
            elif kind == "themes":
                await theme_library.forget_theme(db, row.id)
            elif kind in ("tools", "parsers"):
                users = [c for c in (await db.execute(select(m.CommandDefinition))).scalars()
                         if c.origin != origin or (("commands", c.id) in keep)]
                used = any((c.spec or {}).get("tool") == row.id for c in users) if kind == "tools" else any(
                    o.get("parser") == row.id for c in users for o in (c.spec or {}).get("outputs", []))
                if used:
                    row.origin = "user"
                    kept.append(f"{LABELS[kind]} {row.id}")
                    if kind == "parsers":
                        from .parser_spec import ParserSpec

                        parser_library.register(ParserSpec.model_validate(row.spec), "user")
                    continue
                if kind == "parsers":
                    parser_library.unregister(row.id)
            elif kind == "variable_kinds":
                count = (await db.execute(select(func.count()).where(m.VariableSet.kind_id == row.id))).scalar()
                if count:
                    row.origin = "user"
                    kept.append(f"{LABELS[kind]} {row.id}")
                    continue
            await db.delete(row)
        await db.flush()
    if kept:
        log.info("plugin %s: kept %s (still in use)", plugin_id, ", ".join(kept))
    return kept


async def items_of(db: AsyncSession, plugin_id: str) -> list[dict[str, str]]:
    """What an installed plugin added to the library."""
    origin = origin_of(plugin_id)
    out = []
    for kind in KINDS:
        model = MODELS[kind]
        for row_id, name in (await db.execute(select(model.id, model.name).where(model.origin == origin).order_by(model.id))).all():
            out.append({"kind": kind, "id": row_id, "name": name})
    return out
