# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What Castor is

A Kanban task manager and job orchestrator for pentest engagements. Cards are either
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
.venv/bin/python -m pytest server/tests/test_flow.py -k job   # one test by name
.venv/bin/ruff check server/castor_server agent/castor_agent
cd web && npm run check                                       # svelte-check
cd web && npm run build                                       # adapter-node build
```

Migrations (run from `server/`; `CASTOR_DATABASE_URL` selects the DB):

```bash
cd server && ../.venv/bin/python -m alembic revision --autogenerate -m "..."
cd server && ../.venv/bin/python -m alembic upgrade head
```

Note: autogenerate renders JSON columns as plain `sa.JSON()`; replace with
`sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')` to match `models.JSONType`.
In dev, `CASTOR_AUTO_MIGRATE=true` (default) runs `create_all` at startup instead.

Regenerate the default Material 3 palette: `cd web && node scripts/gen-tokens.mjs '#4f5bd5'`
writes `src/lib/m3/tokens.css` (committed, do not hand-edit).

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
- Auth: `POST /api/auth/login` sets an httpOnly cookie; the same JWT works as
  `Authorization: Bearer`. Agents authenticate with the shared `CASTOR_AGENT_SECRET`.
- Config is `pydantic-settings` with the `CASTOR_` prefix (`config.py`).
- Tests use Starlette's `TestClient` with a temp SQLite DB set via env vars in
  `tests/conftest.py` **before** the app is imported; the fake agent in `test_flow.py`
  is the reference for the wire protocol.

### Agent (`agent/castor_agent`)

`agent.py` is the connection loop (hello → `job.run`/`job.cancel` → `job.*` replies,
heartbeat). `runtime.py` builds the argv: with an image it runs
`<podman|docker> run --rm --network <net> -v workdir:/work:Z image /bin/sh -c cmd`,
otherwise `/bin/sh -c cmd` on the host (if `--allow-host`). Processes start in their own
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
- `JobConsole.svelte` loads `/api/jobs/{id}` then appends `job.log` events, buffering any
  that arrive during the initial load and de-duplicating by `seq`.
