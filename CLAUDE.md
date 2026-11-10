# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What Castor is

A Kanban task manager and workflow orchestrator for automation pipelines. Cards are either
**manual** (a person does them) or **command** (an agent runs them). Dragging a command
card into the project's *active* column enqueues a Job; the dispatcher sends it to a
connected agent, which runs it in a container (or host shell) and streams output back into
the card. `docs/ARCHITECTURE.md` has the data model, event names, and the agent wire
protocol; keep it in sync when you change any of those.

`reference/` holds the *old* Castor repos. They are gitignored and are inspiration only:
do not copy code from them.

## Commands

One virtualenv at the repo root serves both Python packages. `node` 22+ for `web/`.

```bash
python3 -m venv .venv && .venv/bin/pip install -e "./server[dev]" -e "./agent[dev]"
(cd web && npm install)
```

Run the three processes (API first; the agent and the Vite proxy both target `:8000`):

```bash
.venv/bin/castor-server                        # API + WS on :8000, SQLite in ./data
cd web && npm run dev                          # UI on :5173, proxies /api and /ws
.venv/bin/castor-agent --secret change-me-agent-secret --name dev-agent --tags lab
```

`.claude/launch.json` defines `api` and `web` for the desktop app's preview server.
Default login is `admin` / `castor` (from `CASTOR_ADMIN_*`, created on first start).

Tests, lint, type-check, build:

```bash
.venv/bin/python -m pytest server/tests                       # all server tests
.venv/bin/python -m pytest agent/tests                        # agent tests
.venv/bin/python -m pytest server/tests/test_flow.py -k job   # one test by name
.venv/bin/ruff check server/castor_server agent/castor_agent
cd web && npm run check                                       # svelte-check
cd web && npm run build                                       # adapter-node build
```

Migrations live in `server/castor_server/migrations/` (inside the package so they ship in
the Docker image). The server applies them on startup (`CASTOR_AUTO_MIGRATE`, default on);
a database created before Alembic was used is stamped at the baseline revision first
(`db.run_migrations`). Create a migration from `server/` against an up-to-date DB:

```bash
cd server && ../.venv/bin/python -m alembic revision --autogenerate -m "..."
cd server && ../.venv/bin/python -m alembic upgrade head      # CASTOR_DATABASE_URL picks the DB
```

`env.py` renders `UTCDateTime` columns as `sa.DateTime(timezone=True)` so migrations don't
import app code; check generated files anyway. New JSON columns come out as
`postgresql.JSONB(astext_type=Text())` with `Text` unimported; write them as
`sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')` to match `models.JSONType`. On SQLite, migrations run without foreign-key
enforcement on purpose (batch table rebuilds would cascade-delete child rows); the app's own
connections turn it on.

Regenerate the default Material 3 palette: `cd web && node scripts/gen-tokens.mjs '#4f5bd5'`
writes `src/lib/m3/tokens.css` (committed, do not hand-edit).

README images (`.github/assets/*.svg`, each in dark and `-light`) are generated with fontTools
in the app's look (colours from `tokens.css`, Roboto Flex/Mono and Material Symbols, cached in
`.github/assets/.cache/`): `python3 .github/assets/build_assets.py`. Only the footer needs the
AstraLabs brand repo (`../astralabs_design`). Don't hand-edit the SVGs; change the script and
regenerate.
README screenshots (`.github/assets/screenshots/*.png`) come from a seeded throwaway demo
instance (`seed_demo.py`, `capture.py`); the steps are in that folder's README.

Full stack: `cp .env.example .env && docker compose up -d --build` (also works with
`podman compose` if a compose provider is installed). The Postgres path has not been
exercised on the original dev machine; SQLite is what the tests and dev loop use.

## Architecture

### Server (`server/castor_server`)

- `models.py` is the schema. All timestamps use `UTCDateTime` so SQLite returns
  tz-aware values; all JSON columns use `JSONType`. IDs are UUIDs.
- `services.py` holds domain operations shared by the REST routers, the gateway and the
  dispatcher: `enqueue_job`, `move_task` (the enqueue-on-active-column rule lives here),
  `record_activity`, and the `task_out`/`publish_task` serialisers that attach
  `latest_job` and comment/attachment counts. Add new domain rules here, not in routers.
- `events.py` is an in-process bus. Every mutation publishes a named event
  (`task.moved`, `job.log`, `agent.updated`, ...) with `project_id`; `ws/ui.py` fans these
  out to every authenticated browser. The UI relies on these events to stay in sync, so a
  new mutation path must publish or the board goes stale.
- `orchestrator/gateway.py` owns agent connections (`AgentConnection` registry keyed by
  agent id) and the agent protocol handlers. It opens its own `SessionLocal()` sessions
  because it runs outside request scope. `_unregister` is wrapped in `asyncio.shield`
  because the test client cancels handlers mid-cleanup.
- `orchestrator/dispatcher.py` runs `pump()` on wake (new job, agent change, job finish)
  and every `CASTOR_DISPATCH_INTERVAL` seconds. Matching rule is in `agent_matches`:
  free slot, required runtime (image → needs podman/docker), optional `agent_id` or
  `tags` selector. `{tags, all: true}` fans a card out to every connected agent with the tags
  (`services.enqueue_job`: one job each, a `batch_id`, `target_agent_id`), and a playbook run
  to one run per agent; step cards never fan out.
- Command library: `command_spec.py` (YAML format → `CommandSpec`), `command_render.py`
  (fills `{{ arg }}` placeholders; **every value goes through `shlex.quote`**, never plain
  string substitution), `command_library.py` (built-in sync from `CASTOR_COMMANDS_DIR` at
  startup, `apply_to_task`). A card with `command_ref` has its `command` rendered by the
  server; a raw `command`/`image` edit (the card dialog's Command field, the AI resolver's
  fixes) stays as a one-off change until the arguments change and it's rendered again;
  detaching (`command_ref: null`) keeps the command as plain shell.
- Artifacts: the dispatcher issues a one-off upload token per job (`artifacts.py`, only the
  SHA-256 is stored) and sends it with the paths to collect in `job.run`; the agent uploads
  to `POST /api/jobs/{id}/artifacts`, which creates `kind = artifact` attachments.
- Parsed outputs (Phase 2b): `parsers.py` is the registry (`@parser(name, type, ...)`, pure
  `(bytes, options)` → JSON functions, raise `ParseError`; parse XML only through
  `defusedxml`; run user regexes through `regex` with a timeout). Each parser declares an
  `Options` model and a sample file in `parser_samples/` (the Parsers page and
  `test_every_sample_parses` rely on it).
  `outputs.py` runs them after `job.finished` in a background task using the job's
  `output_specs` snapshot and stores `JobOutput` rows, publishing `job.outputs`.
  Parsers in the library: YAML (`parser_spec.py`: format + field map, or `format: javascript` with
  `code` defining `parse(text)`, run by `js_sandbox.py` in a V8 isolate with no files, network or
  imports, 3 s and 256 MB; never run user code as Python on the server),
  `parser_definitions`, registered next to the built-ins by `parser_library.py`
  (`parsers.get` / `all_parsers`, not `PARSERS`, when the library's count). Output its parser
  couldn't read (`outputs.is_mismatch`) gets an AI draft as a `ParserProposal`
  (`ai.draft_parser` runs every draft on the real output; `services.apply_parser_proposal`
  re-reads the job, inline `parser_spec` or `save_as`). A playbook step with `ask`/`ai` pauses
  on it (`result.parse`); `ai` steps use the draft by themselves (`ai.draft_parsers`). Saving to
  the library is always a person's choice. Web: `ParserEditor` (its Reference button opens `ParserGuide`: examples that load into the
  editor, types from `GET /api/parsers/reference`; keep its examples parsing), `ParserFix` (results and step
  decisions), `routes/parsers` (`?new=1&job=&output=`).
- Playbooks (Phase 2c): `playbook_spec.py` (YAML → `PlaybookSpec`; command steps and
  server-side transform steps, references auto-added to `depends_on`),
  `playbook_transforms.py` (the transform ops), `services._advance` (runs ready steps,
  stores transform/skip results in `playbook_runs.step_results`, settles the run).
  `docs/PLAYBOOK_SPEC.md` is the authoring spec (keep it in sync with the format);
  `check_against_library` validates against the command library on save and in
  `POST /api/playbooks/validate`.
  Built-in library: `tools/`, `commands/`, `playbooks/` ship real tooling (nmap, NetExec,
  ffuf, httpx, nuclei, subfinder, dnsx, testssl.sh, ssh-audit, trivy, curl). A built-in whose
  file is removed is retired at the next start (`_retire_builtins`; cards keep their command
  line). The no-network demo commands and playbooks (`pipeline-demo`, `branching-demo`,
  `ai-resolver-demo`, `demo-*`) are test fixtures in `server/tests/fixtures/`: conftest builds
  the tests' library from the built-ins plus those. Copy them into `commands/`/`playbooks/`
  locally to watch them in the UI (agent `--allow-host --max-concurrency 4`); don't ship them.
- Branching: `playbook_conditions.py` (`when` condition trees, `expect`), step `join` and
  `on_failure` in `playbook_spec.py` (`readiness`, `step_statuses`, `run_outcome` are pure;
  test them without a DB). Conditions and expectations stay raw dicts on the step and are
  parsed on demand (like transform ops) so specs survive `model_dump` → `model_validate`.
  A stored step result with `job_id` overrides that job's status (a judgement or decision);
  a newer job supersedes it. `on_failure: ask` pauses a step (`attention`) and calls
  `notifications.notify`; `services.resolve_step` applies retry/continue/skip/fail and takes
  an `actor` for non-human resolvers. The `branching-demo` test fixture exercises all of it.
- Run cards (3h): a run is one board card (`playbook_runs.card_id`, `TaskOut.run` summary,
  `services._card_to_review`, `ensure_run_cards`); step cards have `playbook_step_id` and are
  filtered out of the board (server `get_board`, client `BoardStore`). Web: `RunPanel.svelte`
  in TaskDialog's Run tab; the board page opens `?step=` on top and redirects step links.
- Tools (Phase 3a): `tool_spec.py` (YAML → `ToolSpec`: `image`, `build` recipe, `host`;
  `way_for` picks one per agent; recipe tags hash the recipe), `tool_library.py` (built-ins
  from `CASTOR_TOOLS_DIR` = `tools/`), `api/tools.py`. Commands say `tool: <id>`; the
  dispatcher resolves it into `job.tool` and the agent's `tools.py` pulls/builds/checks it.
  A recipe's `check` must exit 0 (it is the last build step). Never add tools to the agent
  image.
  Web: one Library page (`routes/library`, `?c=<command>` / `?t=<tool>` / `?new=`): tools are
  folders and their commands the rows, a detail pane per item (YAML is edited there), facets
  and grouping in `lib/library.ts` (pure, meant for the playbook palette too), rows in
  `components/library/CommandRow.svelte`. `/commands` and `/tools#id` redirect into it.
  Command usage (board cards you can see, playbooks) is `command_library.usage` and
  `GET /api/commands/{id}/usage`.
- People (Phase 3b): `people.py` (who can see a project, `@mention` parsing, assignee checks).
  Groups: `api/groups.py`, `groups`/`group_members`/`project_groups`. Visibility is
  `access.member_project_ids` (direct members plus groups), so use it, not `project_members`
  alone. `@group` expands to members; handles share a namespace with usernames
  (`people.handle_taken`). SSO keeps `sso_group` groups in step (`sso.sync_groups`). Web:
  `routes/settings/groups`, `GroupDialog`, groups in `MembersDialog`, `people.mentionables`.
  `tasks.assignees` and `comments.mentions` hold user ids as strings; `tasks.group_assignees` group ids
  (check "is it mine" with `people.assigned_to`/`my_group_ids`, web `people.assignedTo`). Personal notifications
  (`task.assigned`, `comment.mention`) use `notify(user_ids=..., webhooks=False)`. Web:
  `lib/people.svelte.ts` (per-project people + mention chips), `mentionSuggest.svelte.ts`.
  Person pages (3c): `api/people.py` (`/api/people`, `/api/people/{username}`, scoped to the
  viewer's projects), web `routes/people/`. Admin pages live under `routes/settings/` (its
  `+layout.ts` refuses non-admins; add sections to `SECTIONS` in its `+layout.svelte`).
- Assets (Phase 3f): `assets.py` (types and identity keys, `observe` to record and merge,
  parser mappings in `_map`/`MAPPED_PARSERS`, `parse_where` filters, scope, triggers and
  `run_fire`), `api/assets.py`; ingest is called from `outputs.parse_job`. Credential secrets go
  through `vault`, never into `fields`. Web: `routes/projects/[id]/assets`, `AssetDialog`,
  `TriggerDialog`, `AssetRefDialog` (assets as list arguments via `ListArgInput`).
- Search (Phase 3g): `search.py` (one permission-scoped query per kind of thing over the live
  tables; add new kinds to `TYPES` and a `Searcher` method) behind `GET /api/search`; web
  `CommandPalette.svelte` (Ctrl/Cmd+K, `lib/palette.svelte.ts`). `m3/Select` searches its
  options automatically past 7 (options can have `description`, `group`, `keywords`).
- AI (3e): `llm.py` (provider `KINDS`, adapters in `call`, httpx only, no SDKs; `complete`
  walks the purpose's chain with fallback, `check` rejects unusable answers), `ai.py`
  (resolver for `on_failure: ai` and Ask AI, `generate` validates drafts with the real spec
  parsers and repairs, regex suggestions, failure explanations). Never send variable values;
  logs are already masked. Tests use a local fake model server (`tests/test_ai.py`).
  The `ai-resolver-demo` fixture + `demo-trouble` (tests/fixtures) act out five realistic failures (timeout,
  harmless exit 1, renamed flag, prompt injection, missing key) to watch the resolver; run an
  agent with `--allow-host` (the flaky scenarios remember attempt 1 in `$TMPDIR`).
- Alerts (3k): `alerts.py` (`PRESETS` kinds, each building one request template; `render`
  escapes `{{ var }}` per content type, never format values in yourself; hooks `job_finished`,
  `run_finished`, `decision`, `approval`; `check_long_running` sweep started in `app.py`).
  Channel secrets go through `vault`, per field. Web: `routes/settings/alerts`,
  `AlertChannelDialog`, `AlertRulesForm` (card menu and Run Playbook).
- Notifications: `notifications.py` (one row per user, `subject` to clear related ones,
  webhooks via `CASTOR_NOTIFY_WEBHOOKS` posted from a thread so a dead webhook never blocks
  a run). Web: `lib/notifications.svelte.ts` + `NotificationBell.svelte` in the rail.
- Variables: `variables.py` (kinds = typed field templates, sets = instances; `resolve` turns
  a job's bindings into env vars + files at dispatch), `vault.py` (Fernet; `CASTOR_VAULT_KEY`
  or `<data_dir>/vault.key`), `api/variables.py`. A command's `vars` slots render as
  `"${CASTOR_VAR_<SLOT>_<FIELD>}"`, so **no variable value may ever be rendered into
  `task.command`/`job.command` or stored on a job**; values exist only in the `job.run`
  message, and the gateway masks secrets in logs. The agent writes files under
  `<job dir>/.castor/` (removed after the job, never collected as artifacts) and passes
  container env by name only (`-e NAME`).
  Target lists: an argument value `{"list": id}` links a set's list field; always render
  library commands through `command_library.apply_library` (not `apply_to_task`) so these
  are expanded.
- Auth: `POST /api/auth/login` sets an httpOnly cookie; the same JWT works as
  `Authorization: Bearer`. Users have a role (viewer/operator/admin): guard mutations
  with `deps.Operator` / `deps.Admin`, and anything under a project with `access.py`
  (`project_or_404`, `task_or_404`, `job_or_404`, `check_project`, `visible_project_ids`
  for lists; 404 for what the user can't see). Events of a project only reach sockets
  whose user sees it, so keep passing `project_id` to `bus.publish` (and `user_id=` for
  one user's events). Single sign-on: `sso.py` (OpenID Connect, PKCE, `id_token` verified against
  JWKS; `resolve_user`), `api/sso.py` (public `/api/auth/sso/*`, admin `/api/sso-providers`), web
  `routes/settings/sso`, login page buttons; `users.email` is admin-set (SSO links by it). Tests run a
  fake provider (`tests/test_sso.py`). There's always an active admin (API guard + `accounts.ensure_active_admin`
  at startup); `castor-admin` (`manage.py`) recovers accounts from the shell. Agents authenticate with their own token (`agent_tokens.py`:
  only the SHA-256 is stored; the first agent to use an unused token is bound to it) or,
  while `CASTOR_ALLOW_SHARED_AGENT_SECRET` is on, the shared `CASTOR_AGENT_SECRET`, which
  is never accepted for an agent that has or had a token. Revoke/rotate call
  `gateway.disconnect`.
- Import and export (3i): `transfer.py` (archive format, `values_for`/`remap` give rows new
  ids and rewrite ids inside JSON and text, `plan` is the preview, `run_import`), `api/transfer.py`.
  A new table that belongs to a project must be added to `PROJECT_TABLES` (in insert order)
  or it won't travel; vault-encrypted values go through `Secrets` (never plain text).
  Web: `ExportDialog`, `ImportDialog`, `LibraryTransfer`, `routes/settings/transfer`.
  Duplicating a card (3m), `services.duplicate_task`, reuses `values_for`/`remap`; a new
  table hanging off a card must decide whether a duplicate copies it.
- Instance settings admins change in the UI: `app_settings.py` (`AppSettings` fields are the
  keys and defaults), `api/app_settings.py`; web `lib/appSettings.svelte.ts`,
  `routes/settings/general`. Env-level config stays in `config.py`.
- API tokens (Phase 4a): `api_tokens.py` (`castor_pat_` tokens, `SCOPES`, `RULES` mapping
  every method+path to a scope; unmapped routes refuse tokens), checked in `deps.current_user`;
  `user.api_token` carries it, and `access.py` applies its project limit. **A new endpoint
  needs a rule in `RULES`** (`test_every_route_has_a_scope`). Token management, password and
  profile are session only. Web: `routes/tokens` (yours), `routes/settings/tokens` (admins).
  API reference at `/api/docs`.
- MCP server (4b): `mcp.py` (`TOOLS`: each tool is one REST endpoint, called in-process with the
  caller's own token, so it inherits that endpoint's scope and checks; never re-implement checks
  there; `RESOURCES` the same for `castor://` URIs), `api/mcp.py` (`POST /api/mcp`, bearer only).
  Its calls mark activity `payload.via = "mcp"` (`api_tokens.via`, set in `deps.current_user` from
  a startup key). `castor-mcp` (agent `mcp_stdio.py`, stdlib only) bridges stdio clients. Web:
  `McpConnect.svelte`.
  Prefer `_auto(...)` for new tools (inputs come from the endpoint's OpenAPI). A UI action
  should have its tool, apart from the deliberate gaps listed above `TOOLS +=`.
- SSH hosts: `orchestrator/ssh.py` (a `HostLink` per host keeps an asyncssh connection and
  registers it with the gateway as an agent whose `ws` is an in-process `SshSocket`, so it
  speaks the agent protocol unchanged), `api/ssh_hosts.py` (admin), web `SshHostDialog` on the
  Agents page. Keys go through `vault`; host keys are trusted on first use (`host_key_pending`
  until an admin accepts a change, per hop). Jump hosts come from one shared SSH config
  that `ssh_config.py` parses itself (allowlist: Host, HostName, Port, User, ProxyJump,
  keepalives; never pass it to asyncssh, whose ProxyCommand/Include would run or read things
  on the server). Variable values reach the host only in `.castor/env`
  (sourced and deleted by the job's script), never in a command line. Tests run a real
  asyncssh server in a thread (`tests/test_ssh_hosts.py`).
- Config is `pydantic-settings` with the `CASTOR_` prefix (`config.py`).
- SQLite uses `NullPool` (aiosqlite connections are bound to the event loop that opened
  them; the test client runs WebSocket sessions on another loop).
- Tests use Starlette's `TestClient` with a temp SQLite DB (or
  `CASTOR_TEST_DATABASE_URL`, e.g. a throwaway Postgres container) set via env vars in
  `tests/conftest.py` **before** the app is imported; the `auth` fixture clears the login
  cookie so tests authenticate only via the Bearer header. Test files share one database, so
  don't assume a test's data is the only data (look agents/projects up by name); the fake agent in `test_flow.py`
  is the reference for the wire protocol.

### Agent (`agent/castor_agent`)

`agent.py` is the connection loop (hello → `job.run`/`job.cancel` → `job.*` replies,
heartbeat). Each job gets its own directory `<workdir>/<job_id>/`. `runtime.py` builds the
argv: with an image it runs
`<podman|docker> run --rm --network <net> -v <job dir>:/work:Z --entrypoint /bin/sh image -c cmd`
(the entrypoint is overridden because tool images such as `instrumentisto/nmap` set it to the
tool itself; images therefore need `/bin/sh`),
otherwise `/bin/sh -c cmd` on the host in the job dir (if `--allow-host`). Commands write there as
`{{ work }}` (rendered `"${CASTOR_WORK}"`: `/work` in a container, set by the dispatcher; the job dir on
a host, set by the agent or SSH host); never hard-code `/work` in a command. After the job,
`artifacts.py` collects declared outputs and `/work/artifacts/**` (skipping symlinks and
anything outside the job dir) and uploads them with stdlib `urllib` before `job.finished`. Processes start in their own
session so cancel can `killpg`. Interactive jobs (`job.tty`, 3j) run under a stdlib `pty`
(`JobRunner._run_tty`; containers add `-t -i`) with `job.stdin`/`job.resize`/`job.signal` in and
`job.prompt` out (`PromptWatch`); other jobs get stdin from `/dev/null`. Only `websockets` is a
dependency; keep it that way so the agent stays trivially installable on target boxes.

### Web (`web/src`)

- Svelte 5 runes throughout (`$state`, `$derived`, `$props`, snippets). No stores from
  `svelte/store`; shared state is classes with rune fields (`realtime.svelte.ts`,
  `snackbar.svelte.ts`, `stores/board.svelte.ts`).
- `lib/m3/` is the Material 3 design system: `tokens.css` (generated color roles),
  `app.css` maps roles onto Tailwind v4 utilities via `@theme inline` (`bg-primary`,
  `text-on-surface-variant`, `rounded-md`, `shadow-e1`...) and defines the type-scale
  utilities (`title-medium`, `body-small`...) and the `state-layer` utility. Do not use
  raw Tailwind palette colors or Tailwind's `dark:` variant (theme is driven by
  `data-theme` on `<html>`, see `m3/theme.ts`); use the role tokens so light/dark both work.
  Each user can recolour every role from a seed (`users.theme_seed`, `m3/theme.ts` `applySeed`,
  `ThemeColorPicker`), so never hard-code a colour that should follow the theme.
- `lib/api.ts` is the typed client; pass SvelteKit's `fetch` in load functions
  (`api(fetch)`). `hooks.server.ts` rewrites server-side `/api/*` fetches to
  `API_INTERNAL_URL` and forwards the session cookie.
- `routes/+layout.server.ts` is the auth guard. It deliberately uses `route.id` rather
  than `url` so query-string navigations (opening a card adds `?task=`) do not re-run it.
- Pages copy load `data` into local rune state and then apply realtime events on top.
  Because SvelteKit re-merges `data` on every navigation, each page guards the copy with an
  identity check (`if (data.board !== loadedBoard)`); keep that pattern or realtime updates
  and optimistic moves get reverted.
- The board page keeps the open card in the URL (`?task=<id>`) so cards are linkable.
  `BoardColumn.svelte` keeps a local copy of its tasks for `svelte-dnd-action`, computes
  the new position from neighbours, and the receiving column reports the move.
- `JobResults.svelte` renders a run's parsed outputs (tables per output type, CSV copy that
  neutralises spreadsheet formulas via `lib/csv.ts`); the card's Output tab toggles it
  against `JobConsole`. Both it and `routes/parsers` (live parser playground) render values
  with `OutputCard.svelte`.
- Playbook editor: `routes/playbooks/edit/[id]` reuses `PlaybookVisualizer.svelte` with
  `editable` (connect from a node's handle, delete edges, palette drops, `onlayout`), and
  `lib/playbookEditor.ts` holds the pure spec operations (connect options, disconnect,
  rename, delete, implied dependencies). Copy state with JSON, not `structuredClone`
  (it can't clone Svelte state proxies). The server renders and checks YAML in
  `POST /api/playbooks/to-yaml` (`playbook_yaml.py`); keep its round trip exact
  (`test_playbook_yaml.py` runs every built-in playbook through it).
- Current project: `lib/currentProject.svelte.ts` (per user in localStorage). Home, Board and
  Assets in the nav (`routes/+layout.svelte`, `PhoneNav`) are the current project's; `/`
  redirects to its dashboard (or the `/projects` picker). `routes/projects/[id]/+layout.*`
  loads the project and makes it current, announcing a switch that came from a link; project
  page titles are `ProjectSwitcher.svelte`. Library pages (commands, tools, playbooks…) are
  shared; the Playbooks runs tab shows the current project's runs. The all-projects activity
  feed is Settings → Activity log (`routes/settings/activity`).
- `routes/projects/[id]/dashboard` (the project's Home) renders
  `lib/components/dashboard/Dashboard.svelte` (one `projectId`), fed by
  `api/dashboard.py` (`/summary`, `/feed`: activity events enriched with the comment, job,
  card and author). New activity types map to feed kinds in `dashboard.kind_of` and to where clicking them goes in
  `dashboard.link_for`. Every mutation that
  changes something people would care about records an activity (`record_activity`; library items
  through `services.library_activity`), or MCP work done through it never shows up in the feed.
  Posts (`api/posts.py`, `project_posts`) are comments on nothing in the timeline (`PostComposer.svelte`,
  rendered by `TimelineItem` from `FeedItem.post`). `#hashtags` are `posts.hashtags` on the server and
  `lib/hashtags.ts` in the browser: keep the two rules the same. Home filters by `?tag=`.
  A project dashboard starts with `ProjectNotes.svelte` (pinned notes, `api/notes.py`; edits
  keep earlier versions in `project_notes.history`). Pasting images into markdown boxes
  (comments, notes) goes through `lib/pastedImages.svelte.ts` + `NameImageDialog`.
  Comments and timeline posts are written in `MarkdownEditor.svelte` (Write/Preview, toolbar and
  shortcuts from `lib/markdownEdit.ts`, pure and tested by hand; @mentions, images pasted, dropped or
  picked via `PastedImages.add`, list continuation, drafts in localStorage by `draftKey`; `onsubmit` throws
  to keep the text). Other markdown boxes are `TextField` with `markdown` (` over a selection makes it
  code, `m3/markdownKeys.ts`); never on command, YAML or key fields. Rendered markdown (`{@html renderMarkdown(...)}` or `RichText`) takes
  `use:copyableCode` (`lib/copyCode.ts`): a copy button on code blocks, inline code copies on click.
- Tags: edit them with `m3/TagInput` (pills; Enter, comma, space or Tab adds; an existing tag
  is reused whatever its case; Backspace selects then removes), fed by `lib/tags.ts` `loadTags`
  from `GET /api/tags/cards|agents|library` (`api/tags.py`: in use, most used first, a project's
  own first). Don't add comma-separated tag text fields. Renaming or merging (`services.rename_tags`,
  `TagManagerDialog`): a project's card tags, or agent tags, which also rewrite the `agent_selector`
  of cards and runs and the live `AgentConnection.tags`.
- Motion: `lib/motion.ts` (`enter`/`leave` for list rows, `pop` for grid cards, `glide` for
  reorders). Use it for anything that appears or disappears live, and pass
  `{ enabled: false }` while a list (re)loads so page loads and filter changes don't cascade.
  It honours prefers-reduced-motion (fades only) and skips hidden tabs.
- `JobConsole.svelte` is xterm.js: it loads `/api/jobs/{id}` then writes `job.log` events,
  buffering any that arrive during the initial load and de-duplicating by `seq`. Plain streams
  get CRLF and colour (stderr red, system dim); a tty job's output is written raw. Its setup
  effect must track `jobId` (a derived primitive), not `job`: the job object is replaced on
  every update. Take control uses `/ws/jobs/{id}/terminal` (`ws/terminal.py`, `interactive.py`).
