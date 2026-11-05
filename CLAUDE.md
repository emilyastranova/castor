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

README images (`.github/assets/*.svg`) are generated from the AstraLabs brand repo
(`../astralabs_design`) with fontTools: `python3 .github/assets/build_assets.py`. Don't
hand-edit the SVGs; change the script and regenerate.

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
  `tags` selector.
- Command library: `command_spec.py` (YAML format → `CommandSpec`), `command_render.py`
  (fills `{{ arg }}` placeholders; **every value goes through `shlex.quote`**, never plain
  string substitution), `command_library.py` (built-in sync from `CASTOR_COMMANDS_DIR` at
  startup, `apply_to_task`). A card with `command_ref` has its `command` rendered by the
  server; the API refuses raw `command`/`image` edits until it is detached.
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
- Playbooks (Phase 2c): `playbook_spec.py` (YAML → `PlaybookSpec`; command steps and
  server-side transform steps, references auto-added to `depends_on`),
  `playbook_transforms.py` (the transform ops), `services._advance` (runs ready steps,
  stores transform/skip results in `playbook_runs.step_results`, settles the run).
  `docs/PLAYBOOK_SPEC.md` is the authoring spec (keep it in sync with the format);
  `check_against_library` validates against the command library on save and in
  `POST /api/playbooks/validate`.
  `pipeline-demo` + `commands/demo-*` is a no-network test pipeline for the playbook UI;
  run an agent with `--allow-host --max-concurrency 4` to watch it.
- Branching: `playbook_conditions.py` (`when` condition trees, `expect`), step `join` and
  `on_failure` in `playbook_spec.py` (`readiness`, `step_statuses`, `run_outcome` are pure;
  test them without a DB). Conditions and expectations stay raw dicts on the step and are
  parsed on demand (like transform ops) so specs survive `model_dump` → `model_validate`.
  A stored step result with `job_id` overrides that job's status (a judgement or decision);
  a newer job supersedes it. `on_failure: ask` pauses a step (`attention`) and calls
  `notifications.notify`; `services.resolve_step` applies retry/continue/skip/fail and takes
  an `actor` for non-human resolvers. `branching-demo` exercises all of it.
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
  `Authorization: Bearer`. Agents authenticate with their own token (`agent_tokens.py`:
  only the SHA-256 is stored; the first agent to use an unused token is bound to it) or,
  while `CASTOR_ALLOW_SHARED_AGENT_SECRET` is on, the shared `CASTOR_AGENT_SECRET`, which
  is never accepted for an agent that has or had a token. Revoke/rotate call
  `gateway.disconnect`.
- Config is `pydantic-settings` with the `CASTOR_` prefix (`config.py`).
- SQLite uses `NullPool` (aiosqlite connections are bound to the event loop that opened
  them; the test client runs WebSocket sessions on another loop).
- Tests use Starlette's `TestClient` with a temp SQLite DB set via env vars in
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
otherwise `/bin/sh -c cmd` on the host in the job dir (if `--allow-host`). After the job,
`artifacts.py` collects declared outputs and `/work/artifacts/**` (skipping symlinks and
anything outside the job dir) and uploads them with stdlib `urllib` before `job.finished`. Processes start in their own
session so cancel can `killpg`. Only `websockets` is a dependency; keep it that way so the
agent stays trivially installable on target boxes.

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
- `JobConsole.svelte` loads `/api/jobs/{id}` then appends `job.log` events, buffering any
  that arrive during the initial load and de-duplicating by `seq`.
