"""What changed between two runs' parsed results (pure; the API gathers the values).

Lists of records are matched by an identity key: a host by its IP, a service by host, port
and protocol, a URL by itself, a finding by template and where it matched. The key comes from
the output's type when Castor knows it, otherwise the first candidate every record has and no
two share. Records with the same key but other fields different are "changed" (a port that
went from open to filtered, a page whose title changed). Lists of plain values are compared
as sets; anything else is equal or changed as a whole.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

TYPE_KEYS: dict[str, tuple[str, ...]] = {
    "list<host>": ("ip",),
    "list<service>": ("host", "port", "proto"),
    "list<smb_host>": ("host", "port"),
    "list<url>": ("url",),
}
CANDIDATE_KEYS: list[tuple[str, ...]] = [
    ("ip",),
    ("host", "port", "proto"),
    ("host", "port", "protocol"),
    ("host", "port"),
    ("url",),
    ("template_id", "matched_at"),
    ("template-id", "matched-at"),
    ("template_id", "host"),
    ("template-id", "host"),
    ("id",),
    ("name", "host"),
    ("name",),
    ("host",),
    ("domain",),
    ("path",),
    ("username",),
    ("user",),
]
LIMIT = 500  # records listed per kind of change; the counts are always whole


def _norm(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _records(items: Iterable[Any]) -> bool:
    return all(isinstance(i, dict) for i in items)


def pick_key(type_: str | None, before: list[Any], after: list[Any]) -> tuple[str, ...] | None:
    """The fields that say which record is which, or None to match whole records."""
    both = [*before, *after]
    if not both or not _records(both):
        return None
    candidates = ([TYPE_KEYS[type_]] if type_ in TYPE_KEYS else []) + CANDIDATE_KEYS
    for key in candidates:
        if not all(all(r.get(k) not in (None, "") for k in key) for r in both):
            continue
        if all(len({_norm([r.get(k) for k in key]) for r in side}) == len(side) for side in (before, after)):
            return key
    return None


def _field_changes(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"field": f, "before": before.get(f), "after": after.get(f)}
        for f in dict.fromkeys([*before, *after])
        if _norm(before.get(f)) != _norm(after.get(f))
    ]


def diff(name: str, type_: str | None, before: Any, after: Any) -> dict[str, Any]:
    """One output compared. `before`/`after` None means the output wasn't there."""
    out: dict[str, Any] = {
        "name": name, "type": type_, "key": None, "status": "same",
        "added": [], "removed": [], "changed": [], "unchanged": 0,
        "counts": {"added": 0, "removed": 0, "changed": 0},
        "before_count": None, "after_count": None, "before": None, "after": None,
    }
    if before is None and after is None:
        return out
    as_list = isinstance(before, list) or isinstance(after, list)
    if as_list and (before is None or isinstance(before, list)) and (after is None or isinstance(after, list)):
        old, new = before or [], after or []
        out["before_count"] = None if before is None else len(old)
        out["after_count"] = None if after is None else len(new)
        key = pick_key(type_, old, new)
        if key is not None:
            out["key"] = list(key)
            ident = lambda r: _norm([r.get(k) for k in key])
            olds = {ident(r): r for r in old}
            news = {ident(r): r for r in new}
            added = [r for k, r in news.items() if k not in olds]
            removed = [r for k, r in olds.items() if k not in news]
            changed = []
            for k, r in news.items():
                if k in olds and (fields := _field_changes(olds[k], r)):
                    changed.append({"item": r, "before": olds[k], "fields": fields})
            unchanged = len(news) - len(added) - len(changed)
        else:
            olds_set = {_norm(v): v for v in old}
            news_set = {_norm(v): v for v in new}
            added = [v for k, v in news_set.items() if k not in olds_set]
            removed = [v for k, v in olds_set.items() if k not in news_set]
            changed = []
            unchanged = len(news_set) - len(added)
        out["counts"] = {"added": len(added), "removed": len(removed), "changed": len(changed)}
        out["added"], out["removed"], out["changed"] = added[:LIMIT], removed[:LIMIT], changed[:LIMIT]
        out["unchanged"] = unchanged
    else:
        out["before"], out["after"] = before, after
        if _norm(before) != _norm(after):
            out["counts"]["changed"] = 1
            if isinstance(before, dict) and isinstance(after, dict):
                out["changed"] = [{"item": after, "before": before, "fields": _field_changes(before, after)}]
    if before is None:
        out["status"] = "new"
    elif after is None:
        out["status"] = "gone"
    elif any(out["counts"].values()):
        out["status"] = "changed"
    return out


def compare(before: dict[str, tuple[str | None, Any]], after: dict[str, tuple[str | None, Any]]) -> list[dict[str, Any]]:
    """Every output by name: {name: (type, value)} for each side, in `after`'s order."""
    names = list(dict.fromkeys([*after, *before]))
    result = []
    for name in names:
        type_ = (after.get(name) or before.get(name) or (None, None))[0]
        result.append(diff(name, type_, before[name][1] if name in before else None, after[name][1] if name in after else None))
    return result


def totals(outputs: Iterable[dict[str, Any]]) -> dict[str, int]:
    t = {"added": 0, "removed": 0, "changed": 0}
    for o in outputs:
        for k in t:
            t[k] += o["counts"][k]
    return t
