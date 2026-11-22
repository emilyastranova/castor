"""Ansible inventories as SSH hosts (Agents → Add from an Ansible inventory): the hosts of an
inventory, with how to reach each (ansible_host, ansible_port, ansible_user) and its groups, for
api/ssh_hosts.py to add as Castor SSH hosts. Pure: no files, no network, nothing run.

Reads the three shapes inventories come in: INI (`[web]`, `[web:vars]`, `[web:children]`, host
ranges such as `web[01:03].lab`), YAML (`all: {hosts, vars, children}`) and the JSON that
`ansible-inventory --list` and AWX's inventory script print (`_meta.hostvars` and groups with
`hosts`, `vars`, `children`). Also takes rows already read (a job's output: {name, host, port,
user, groups}). Variables are only read for what connecting needs; templates (`{{ … }}`) are left
as they are and such a value is skipped, never evaluated. Dynamic inventory plugins run where
Ansible runs: the Ansible plugin's inventory command lists them on an agent first."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

MAX_HOSTS = 2000
MAX_TEXT = 4 * 1024 * 1024
SKIP_GROUPS = {"all", "ungrouped"}
# Older names first, so the current ones win when both are set.
HOST_VARS = ("ansible_ssh_host", "ansible_host")
PORT_VARS = ("ansible_ssh_port", "ansible_port")
USER_VARS = ("ansible_ssh_user", "ansible_user")
RANGE = re.compile(r"\[([0-9]+|[a-z]):([0-9]+|[a-z])(?::([0-9]+))?\]")


class InventoryError(ValueError):
    pass


@dataclass
class InvHost:
    name: str
    host: str
    port: int | None = None
    user: str = ""
    groups: list[str] = field(default_factory=list)
    # Why it can't be an SSH host (a local or WinRM connection), if it can't.
    skip: str | None = None

    def out(self) -> dict[str, Any]:
        return {"name": self.name, "host": self.host, "port": self.port, "user": self.user, "groups": self.groups, "skip": self.skip}


@dataclass
class _Inv:
    hosts: dict[str, dict[str, Any]] = field(default_factory=dict)  # name: its own vars
    groups: dict[str, dict[str, Any]] = field(default_factory=dict)  # name: {vars, children, hosts}

    def group(self, name: str) -> dict[str, Any]:
        return self.groups.setdefault(name, {"vars": {}, "children": [], "hosts": []})

    def add_host(self, group: str | None, name: str, values: dict[str, Any]) -> None:
        if name not in self.hosts and len(self.hosts) >= MAX_HOSTS:
            raise InventoryError(f"more than {MAX_HOSTS} hosts")
        self.hosts.setdefault(name, {}).update(values)
        if group:
            hosts = self.group(group)["hosts"]
            if name not in hosts:
                hosts.append(name)


def expand(pattern: str) -> list[str]:
    """A host pattern with ranges: web[01:03] → web01, web02, web03; db-[a:c] → db-a, db-b, db-c."""
    match = RANGE.search(pattern)
    if match is None:
        return [pattern]
    start, end, step = match.group(1), match.group(2), int(match.group(3) or 1)
    if start.isdigit() != end.isdigit() or step < 1:
        raise InventoryError(f"{pattern}: a range is [01:10] or [a:f]")
    if start.isdigit():
        width = len(start) if start.startswith("0") else 0
        values = [str(i).zfill(width) for i in range(int(start), int(end) + 1, step)]
    else:
        values = [chr(c) for c in range(ord(start), ord(end) + 1, step)]
    if len(values) > MAX_HOSTS:
        raise InventoryError(f"{pattern}: more than {MAX_HOSTS} hosts")
    head, tail = pattern[: match.start()], pattern[match.end():]
    out: list[str] = []
    for v in values:
        out.extend(expand(head + v + tail))
        if len(out) > MAX_HOSTS:
            raise InventoryError(f"{pattern}: more than {MAX_HOSTS} hosts")
    return out


def _ini_value(text: str) -> Any:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def _ini_pairs(words: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for word in words:
        key, sep, value = word.partition("=")
        if sep and key:
            out[key] = _ini_value(value)
    return out


def _parse_ini(text: str) -> _Inv:
    import shlex

    inv = _Inv()
    section, kind = "ungrouped", "hosts"
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            name, _, kind = line[1:-1].partition(":")
            section, kind = name.strip(), kind.strip() or "hosts"
            if kind not in ("hosts", "vars", "children"):
                raise InventoryError(f"line {number}: [{name}:{kind}] isn't a section Ansible knows")
            inv.group(section)
            continue
        try:
            words = shlex.split(line, comments=True)
        except ValueError as exc:
            raise InventoryError(f"line {number}: {exc}") from exc
        if not words:
            continue
        if kind == "vars":
            key, sep, value = line.partition("=")
            if not sep:
                raise InventoryError(f"line {number}: [{section}:vars] lines are name=value")
            inv.group(section)["vars"][key.strip()] = _ini_value(value)
        elif kind == "children":
            children = inv.group(section)["children"]
            if words[0] not in children:
                children.append(words[0])
            inv.group(words[0])
        else:
            for name in expand(words[0]):
                inv.add_host(section, name, _ini_pairs(words[1:]))
    return inv


def _yaml_group(inv: _Inv, name: str, body: Any, depth: int = 0) -> None:
    if depth > 20:
        raise InventoryError("groups nested more than 20 deep")
    group = inv.group(name)
    if body is None:
        return
    if not isinstance(body, dict):
        raise InventoryError(f"{name}: a group is a mapping of hosts, vars and children")
    for pattern, values in (body.get("hosts") or {}).items():
        for host in expand(str(pattern)):
            inv.add_host(name, host, dict(values or {}) if isinstance(values, dict) else {})
    if isinstance(body.get("vars"), dict):
        group["vars"].update(body["vars"])
    for child, child_body in (body.get("children") or {}).items():
        if str(child) not in group["children"]:
            group["children"].append(str(child))
        _yaml_group(inv, str(child), child_body, depth + 1)


def _parse_list_json(data: dict[str, Any]) -> _Inv:
    """`ansible-inventory --list` / AWX's inventory script: groups at the top, hostvars in _meta."""
    inv = _Inv()
    hostvars = (data.get("_meta") or {}).get("hostvars") or {}
    for name, body in data.items():
        if name == "_meta":
            continue
        group = inv.group(name)
        if isinstance(body, list):  # the old short form: a list of hosts
            body = {"hosts": body}
        if not isinstance(body, dict):
            continue
        for host in body.get("hosts") or []:
            inv.add_host(name, str(host), {})
        if isinstance(body.get("vars"), dict):
            group["vars"].update(body["vars"])
        for child in body.get("children") or []:
            if str(child) not in group["children"]:
                group["children"].append(str(child))
            inv.group(str(child))
    for name, values in hostvars.items():
        inv.add_host(None, str(name), dict(values or {}) if isinstance(values, dict) else {})
    return inv


def _parse(text: str) -> _Inv:
    if len(text.encode()) > MAX_TEXT:
        raise InventoryError(f"the inventory is larger than {MAX_TEXT // (1024 * 1024)} MB")
    stripped = text.strip()
    if not stripped:
        raise InventoryError("the inventory is empty")
    data: Any = None
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise InventoryError(f"JSON: {exc}") from exc
        all_group = data.get("all") if isinstance(data, dict) else None
        if isinstance(data, dict) and ("_meta" in data or (isinstance(all_group, dict) and isinstance(all_group.get("children"), list))):
            return _parse_list_json(data)
    elif re.search(r"^\s*\[[^\]]+\]\s*$", text, re.MULTILINE) or not re.search(r"^\s*[\w.-]+\s*:", text, re.MULTILINE):
        return _parse_ini(text)
    else:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise InventoryError(f"YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise InventoryError("an inventory is INI, YAML (all: hosts, children) or ansible-inventory --list JSON")
    inv = _Inv()
    for name, body in data.items():
        _yaml_group(inv, str(name), body)
    return inv


def _first(values: dict[str, Any], names: tuple[str, ...]) -> Any:
    found = None
    for n in names:
        if values.get(n) not in (None, ""):
            found = values[n]
    return found


def _resolve(inv: _Inv) -> list[InvHost]:
    parents: dict[str, list[str]] = {}
    for name, group in inv.groups.items():
        for child in group["children"]:
            parents.setdefault(child, []).append(name)

    def lineage(group: str, seen: frozenset[str] = frozenset()) -> list[str]:
        """The group and its ancestors, farthest first (so nearer groups' vars win)."""
        out: list[str] = []
        for parent in parents.get(group, []):
            if parent not in seen:
                out.extend(g for g in lineage(parent, seen | {group}) if g not in out)
        return [*out, group] if group not in out else out

    member: dict[str, list[str]] = {}
    for name, group in inv.groups.items():
        for host in group["hosts"]:
            member.setdefault(host, []).append(name)
    out: list[InvHost] = []
    for name, own in inv.hosts.items():
        groups: list[str] = []
        for g in member.get(name, []):
            for x in lineage(g):
                if x not in groups:
                    groups.append(x)
        values: dict[str, Any] = dict((inv.groups.get("all") or {}).get("vars") or {})
        for g in groups:
            values.update(inv.groups[g]["vars"])
        values.update(own)
        host = str(_first(values, HOST_VARS) or name)
        port = _first(values, PORT_VARS)
        user = _first(values, USER_VARS)
        item = InvHost(name=name, host=host, groups=sorted(g for g in groups if g not in SKIP_GROUPS))
        connection = str(values.get("ansible_connection") or "ssh")
        if connection not in ("ssh", "smart", "paramiko", "ansible.builtin.ssh", "ansible.builtin.paramiko_ssh"):
            item.skip = f"it connects with {connection}, not SSH"
        if "{{" in host or (isinstance(user, str) and "{{" in user) or (isinstance(port, str) and "{{" in port):
            item.skip = "how to reach it is a template (Ansible works it out when it runs)"
        else:
            try:
                item.port = int(port) if port not in (None, "") else None
                if item.port is not None and not 1 <= item.port <= 65535:
                    raise ValueError
            except (TypeError, ValueError):
                item.skip = f"its port {port!r} isn't a port"
            item.user = str(user or "")
        out.append(item)
    return out


def read(text: str) -> list[InvHost]:
    """The inventory's hosts, in the order they first appear. Raises InventoryError."""
    return _resolve(_parse(text))


def from_rows(rows: Any) -> list[InvHost]:
    """Rows a job read ({name, host, port, user, groups}: the Ansible plugin's inventory parser, AWX's)."""
    if not isinstance(rows, list):
        raise InventoryError("that output isn't a list of hosts")
    out: list[InvHost] = []
    for row in rows[:MAX_HOSTS]:
        if not isinstance(row, dict) or not (row.get("name") or row.get("host")):
            continue
        name = str(row.get("name") or row.get("host"))
        port = row.get("port")
        item = InvHost(name=name, host=str(row.get("host") or name), user=str(row.get("user") or ""),
                       groups=sorted({str(g) for g in row.get("groups") or [] if str(g) not in SKIP_GROUPS}))
        try:
            item.port = int(port) if port not in (None, "") else None
        except (TypeError, ValueError):
            item.skip = f"its port {port!r} isn't a port"
        if row.get("skip"):
            item.skip = str(row["skip"])
        out.append(item)
    return out
