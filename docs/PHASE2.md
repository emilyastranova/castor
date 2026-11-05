# Phase 2 design: command library, structured outputs, playbooks

**Status: done (2026-11-04).** Every slice is built (2a–2f), along with several things the
original plan didn't have (see [Built beyond the plan](#built-beyond-the-plan)). Roadmap
item **M2** in [ARCHITECTURE.md](ARCHITECTURE.md). This document is kept as the record of
the Phase 2 design (decided 2026-10-15), with notes on how each part was actually built;
new work isn't tracked here.

Phase 2 turns Castor from "run this shell command on an agent" into "run a known tool with
typed inputs, get structured results, and feed them into the next tool". It has five
parts. They are listed in build order; each one is usable on its own.

| Slice | What you get | Depends on | Status |
| --- | --- | --- | --- |
| 2a. Command library + per-job artifacts | Pick "Nmap ping sweep" from a library, fill in typed fields, files the tool writes come back to the card | nothing | Built |
| 2b. Parsers + structured outputs | nmap XML becomes a hosts/services table on the card | 2a | Built |
| 2c. Playbooks (runs as cards) | A chain of blocks becomes linked cards that dispatch as their inputs become ready | 2b | Built |
| 2d. Playbook graph editor | Drag blocks and wire outputs to inputs | 2c | Built |
| 2e. Per-agent tokens | Enroll and revoke agents individually | nothing | Built |
| 2f. Users and roles | Real accounts, project membership, viewer/operator/admin | nothing | Built |

2e and 2f are independent of the playbook track and can be slotted in anywhere.

## Gaps in the code at the start of Phase 2

- ~~**Shared agent workdir.**~~ Fixed in 2a: each job gets `<workdir>/<job_id>/`.
- ~~**No dependencies between cards.**~~ Fixed in 2c: `tasks.depends_on`.
- ~~**One shared agent secret** for every agent.~~ Fixed in 2e: per-agent tokens.
- ~~**Command templates are unquoted.**~~ Fixed in 2a: every value goes through
  `shlex.quote` (`command_render.py`).

## 2a. Command library and per-job artifacts

**Definitions.** Keep the YAML format in `commands/` and load it into a `command_definitions`
table at startup (source = `builtin`), plus definitions created in the UI (source = `user`).
Key fields: `id`, `name`, `image`, `args[]` (name, type, required, default, example, regex,
choices), `command` (template), `outputs[]` (name, type, parser, source path).

**Rendering.** A small renderer, not full Jinja. Each `{{ name }}` is replaced with
`shlex.quote(value)`; list values are quoted per item and joined with spaces (or with the
separator the arg declares, e.g. `,` for nmap target lists, quoted as one word). Values are
validated against the arg's type and `regex` before rendering. Rendering happens on the
server, so the agent still receives a single command string, and the rendered command is
stored on the Job for the audit trail.

**Cards.** A command card gets `command_ref` (definition id + version) and `args` (values).
The card dialog shows a typed form instead of the raw command box, with the rendered
command shown read-only underneath. Free-form shell cards stay as they are.

**Per-job workdir.** The agent creates `<workdir>/<job_id>/` and mounts it as `/work` for
that job only. When the job finishes, the agent uploads files matching the definition's
declared outputs (and anything under `/work/artifacts/`) to
`POST /api/jobs/{id}/artifacts`, authenticated with a short-lived per-job token sent in
`job.run`. Uploaded files become Attachments with `kind = artifact` and `job_id` set, so
they already show on the card. Size cap per file and per job (configurable).

As built: as designed. The command format is documented in `commands/README.md`.
Arguments also gained `flag:` (render as `--flag value`, or a bare switch for booleans),
`show_if:` (show an argument only when a boolean is on) and `choices_from:` (a dropdown
filled from a variable set).

## 2b. Parsers and structured outputs

**Parsers** are server-side Python functions in a registry, e.g. `nmap.hosts`,
`nmap.services`, `ffuf.results`, `lines` (one value per non-empty stdout line). Each takes the
artifact bytes (or stdout) and returns a typed value. Parsing runs after artifacts are
uploaded, in a worker task so a large file can't block the API.

**Types** are a small fixed set so ports can be checked when wiring blocks:
`string`, `number`, `boolean`, `list<string>`, `host` / `list<host>` (`{ip, hostnames[],
status}`), `service` / `list<service>` (`{host, port, proto, name, product, version}`),
`url` / `list<url>`.

**Storage.** `job_outputs (job_id, name, type, value JSON, parser, error)`. The card's
Output tab gains a "Results" view: tables for hosts/services/urls, chips for lists, with
copy-as-CSV. Parse failures show on the card and do not fail the job.

As built: outputs are snapshotted onto the job at dispatch (`jobs.output_specs`), an
output's `type` defaults to its parser's type (a mismatch is a validation error), and nmap's
own fatal errors (`runstats/finished exit="error"`) are reported instead of an empty list.
CSV copies prefix cells starting with `= + - @` so external untrusted strings can't become
spreadsheet formulas.

## 2c. Playbooks

**Definition.** A playbook is a DAG of steps. Each step references a command definition and
binds each arg to one of: a literal, a playbook input (asked when the run starts), or an
upstream output (`steps.sweep.outputs.hosts`). Optional per-step `for_each` fans out over a
list (one job per host), capped by a max.

**Runs become cards (recommended).** Starting a run creates one card per step in To Do,
linked with `depends_on`, plus a parent "playbook run" card that shows progress. A step's
card dispatches automatically when all upstream steps succeeded, using their outputs as
args. This keeps everything on the Kanban board: people can comment on, re-run, or
manually complete any step, and a failed step stops its downstream steps (shown as
"blocked"). Alternative: one card per run with an internal step list; less board clutter,
less visibility.

**Model changes.** `playbooks`, `playbook_steps`, `playbook_runs`; `tasks.depends_on` (list
of task ids) and `tasks.playbook_run_id`; the dispatcher checks dependencies before
enqueueing.

As built:

- Definitions are YAML (`playbooks/*.yaml` built-ins, user ones edited in the UI) stored
  whole in `playbook_definitions.spec`; there is no `playbook_steps` table. Each run keeps a
  snapshot of the spec. The authoring format is
  [PLAYBOOK_SPEC.md](PLAYBOOK_SPEC.md).
- Runs are one card per command step, linked with `depends_on`, as recommended. There is no
  parent "run" card: the Playbooks page shows each run as a live graph with a step
  inspector instead.
- The server advances runs (`services._advance`) when a job finishes and its outputs are
  parsed; the dispatcher itself doesn't look at dependencies.
- `for_each` creates the item cards when the step becomes ready (the count isn't known
  before), tagged with `tasks.playbook_item`. The items count as one job for the step's
  status (`services.FanOut`), with an optional `concurrency` limit and a `max_items` cap
  (refuse, don't truncate) instead of the planned silent max.

## 2d. Graph editor

`@xyflow/svelte` canvas: blocks from the command library on the left, steps as nodes with
typed input/output ports, edges only allowed between compatible types (`list<host>` into
an arg that accepts a host list, with an optional "join as comma list" adapter). Saves to the
same playbook model as 2c, so a YAML/list editor can come first and the canvas later.

As built: `/playbooks/edit/<id>` (or `new`, or a built-in, which saves as a copy). It reuses
the run graph (`PlaybookVisualizer.svelte`, hand-rolled, no `@xyflow/svelte`) in an
editable mode, so the editor and runs look the same:

- A palette of library commands plus a Transform block; drag onto the canvas or click.
- Connect by dragging from a step's handle onto another step. A menu offers what the
  connection can mean: bind an output to an argument (required ones first), add it to a
  transform's input, run once per item (`for_each`), run after it, or run only if it fails.
  Types aren't enforced on the connection; the server's validation is.
- A properties panel per step: arguments (value, playbook input, step output, current
  item), variable slots, `for_each`, a simple condition builder (one value test or one
  status test), `join`, `on_failure`, `skip_if_empty`, a simple `expect` (outputs that
  mustn't be empty, a log regex), and transform ops as form rows. Anything more complex
  (nested conditions, other expectations) shows as text and is edited in the YAML tab.
- With no step selected: name, description, tags and inputs.
- A YAML tab shows exactly what saving writes; editing it and applying updates the graph.
- Every change is checked by `POST /api/playbooks/to-yaml` (debounced): it renders the
  spec as minimal YAML (`playbook_yaml.py`) and validates it, and problems show on the
  nodes. Positions are saved in the spec's `layout`, which run graphs also use.
- Saving rewrites the YAML, so comments in a hand-written playbook are lost (the editor
  warns about this).

## 2e. Per-agent tokens

Admin creates an enrollment token in the UI (shown once). The agent sends it in `hello`; the
server stores only a hash, binds it to the agent record on first use, and the agents page
gets Revoke (disconnects immediately) and Rotate. The shared secret stays as an optional
dev fallback behind a setting.

As built: as designed (`agent_tokens.py`, `api/agent_tokens.py`; the Agents page has
"Enroll agent", and each agent shows its token prefix with Rotate and Revoke). Decisions
made on the way:

- The shared secret (`CASTOR_ALLOW_SHARED_AGENT_SECRET`, default on so existing agents
  keep working) can't connect as an agent that has or had a token, so it can't impersonate
  one or undo a revoke. Forgetting the agent clears this.
- A token, once bound, decides which agent connects; `--name` only renames it.
- Rotating keeps the token record and its agent; the old value stops working at once.
- An agent refused at hello exits (code 2) rather than retrying forever.
- There's no expiry for unused tokens; revoke or delete them on the Agents page.

## 2f. Users and roles

User CRUD for admins, roles `admin` / `operator` / `viewer`, per-project membership, and
authorization checks in the API (viewers read-only; operators can run jobs; admins manage
users, agents and tokens). Comment/attachment author checks reuse this.

As built (see Auth in ARCHITECTURE.md): one global role per user, and membership decides
which projects a non-admin sees (no per-project roles). Operators also edit the shared
libraries and create projects (becoming members). Users are disabled, not deleted, so
their history keeps its authors. The UI socket filters events by what each user can see.
The migration keeps existing access: `is_admin` → admin, others → operator, and every
existing user becomes a member of every existing project. Tested on SQLite and Postgres,
including an install with hand-made users; the server test suite also runs against
Postgres with `CASTOR_TEST_DATABASE_URL`.

## Built beyond the plan

- **Transform steps** (`playbook_transforms.py`): server-side steps between commands that
  filter, pluck, format, dedupe, sort and limit upstream results, with no card or agent.
  They do the job the planned "join as comma list" adapter would have.
- **Variables** (`variables.py`, `vault.py`): typed kinds (credential, SSH key, listener,
  API token, target list, choice list, custom) and sets of values. Secrets are encrypted at
  rest, reach the job only as environment variables and files, and are masked in output.
  Commands declare variable slots; playbooks take varset inputs.
- **Target lists**: import hosts/IPs/URLs from a file with a preview, save them as sets, and
  link a saved list to any text or list field of a card or playbook.
- **Conditions and decisions**: `when:` branches, failure branches, `join: any|always`,
  `expect:` checks on outputs and logs, and `on_failure: stop|continue|ask`. `ask` pauses the
  step until a person retries, continues, skips or fails it
  ([PLAYBOOK_SPEC.md](PLAYBOOK_SPEC.md) section 8).
- **Notifications**: in-app (bell, desktop notifications) and webhooks (Slack, Discord, ntfy,
  JSON) for steps waiting on a decision and runs finishing.
- **`for_each`**: a command step runs once per item of a list, one card per item, with a
  concurrency limit.
- **Accounts**: profile pictures (upload, image URL or Gravatar), self-service password and
  name changes, and lockout protection (the last admin can't be demoted or disabled, startup
  restores an admin if there's none, and `castor-admin` recovers accounts from the shell).
- **Upgrades**: SQLite is backed up before migrations run; every migration is tested on
  SQLite and Postgres (including an install with hand-made users), and the server test suite
  runs against Postgres with `CASTOR_TEST_DATABASE_URL`.

## Carried forward

The AI resolver for paused steps moved to [PHASE3.md](PHASE3.md) (3e), with the rest of
what's next.

## Decisions (2026-10-15)

1. Build order: 2a → 2b → 2c → 2d; 2e and 2f later.
2. Playbook runs appear as one card per step, linked by dependencies.
3. Command definitions live in both places: built-ins as YAML in `commands/`, user
   definitions in the database (edited in the UI as YAML).
4. Playbooks ship with a step-list editor first; the graph canvas follows.
