# Phase 4 plan

Status: in progress (2026-11-08). Roadmap item **M4** in [ARCHITECTURE.md](ARCHITECTURE.md);
Phase 3 is done and recorded in [PHASE3.md](PHASE3.md).

Phase 4 opens Castor up to other software: scripts, CI pipelines and AI assistants drive it
through the same API the UI uses, with credentials that can do only what they're for.

| Slice | What you get | Depends on | Status |
| --- | --- | --- | --- |
| 4a. API tokens and reference | Personal tokens with scopes and an optional project list; every endpoint documented with the scope it needs | nothing | Built |
| 4b. Built-in MCP server | AI assistants (Claude, Gemini, IDEs…) work with Castor through a Model Context Protocol endpoint, using an API token | 4a | Planned |

## 4a. API tokens and reference

**Built.** Code: `api_tokens.py` (format, scope catalogue, route rules), `deps.py` (token
authentication), `access.py` (project limit), `api/tokens.py`; web `routes/tokens`,
`routes/settings/tokens`, `ApiTokenDialog`, `ApiTokenList`. Tests: `test_api_tokens.py`.

- **Tokens.** Anyone makes their own (account menu → API tokens): a name, scopes, optionally
  some projects, and an expiry (30/90/365 days or never). The token (`castor_pat_…`) is
  shown once; the server keeps its SHA-256, the first characters to recognise it, and when
  and from where it was last used. Owners revoke their own; admins see and revoke everyone's
  (Settings → API tokens). Revoking closes the token's live UI sockets.
- **A token acts as its user, never more.** Their role and project membership still apply,
  so a viewer's token can't write and a disabled account's tokens stop working. Scopes a
  role could never use (`admin` for an operator, writes for a viewer) are refused when the
  token is made.
- **Scopes** are `<area>:read` / `<area>:write` (write includes read): `projects`, `jobs`,
  `library`, `variables`, `assets`, `notifications`, `agents`; plus `playbooks:run`,
  `secrets:reveal` (secret variable values and stored credentials), `ai:use`, `transfer`
  (export/import) and `admin`. The UI offers "Read only", "Everything you can do" and a
  picker.
- **Every route has a rule** (`api_tokens.RULES`, first match wins, by method and path).
  An unmapped route refuses tokens, so a new endpoint stays closed until it's given a scope;
  `test_every_route_has_a_scope` fails until it is. Checking and rendering endpoints that
  change nothing (`/playbooks/validate`, `/commands/{id}/render`…) need only read.
- **Session only:** managing tokens, the password, the profile and avatar refuse tokens,
  so a leaked token can't mint a broader one or take the account over. `GET /api/auth/me`
  works with any token ("who am I").
- **Project limit.** A token limited to some projects sees only those (even an admin's):
  `visible_project_ids` / `can_see` intersect with it, as do search, notifications and the
  UI socket. Such a token can't create projects.
- **Sockets.** `/ws/ui` needs `projects:read`, an interactive job's terminal `jobs:write`.
  Tokens go in the `Authorization` header only, never `?token=` (URLs end up in logs).
- **Reference.** Swagger UI at `/api/docs`, ReDoc at `/api/redoc`, the schema at
  `/api/openapi.json` (moved under `/api` so the Vite proxy and Caddy serve them). Each
  operation says its scope in its description and as `x-castor-scope`; "Authorize" takes a
  token.

Not built: rotating a token in place (make a new one, revoke the old), per-token rate
limits, service accounts that aren't a person (a user made for the purpose works today).

## 4b. Built-in MCP server

**Planned.** An MCP endpoint in the server so an AI assistant can work with Castor the way
a person does in the UI: look at boards, make and run cards, read output, run playbooks,
answer paused steps.

- **Transport.** Streamable HTTP at `/api/mcp` (inside the API, so Caddy and the Vite proxy
  already route it), authenticated with an API token (4a) as `Authorization: Bearer`. The
  token's scopes decide which tools the server lists, and each call goes through the same
  checks as the REST endpoint behind it. A small `castor-mcp` stdio shim for clients that
  only speak stdio (it forwards to the HTTP endpoint).
- **Tools** (thin wrappers over `services.py`, not over HTTP): `search`, `list_projects`,
  `get_board`, `get_card`, `create_card` (manual, raw command, or a library command with
  arguments), `move_card`, `comment`, `run_card`, `get_job` / `get_job_output` (plain text,
  `interactive.plain`, tail by default), `cancel_job`, `list_commands` / `get_command`,
  `list_playbooks`, `run_playbook`, `get_run`, `resolve_step`, `list_assets`. Each declares
  its scope; destructive ones (`delete_*`) are left out at first.
- **Resources.** Cards, job logs and playbook runs as `castor://` URIs, so a client can
  attach them as context; subscribing to a job's log streams new output.
- **Prompts.** "Triage this failed card", "Summarise this project's findings", "Draft a
  command for <tool>" (these reuse 3e's prompts where they exist).
- **Safety.** Secret variable values never appear in tool results (only with
  `secrets:reveal`, and then still masked unless asked). Job output can contain hostile text
  (3e's banner test), so tool results mark it as untrusted data. Every call is recorded in
  the activity feed as the token's user, "via MCP".
- **UI.** On the API tokens page: "Connect an AI assistant" with ready-to-paste config for
  Claude Desktop/Code, Gemini CLI and VS Code, and a token preset for it.
- **Open questions:** an official MCP Python SDK dependency, or the small JSON-RPC subset
  by hand (the server already avoids SDKs for LLMs)? Should Castor's own AI (3e) use these
  tools too, so the resolver can look around before deciding?
