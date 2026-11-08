# Phase 4 plan

Status: in progress (2026-11-08). Roadmap item **M4** in [ARCHITECTURE.md](ARCHITECTURE.md);
Phase 3 is done and recorded in [PHASE3.md](PHASE3.md).

Phase 4 opens Castor up to other software: scripts, CI pipelines and AI assistants drive it
through the same API the UI uses, with credentials that can do only what they're for.

| Slice | What you get | Depends on | Status |
| --- | --- | --- | --- |
| 4a. API tokens and reference | Personal tokens with scopes and an optional project list; every endpoint documented with the scope it needs | nothing | Built |
| 4b. Built-in MCP server | AI assistants (Claude, Gemini, IDEs…) work with Castor through a Model Context Protocol endpoint, using an API token | 4a | Built |

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

**Built.** Code: `mcp.py` (protocol and tools), `api/mcp.py` (`POST /api/mcp`); web
`McpConnect.svelte` on the API tokens page. Tests: `test_mcp.py`.

- **Transport.** Streamable HTTP at `/api/mcp`, stateless: each POST carries one JSON-RPC
  message (or a batch) and gets a plain JSON reply; notifications get 202; `GET` answers 405
  (no server-initiated stream). Protocol versions 2025-06-18, 2025-03-26 and 2024-11-05.
  Methods: `initialize`, `ping`, `tools/list`, `tools/call`.
- **Auth.** A bearer token is required (an API token; a session JWT also works when sent
  as a bearer); cookies are refused, so a web page can't drive it with someone's session.
- **Tools are REST endpoints.** Each tool names one endpoint and is called in-process
  (`httpx.ASGITransport`) with the caller's own Authorization header, so it can do exactly
  what the token can over HTTP: same scopes, role, project limit, validation and activity.
  `tools/list` shows only the tools the token's scopes grant (`Tool.scope` comes from
  `api_tokens.RULES`). Tools: `search`, `list_projects`, `get_board`, `get_card`,
  `list_comments`, `create_card`, `move_card`, `comment`, `run_card`, `list_jobs`, `get_job`,
  `get_job_output`, `get_job_results`, `cancel_job`, `list_commands`, `get_command`,
  `list_playbooks`, `run_playbook`, `get_run`, `resolve_step`, `list_assets`. Nothing
  deletes, and nothing reveals secrets.
- **Untrusted output.** Results that carry job output or what a job found start with a note
  that the text is data, not instructions. Long results are cut at 100k characters (job
  output keeps its end).
- `POST /api/tasks/{id}/move` now takes `position: null` for "top of the column", so callers
  (and `move_card`) don't need to compute one.
- **Resources.** `resources/list` gives each visible project's board
  (`castor://projects/{id}/board`); templates add `castor://cards/{id}`,
  `castor://jobs/{id}/output` (untrusted) and `castor://runs/{id}`. Reading one is the same
  in-process GET as a tool, and templates the token's scopes don't allow are hidden. No
  subscriptions: the endpoint has no stream back.
- **"via MCP".** The in-process calls carry `X-Castor-Via` with a key made at startup
  (`api_tokens.MCP_KEY`, so a client can't fake it); `deps.current_user` sets the `via`
  context variable and `record_activity` stores `payload.via = "mcp"`. The home timeline and a
  card's activity show "via MCP" after the person's name.
- **stdio.** `castor-mcp` (in the agent package, standard library only) forwards stdio
  JSON-RPC lines to `/api/mcp`, for clients that only speak stdio (Claude Desktop). It reads
  the token from `CASTOR_TOKEN` and the address from `--server` or `CASTOR_URL`.
- **UI.** "Connect an AI assistant" on the API tokens page: config for Claude Code, Claude
  Desktop (`castor-mcp`), VS Code and Gemini CLI.

Not built from the plan: prompts, resource subscriptions (a live job log), and the open
question of Castor's own AI using these tools.
