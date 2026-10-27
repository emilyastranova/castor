"""Playbook conditions: `when:` (should a step run?) and `expect:` (does a finished step's
result look right?). Both are evaluated on the server and explain themselves, so the UI can
show why a branch was or wasn't taken.

    when: { value: "{{ steps.unsigned.outputs.hosts }}", count: { gt: 0 } }
    when: { step: smb_audit, status: failed }
    when: { any: [ <condition>, ... ] }      # also all:, not:, or a plain list (= all)

    expect:
      outputs: { hosts: { empty: false } }
      log: { not_matches: "STATUS_LOGON_FAILURE" }
"""

from __future__ import annotations

import re
from typing import Any, Literal

import regex
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    ValidationError,
    field_validator,
    model_validator,
)

from .playbook_transforms import REGEX_TIMEOUT_S, FilterOp, TransformError, as_items

# Statuses a status test can wait for: every way a step can end.
FINAL_STATUSES = ("succeeded", "failed", "cancelled", "skipped", "not_taken")
REF_RE = re.compile(
    r"\{\{\s*(inputs\.[A-Za-z0-9_]+|steps\.[A-Za-z0-9_-]+\.outputs\.[A-Za-z0-9_]+)\s*\}\}"
)
EXACT_REF_RE = re.compile(r"^" + REF_RE.pattern + r"$")
MAX_LOG_BYTES = 1024 * 1024  # expect.log looks at the last MiB of output

COUNT_OPS = {"eq": "=", "ne": "≠", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤"}


class CountTest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eq: int | None = None
    ne: int | None = None
    gt: int | None = None
    gte: int | None = None
    lt: int | None = None
    lte: int | None = None

    @model_validator(mode="before")
    @classmethod
    def short_form(cls, data: Any) -> Any:
        return {"eq": data} if isinstance(data, int) and not isinstance(data, bool) else data

    @model_validator(mode="after")
    def at_least_one(self) -> CountTest:
        if not self.model_fields_set:
            raise ValueError("count needs eq, ne, gt, gte, lt or lte")
        return self

    def check(self, n: int) -> bool:
        ops = {"eq": n.__eq__, "ne": n.__ne__, "gt": n.__gt__, "gte": n.__ge__, "lt": n.__lt__, "lte": n.__le__}
        return all(ops[k](getattr(self, k)) for k in self.model_fields_set)

    def describe(self) -> str:
        return " and ".join(f"{COUNT_OPS[k]} {getattr(self, k)}" for k in COUNT_OPS if k in self.model_fields_set)


def _item_filter(value: Any) -> FilterOp:
    try:
        return FilterOp.model_validate(value)
    except ValidationError as exc:
        raise ValueError("; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())) from exc


class Checks(BaseModel):
    """Tests on one value. Several in one mapping must all pass."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    empty: bool | None = None
    count: CountTest | None = None
    equals: Any = None
    not_equals: Any = None
    in_: list[Any] | None = Field(default=None, alias="in")
    not_in: list[Any] | None = None
    contains: Any = None
    not_contains: Any = None
    matches: str | None = None
    not_matches: str | None = None
    # Per-item tests for lists, written like a transform filter: { field: port, equals: 445 }
    some: FilterOp | None = None
    every: FilterOp | None = None
    none: FilterOp | None = None
    ignore_case: bool = False

    @field_validator("matches", "not_matches")
    @classmethod
    def check_regex(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                regex.compile(value)
            except regex.error as exc:
                raise ValueError(f"not a valid regular expression: {exc}") from exc
        return value

    @field_validator("some", "every", "none", mode="before")
    @classmethod
    def check_item_filter(cls, value: Any) -> Any:
        return value if value is None or isinstance(value, FilterOp) else _item_filter(value)

    def tests(self) -> list[str]:
        return [
            name
            for name in ("empty", "count", "equals", "not_equals", "in_", "not_in", "contains",
                         "not_contains", "matches", "not_matches", "some", "every", "none")
            if name in self.model_fields_set
        ]

    @model_validator(mode="after")
    def at_least_one(self) -> Checks:
        if not self.tests():
            raise ValueError(
                "give at least one test: empty, count, equals, not_equals, in, not_in, contains, "
                "not_contains, matches, not_matches, some, every or none"
            )
        return self

    # ---------- evaluation ----------

    def _norm(self, value: Any) -> Any:
        return value.lower() if self.ignore_case and isinstance(value, str) else value

    def _search(self, pattern: str, value: Any) -> bool:
        texts = [str(v) for v in value] if isinstance(value, list) else [] if value is None else [str(value)]
        compiled = regex.compile(pattern, regex.IGNORECASE if self.ignore_case else 0)
        try:
            return any(compiled.search(t, timeout=REGEX_TIMEOUT_S) for t in texts)
        except TimeoutError as exc:
            raise TransformError(f"the pattern took longer than {REGEX_TIMEOUT_S:g}s; simplify it") from exc

    def _contains(self, value: Any, needle: Any) -> bool:
        if isinstance(value, list):
            return self._norm(needle) in [self._norm(v) for v in value]
        if value is None:
            return False
        return str(self._norm(needle)) in str(self._norm(value))

    def run(self, value: Any) -> list[tuple[str, bool]]:
        """(description, passed) for each test, in order."""
        out: list[tuple[str, bool]] = []
        n = count_of(value)
        for name in self.tests():
            if name == "empty":
                out.append(("is empty" if self.empty else "is not empty", is_empty(value) == self.empty))
            elif name == "count":
                assert self.count is not None
                out.append((f"count {self.count.describe()}", self.count.check(n)))
            elif name == "equals":
                out.append((f"equals {show(self.equals)}", self._norm(value) == self._norm(self.equals)))
            elif name == "not_equals":
                out.append((f"is not {show(self.not_equals)}", self._norm(value) != self._norm(self.not_equals)))
            elif name == "in_":
                out.append((f"is one of {show(self.in_)}", self._norm(value) in [self._norm(v) for v in self.in_ or []]))
            elif name == "not_in":
                out.append((f"is none of {show(self.not_in)}", self._norm(value) not in [self._norm(v) for v in self.not_in or []]))
            elif name == "contains":
                out.append((f"contains {show(self.contains)}", self._contains(value, self.contains)))
            elif name == "not_contains":
                out.append((f"doesn't contain {show(self.not_contains)}", not self._contains(value, self.not_contains)))
            elif name == "matches":
                out.append((f"matches /{self.matches}/", self._search(self.matches or "", value)))
            elif name == "not_matches":
                out.append((f"doesn't match /{self.not_matches}/", not self._search(self.not_matches or "", value)))
            else:
                op: FilterOp = getattr(self, name)
                items = as_items(value)
                kept = sum(1 for i in items if op.keep(i))
                passed = {"some": kept > 0, "every": kept == len(items), "none": kept == 0}[name]
                label = {"some": "some item", "every": "every item", "none": "no item"}[name]
                out.append((f"{label}: {op.describe()} ({kept} of {len(items)})", passed))
        return out


def is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def count_of(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    return 0 if is_empty(value) else 1


def show(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(str(v) for v in value) + "]"
    return repr(value) if isinstance(value, str) else str(value).lower() if isinstance(value, bool) else str(value)


def short_ref(ref: str) -> str:
    """`steps.unsigned.outputs.hosts` → `unsigned.hosts`; inputs stay as they are."""
    parts = ref.split(".")
    return f"{parts[1]}.{parts[3]}" if parts[0] == "steps" else ref


def describe_value(value: Any) -> str:
    if isinstance(value, list):
        return f"{len(value)} item{'' if len(value) == 1 else 's'}"
    if is_empty(value):
        return "empty"
    text = str(value)
    return repr(text if len(text) <= 60 else text[:57] + "…")


# ---------- the condition tree ----------


class StatusTest(BaseModel):
    """Passes when another step ended in one of these statuses. The step waits for it to
    finish whatever happens, so a failure there doesn't block this step."""

    model_config = ConfigDict(extra="forbid")

    step: str
    status: list[Literal["succeeded", "failed", "cancelled", "skipped", "not_taken"]]

    @field_validator("status", mode="before")
    @classmethod
    def one_or_many(cls, value: Any) -> Any:
        return [value] if isinstance(value, str) else value

    def describe(self) -> str:
        return f"{self.step} {' or '.join(self.status)}"


class ValueTest(Checks):
    """Tests on a value: an input, an upstream output, or text mixing them."""

    value: Any

    def refs(self) -> list[str]:
        return REF_RE.findall(self.value) if isinstance(self.value, str) else []

    def subject(self) -> str:
        if isinstance(self.value, str):
            exact = EXACT_REF_RE.match(self.value.strip())
            if exact:
                return short_ref(exact.group(1))
        return show(self.value)


class AllOf(BaseModel):
    model_config = ConfigDict(extra="forbid")
    all: list[Condition]


class AnyOf(BaseModel):
    model_config = ConfigDict(extra="forbid")
    any: list[Condition]


class Not(BaseModel):
    model_config = ConfigDict(extra="forbid")
    not_: Condition = Field(alias="not")


class Condition(RootModel[AllOf | AnyOf | Not | StatusTest | ValueTest]):
    @model_validator(mode="before")
    @classmethod
    def pick(cls, data: Any) -> Any:
        # A plain list means "all of these". Choose the branch by key so errors are about
        # the condition that was meant, not every alternative.
        if isinstance(data, list):
            return AllOf(all=[Condition.model_validate(c) for c in data])
        if not isinstance(data, dict):
            raise ValueError("a condition is a mapping (value:, step:, all:, any: or not:) or a list")  # noqa: TRY004 - pydantic needs ValueError
        for key, model in (("all", AllOf), ("any", AnyOf), ("not", Not)):
            if key in data:
                return model.model_validate(data)
        if "step" in data:
            return StatusTest.model_validate(data)
        if "value" in data:
            return ValueTest.model_validate(data)
        raise ValueError("a condition needs value:, step:, all:, any: or not:")

    def walk(self):
        node = self.root
        if isinstance(node, AllOf | AnyOf):
            for child in node.all if isinstance(node, AllOf) else node.any:
                yield from child.walk()
        elif isinstance(node, Not):
            yield from node.not_.walk()
        else:
            yield node

    def refs(self) -> list[str]:
        return [r for leaf in self.walk() if isinstance(leaf, ValueTest) for r in leaf.refs()]

    def watched_steps(self) -> list[str]:
        """Steps named in status tests: waited for until they end, whatever the outcome."""
        return [*dict.fromkeys(leaf.step for leaf in self.walk() if isinstance(leaf, StatusTest))]

    def evaluate(self, resolve: Any, statuses: dict[str, str]) -> tuple[bool, list[dict[str, Any]]]:
        """(met, checks): checks lists each leaf test with what was found."""
        checks: list[dict[str, Any]] = []
        return _eval(self, resolve, statuses, checks), checks


def _eval(cond: Condition, resolve: Any, statuses: dict[str, str], checks: list[dict[str, Any]]) -> bool:
    node = cond.root
    # Lists, not generators: every test runs, so the explanation is complete.
    if isinstance(node, AllOf):
        return all([_eval(c, resolve, statuses, checks) for c in node.all])  # noqa: C419
    if isinstance(node, AnyOf):
        return any([_eval(c, resolve, statuses, checks) for c in node.any])  # noqa: C419
    if isinstance(node, Not):
        inner: list[dict[str, Any]] = []
        met = not _eval(node.not_, resolve, statuses, inner)
        checks += [{**c, "text": f"not ({c['text']})", "met": not c["met"]} for c in inner]
        return met
    if isinstance(node, StatusTest):
        actual = statuses.get(node.step, "pending")
        met = actual in node.status
        checks.append({"text": f"{node.step} is {' or '.join(node.status)}", "actual": actual, "met": met})
        return met
    value = resolve(node.value)
    results = node.run(value)
    met = all(ok for _, ok in results)
    for text, ok in results:
        checks.append({"text": f"{node.subject()} {text}", "actual": describe_value(value), "met": ok})
    return met


for _model in (AllOf, AnyOf, Not):
    _model.model_rebuild()


# ---------- expect ----------


class LogChecks(Checks):
    """Only the text tests make sense on a log."""

    @model_validator(mode="after")
    def text_only(self) -> LogChecks:
        bad = [t for t in self.tests() if t not in ("contains", "not_contains", "matches", "not_matches", "empty")]
        if bad:
            raise ValueError(f"log takes contains, not_contains, matches, not_matches or empty (not {', '.join(bad)})")
        return self


class Expect(BaseModel):
    """What a command step's successful result should look like. A step that finishes but
    fails these is treated as failed ("unexpected output"), and its `on_failure` applies."""

    model_config = ConfigDict(extra="forbid")

    outputs: dict[str, Checks] = Field(default_factory=dict)
    log: LogChecks | None = None

    @model_validator(mode="after")
    def something(self) -> Expect:
        if not self.outputs and self.log is None:
            raise ValueError("expect needs outputs: or log:")
        return self

    def evaluate(self, outputs: dict[str, Any], log_text: str | None) -> tuple[bool, list[dict[str, Any]]]:
        checks: list[dict[str, Any]] = []
        for name, test in self.outputs.items():
            value = outputs.get(name)
            for text, ok in test.run(value):
                checks.append({"text": f"output {name} {text}", "actual": describe_value(value), "met": ok})
        if self.log is not None:
            for text, ok in self.log.run(log_text or ""):
                checks.append({"text": f"log {text}", "met": ok})
        return all(c["met"] for c in checks), checks


def failed_checks(checks: list[dict[str, Any]]) -> str:
    """One line naming the checks that didn't pass, for errors and notifications."""
    bad = [c for c in checks if not c["met"]]
    return "; ".join(f"{c['text']} (got {c['actual']})" if c.get("actual") else c["text"] for c in bad)
