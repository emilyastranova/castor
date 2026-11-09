# Phase 4 plan

Status: done (2026-11-08). Roadmap item **M4** in [ARCHITECTURE.md](ARCHITECTURE.md);
Phase 3 is done and recorded in [PHASE3.md](PHASE3.md).

Phase 4 opens Castor up to other software: scripts, CI pipelines and AI assistants drive it
through the same API the UI uses, with credentials that can do only what they're for.

| Slice | What you get | Depends on | Status |
| --- | --- | --- | --- |
| 4a. API tokens and reference | Personal tokens with scopes and an optional project list; every endpoint documented with the scope it needs | nothing | Built |
| 4b. Built-in MCP server | AI assistants (Claude, Gemini, IDEs…) work with Castor through a Model Context Protocol endpoint, using an API token | 4a | Built |
| 4c. Phone usability | Everything works on a phone; checking a run and answering a decision is easy one-handed | nothing | Built |

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
  `list_comments`, `create_card`, `move_card`, `comment`, `attach_file` (base64 content as a
  multipart upload; the result has `markdown` to embed it in a comment), `run_card`, `list_jobs`, `get_job`,
  `get_job_output`, `get_job_results`, `cancel_job`, `list_commands`, `get_command`,
  `list_playbooks`, `run_playbook`, `get_run`, `resolve_step`, `list_assets`, and for parsers
  `list_parsers`, `get_output_input`, `check_parser`, `create_parser`, `list_parser_drafts`,
  `use_parser_draft` (an assistant can read output its parser couldn't, write a parser and
  check it before keeping it). Nothing deletes, and nothing reveals secrets.
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

## 4c. Phone usability

**Built.** PRODUCT.md says "a phone must be enough to check a run and answer a decision".
A critique of the app at phone size on 2026-11-08 (375x812, spot checks at 412x915 and
320x640; Nielsen score 19/40) found it was the desktop layout squeezed down. What changed:

- **Phone navigation** (`PhoneNav.svelte`, `routes/+layout.svelte`): below `md` the rail is
  replaced by a bottom bar (Home, Projects, Search, Inbox, More); More is a sheet with the
  other destinations and the account. A red dot on More means live updates are offline.
- **Board header** (`routes/projects/[id]/+page.svelte`): controls drop out as the window
  narrows (view switch and "Assigned to me" below `lg`, search below `xl`, status chips below
  `2xl`); phones get a view-switch row and a New card FAB, "Only cards assigned to me" is in
  the menu. The project Dashboard and Assets pages share the view row.
- `bottom-above-nav` (`app.css`) puts FABs and snackbars above the phone bar.
- **One current project** (2026-11-08): Home, Board and Assets follow a project you pick once
  (remembered per person in the browser); the Projects nav item, the all-projects dashboard
  and the Board/Dashboard/Assets pill selector are gone. Switching is in the project title,
  the account menu (More on phones) and Ctrl+K; `/projects` is the picker. The all-projects
  activity feed moved to Settings → Activity log; the Playbooks runs tab shows the current
  project's runs.

- **Card dialog** (`TaskDialog.svelte`, `m3/Dialog.svelte`, `m3/Tabs.svelte`): `lg` dialogs are
  full screen below `sm` and use `dvh`; the tab row scrolls by itself; column, priority and
  assignees are one row of chips under the title (the column chip moves the card); a command
  card that has run opens with its form folded to a one-line summary; Run/Cancel sit in a bottom
  bar.
- **Decisions** (`StepDecision.svelte`, `RunPanel.svelte`): the choices are a list of radio rows
  with each explanation visible, a note under them and one "Apply decision" button; Skip and Fail
  ask first. A waiting decision is pinned to the top of the Run tab, and on phones the step list
  comes first with the graph one tap away ("Show the graph").
- **Settings** (`routes/settings/+page.svelte`, `sections.ts`): `/settings` is the list of
  sections on phones (desktop still opens General), and each section has a "← Settings" link.
- **Playbooks** (`routes/playbooks/+page.svelte`): list → detail with a back button on both tabs
  (a run opened from "Open in Playbooks" lands on its detail); the header's three create buttons
  are one "New ▾" menu, like the Library; detail headers stack and wrap.
- **Tap targets**: a `touch-target` utility (`app.css`) gives a control a 44px hit area under a
  coarse pointer without changing how it looks; `IconButton`, `Button`, `Chip` and the small
  hand-made buttons (assignee picker, "Assign to me", "Show everything", tag buttons, feed chips)
  use it.
- **Smaller fixes**: Home's feed chips are one sideways-scrolling row on phones and stat labels
  wrap to two lines instead of cutting off; the tokens page header and rows fit (API reference
  and Revoke become icon buttons); the board opens on the first column with cards (or the active
  one) and columns snap when swiped; the search palette hides its keyboard hints on touch screens;
  the card dialog reflows at 320px with no sideways scroll.
- **Arranging the board on touch** (`lib/arrange.svelte.ts`, `BoardColumn.svelte`): with a coarse
  pointer a swipe over cards scrolls; holding a card (450ms, still) starts arrange mode, where
  every card wiggles (an outline only with reduced motion) and drags. A "Drag cards to move them ·
  Done" bar replaces the New card button; Done, a tap on empty board or Escape ends it. Column
  snapping is off while arranging so it doesn't fight the drag's auto-scroll. Mouse dragging is
  unchanged.

Left as is: the graph's labels are still small (it is now behind a tap on the Run tab, and the
Playbooks detail on a phone shows it zoomable); the MCP snippet on the tokens page scrolls inside
its own box, which is fine for a command line.

Untested so far: real phones and iOS Safari (only emulated Chromium at 375 and 320px), touch
drag and drop on a real phone (arrange mode was exercised with synthetic touch events), the new decision panel on a
step actually waiting (type-checked, not seen live), a full screen-reader pass.

Questions the critique raised: should phones get a "Runs and decisions" inbox (one card per
waiting step) rather than a squeezed board? Is the live graph worth any space on a phone? Should
a one-tap decision be undoable rather than confirmed?

