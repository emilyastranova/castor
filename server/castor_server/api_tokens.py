"""Personal API tokens: scripts, CI and other tools acting as a person, with less power.

A token is `castor_pat_` plus 32 random bytes (URL-safe base64), sent as
`Authorization: Bearer`. The server keeps only its SHA-256, so it's shown once. A request
with a token acts as the token's user, so their role and projects still apply, narrowed
by the token: its scopes say which parts of the API it may use (`required_scope` maps every
route to one), and its project list, if any, hides every other project (`access.py`).

Scopes are `<area>:read` / `<area>:write` (write includes read) plus a few single ones.
A route nobody mapped answers 403 for tokens: new routes stay closed until they're given a
scope (`test_every_route_has_a_scope` keeps that honest). Token management itself, the
password and the profile are for signed-in sessions only, so a leaked token can't mint
a broader one or take over the account."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from .models import ApiToken, User, UserRole

PREFIX = "castor_pat_"


def new_token() -> tuple[str, str, str]:
    """(token, its hash, the prefix shown in the UI)."""
    token = PREFIX + secrets.token_urlsafe(32)
    return token, hash_token(token), token[: len(PREFIX) + 6]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def is_api_token(token: str | None) -> bool:
    return bool(token) and token.startswith(PREFIX)


@dataclass(frozen=True)
class Scope:
    name: str
    label: str
    description: str
    group: str
    role: UserRole = UserRole.viewer  # the least role that can use it at all


V, O, A = UserRole.viewer, UserRole.operator, UserRole.admin
SCOPES: dict[str, Scope] = {s.name: s for s in [
    Scope("projects:read", "Read projects", "Projects, boards, cards, comments, attachments, notes, activity, the dashboard, search and people", "Projects"),
    Scope("projects:write", "Change projects", "Create and edit projects, columns, cards, comments, attachments and notes; move cards", "Projects", O),
    Scope("jobs:read", "Read jobs", "Job status, logs, output, parsed results and artifacts", "Jobs"),
    Scope("jobs:write", "Run jobs", "Run command cards, cancel jobs, re-parse outputs, type into interactive jobs", "Jobs", O),
    Scope("playbooks:run", "Run playbooks", "Start, cancel and resolve playbook runs (reading runs needs projects:read)", "Jobs", O),
    Scope("library:read", "Read the library", "Commands, tools, parsers, playbooks and variable kinds; render and validate them", "Library"),
    Scope("library:write", "Change the library", "Create, edit and delete commands, tools, playbooks and variable kinds; prepare tools", "Library", O),
    Scope("variables:read", "Read variables", "Variable sets (secret values stay hidden)", "Variables"),
    Scope("variables:write", "Change variables", "Create, edit and delete variable sets and their files", "Variables", O),
    Scope("secrets:reveal", "Reveal secrets", "Read secret variable values and stored credentials in plain text", "Variables", O),
    Scope("assets:read", "Read assets", "Assets, scope and triggers", "Assets"),
    Scope("assets:write", "Change assets", "Add, import and edit assets, scope and triggers; approve trigger fires", "Assets", O),
    Scope("notifications:read", "Read notifications", "Your notifications", "You"),
    Scope("notifications:write", "Mark notifications read", "Mark your notifications as read", "You"),
    Scope("ai:use", "Use AI", "Generate drafts, suggest regexes and explain jobs with the configured models", "Other"),
    Scope("transfer", "Import and export", "Export archives, preview and run imports", "Other"),
    Scope("agents:read", "Read agents", "Agents and whether they're connected", "Admin"),
    Scope("agents:write", "Manage agents", "Rename, tag and forget agents", "Admin", A),
    Scope("admin", "Administer", "Users, agent tokens, instance settings, alert channels and AI providers", "Admin", A),
]}

# Presets the UI offers.
READ_ONLY = sorted(n for n in SCOPES if n.endswith(":read"))
EVERYTHING = sorted(SCOPES)

ANY = "any"  # any valid token (who am I)
SESSION = "session"  # signed-in sessions only, never a token
PUBLIC = "public"  # no user at all (or its own credential)

# (methods, path pattern, scope); the first match wins. Patterns match the request path
# segment by segment: `*` is one segment, a trailing `**` any number (including none).
# A scope written as "area:rw" means area:read for GET/HEAD and area:write otherwise.
RULES: list[tuple[str, str, str]] = [
    ("*", "/api/health", PUBLIC),
    ("*", "/api/auth/login", PUBLIC),
    ("*", "/api/auth/logout", PUBLIC),
    ("POST", "/api/jobs/*/artifacts", PUBLIC),  # the agent, with the job's one-off token
    ("GET", "/api/auth/me", ANY),
    ("*", "/api/auth/**", SESSION),
    ("*", "/api/tokens/**", SESSION),
    ("*", "/api/openapi.json", PUBLIC),
    # People and profiles
    ("GET", "/api/users/directory", "projects:read"),
    ("GET", "/api/users/*/avatar", "projects:read"),
    ("*", "/api/users/*/avatar/**", SESSION),
    ("*", "/api/users/**", "admin"),
    ("GET", "/api/people/**", "projects:read"),
    # Admin
    ("*", "/api/agent-tokens/**", "admin"),
    ("GET", "/api/settings", "projects:read"),
    ("*", "/api/settings", "admin"),
    ("GET", "/api/alert-presets", "projects:read"),
    ("GET", "/api/alert-channels/brief", "projects:read"),
    ("*", "/api/alert-channels/**", "admin"),
    ("*", "/api/ai/kinds", "admin"),
    ("*", "/api/ai/providers/**", "admin"),
    ("*", "/api/ai/usage", "admin"),
    ("*", "/api/ai/**", "ai:use"),
    ("GET", "/api/transfer/history", "admin"),
    ("*", "/api/export", "transfer"),
    ("*", "/api/import/**", "transfer"),
    ("*", "/api/agents/**", "agents:rw"),
    # Jobs and runs
    ("POST", "/api/tasks/*/run", "jobs:write"),
    ("GET", "/api/tasks/*/jobs", "jobs:read"),
    ("*", "/api/jobs/**", "jobs:rw"),
    ("POST", "/api/playbooks/*/run", "playbooks:run"),
    ("GET", "/api/playbook-runs/**", "projects:read"),
    ("*", "/api/playbook-runs/**", "playbooks:run"),
    # Library (checking and rendering change nothing)
    ("POST", "/api/playbooks/validate", "library:read"),
    ("POST", "/api/playbooks/to-yaml", "library:read"),
    ("POST", "/api/tools/validate", "library:read"),
    ("POST", "/api/commands/*/render", "library:read"),
    ("POST", "/api/parsers/*/test", "library:read"),
    ("*", "/api/playbooks/**", "library:rw"),
    ("*", "/api/commands/**", "library:rw"),
    ("*", "/api/tools/**", "library:rw"),
    ("*", "/api/parsers/**", "library:rw"),
    ("*", "/api/variables/kinds/**", "library:rw"),
    # Variables
    ("POST", "/api/variables/sets/*/reveal/*", "secrets:reveal"),
    ("*", "/api/variables/sets/**", "variables:rw"),
    # Assets
    ("POST", "/api/assets/*/reveal", "secrets:reveal"),
    ("*", "/api/assets/**", "assets:rw"),
    ("*", "/api/projects/*/assets/**", "assets:rw"),
    ("*", "/api/projects/*/scope", "assets:rw"),
    ("*", "/api/projects/*/asset-triggers", "assets:rw"),
    ("*", "/api/projects/*/asset-trigger-fires", "assets:rw"),
    ("*", "/api/asset-triggers/**", "assets:rw"),
    ("*", "/api/asset-trigger-fires/**", "assets:rw"),
    # You
    ("*", "/api/notifications/**", "notifications:rw"),
    # Projects and everything in them
    ("*", "/api/projects/**", "projects:rw"),
    ("*", "/api/clients/**", "projects:rw"),
    ("*", "/api/tasks/**", "projects:rw"),
    ("*", "/api/attachments/**", "projects:rw"),
    ("*", "/api/notes/**", "projects:rw"),
    ("*", "/api/activity", "projects:read"),
    ("*", "/api/dashboard/**", "projects:read"),
    ("*", "/api/search", "projects:read"),
    # Sockets
    ("*", "/ws/ui", "projects:read"),
    ("*", "/ws/jobs/*/terminal", "jobs:write"),
]

READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def _matches(pattern: str, path: str) -> bool:
    want, got = pattern.strip("/").split("/"), path.strip("/").split("/")
    if want[-1] == "**":
        want = want[:-1]
        if len(got) < len(want):
            return False
        got = got[: len(want)]
    return len(want) == len(got) and all(w == "*" or w == g for w, g in zip(want, got, strict=True))


def required_scope(method: str, path: str) -> str | None:
    """The scope a request needs (or ANY / SESSION / PUBLIC); None if no rule covers it."""
    method = method.upper()
    for methods, pattern, scope in RULES:
        if methods != "*" and method not in methods.split(","):
            continue
        if _matches(pattern, path):
            if scope.endswith(":rw"):
                area = scope[: -len(":rw")]
                return f"{area}:read" if method in READ_METHODS else f"{area}:write"
            return scope
    return None


def grants(scopes: list[str] | set[str], needed: str) -> bool:
    """Whether a token with these scopes may do something needing `needed`."""
    if needed in (ANY, PUBLIC):
        return True
    if needed == SESSION:
        return False
    have = set(scopes)
    if needed in have:
        return True
    area, _, action = needed.partition(":")
    return action == "read" and f"{area}:write" in have


def check(token: ApiToken, method: str, path: str) -> str | None:
    """Why the token may not make this request, or None if it may."""
    needed = required_scope(method, path)
    if needed is None:
        return "API tokens can't use this endpoint"
    if needed == SESSION:
        return "This needs a signed-in session; API tokens can't use it"
    if not grants(token.scopes or [], needed):
        return f"This token doesn't have the {needed} scope"
    return None


def usable(token: ApiToken, now: datetime | None = None) -> str | None:
    """Why the token doesn't work any more, or None if it does."""
    if token.revoked_at is not None:
        return "This API token was revoked"
    if token.expires_at is not None and token.expires_at <= (now or datetime.now(UTC)):
        return "This API token has expired"
    return None


def scopes_for(user: User, wanted: list[str]) -> list[str]:
    """The requested scopes, checked: known, and usable by someone of the user's role.
    Raises ValueError with the reason."""
    out = set()
    for name in wanted:
        scope = SCOPES.get(name)
        if scope is None:
            raise ValueError(f"Unknown scope {name!r}")
        if not user.can(scope.role):
            raise ValueError(f"{scope.label} ({name}) needs the {scope.role.value} role")
        out.add(name)
    if not out:
        raise ValueError("Choose at least one scope")
    return sorted(out)


def project_limit(user: User) -> set[str] | None:
    """The project ids (strings) the request's token is limited to, or None."""
    token = user.api_token
    return None if token is None or token.project_ids is None else {str(p) for p in token.project_ids}
