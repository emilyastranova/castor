# Castor architecture

Castor is a Kanban-style task manager and workflow orchestrator for automation
pipelines. Cards on a board are either **manual tasks** (a person does them) or
**command tasks** (an agent runs them). Dragging a command card into the project's
*active* column dispatches it to an agent, which runs it in a container and streams
the output back into the card.

## Components

| Component | Path | Stack | Role |
| --- | --- | --- | --- |
| Web UI | `web/` | SvelteKit, Svelte 5, Tailwind v4, custom Material 3 system | Boards, cards, agents, live output |
| API + orchestrator | `server/` | FastAPI, SQLAlchemy 2 (async), PostgreSQL | REST API, UI WebSocket, agent gateway, dispatcher |
| Agent | `agent/` | Python 3.10+, `websockets` | Connects out to the server, runs jobs in podman/docker containers (or on the host), streams logs |
| Command library | `commands/` | YAML | Reusable command definitions with typed arguments (milestone 2 wires these into playbooks) |
| Reverse proxy | `compose.yaml` (Caddy) | | Single origin: `/api` and `/ws` go to the server, everything else to the web app |

All services run under `podman compose` / `docker compose` from `compose.yaml`.
Agents can also run outside compose on any host that can reach the server.

## Data model (milestone 1)

```
Client 1──* Project 1──* BoardColumn 1──* Task 1──* Job 1──* JobLogChunk
                                                         └──* JobOutput
                        │                  ├──* Comment
                        ├──* Attachment ◄──┘ (optional task_id / job_id)
                        └──* ActivityEvent
Agent 1──* Job
User   (single admin in M1; multi-user in M2)
```

* **BoardColumn.role** is one of `backlog`, `todo`, `active`, `review`, `done` or null.
  Moving a command task into an `active` column enqueues a Job (if `run_on_move`).
  On success, if the project setting `auto_advance` is on and a `review` column exists,
  the task is moved there.
* **Task.kind** is `manual` or `command`. Command tasks carry `command`, optional
  `image` (container image), and an `agent_selector` (`{agent_id}` or `{tags: [...]}`).
* **Job.status** flows `queued → dispatched → running → succeeded | failed | cancelled | lost`.
  `lost` means the agent disconnected mid-run.
* Logs are stored as ordered `JobLogChunk` rows (`stdout`/`stderr`/`system`) so the UI can
  stream and replay them; `Job.stdout_bytes` / `stderr_bytes` are totals.
* `custom_fields` on Task, `settings` on Project, `capabilities` on Agent are JSON.
* `VariableKind` / `VariableSet` hold typed reusable values; `Task.var_bindings` and
  `Job.var_bindings` map command slots to sets (see Variables below).

IDs are UUIDs. Timestamps are UTC.

## Agent protocol (`/ws/agent`)

JSON text frames. Agent authenticates with the shared `CASTOR_AGENT_SECRET` (per-agent
tokens are milestone 2).

Agent → server

| type | fields |
| --- | --- |
| `hello` | `secret, name, hostname, os, username, version, capabilities{runtimes[], default_image}, tags[], max_concurrency` |
| `heartbeat` | `running[]` (job ids) |
| `job.started` | `job_id, started_at` |
| `job.log` | `job_id, seq, stream ("stdout"\|"stderr"\|"system"), data` |
| `job.finished` | `job_id, exit_code, finished_at, error?` |

Server → agent

| type | fields |
| --- | --- |
| `hello.ok` | `agent_id, heartbeat_interval` |
| `hello.error` | `reason` |
| `job.run` | `job{id, command, display_command?, image?, runtime ("container"\|"host"), env{}, files[], timeout_s, artifacts{token, collect[]}}` |
| `job.cancel` | `job_id` |

### Job directories and artifacts

Each job runs in its own directory on the agent (`<workdir>/<job_id>/`, a temp dir by
default), mounted at `/work` for container jobs and used as the working directory for host
jobs (`$CASTOR_WORK`). `job.run` carries a one-off upload token and the paths to collect: the
command definition's output `source` paths plus `/work/artifacts/**`. After the process exits
and before `job.finished`, the agent uploads matching files to
`POST /api/jobs/{id}/artifacts` (multipart `file` + `path`, header `X-Castor-Job-Token`).
The server stores only the token's SHA-256, accepts uploads until 10 minutes after the job
finishes, caps size and count (`CASTOR_ARTIFACT_*`), and saves each file as an Attachment
with `kind = artifact` and `job_id` set.

### Parsed outputs

At dispatch the server snapshots the definition's parsed outputs onto `Job.output_specs`.
After `job.finished` (not for cancelled jobs) `outputs.py` runs each output's parser in a
background task: the artifact files matching its `source` (or stdout when there is no
source) go through the parser in `parsers.py`, and the result is stored as one `JobOutput`
row (`name, type, parser, sources[], value JSON, error`), then published as `job.outputs`.
A parse failure is stored as `error` on that output and never changes the job's status.
`GET /api/jobs/{id}/outputs` lists them; `POST /api/jobs/{id}/outputs/parse` re-runs the
parsers (for jobs from before Phase 2b it falls back to the card's current definition).

Output types are a fixed set so playbook steps can be type-checked: `string`, `number`,
`boolean`, `list<string>`, `host`/`list<host>` (`{ip, hostnames[], status}`),
`service`/`list<service>` (`{host, port, proto, state, name, product, version}`),
`url`/`list<url>` (`{url, status, length, words, lines}`). Parsers: `nmap.hosts`,
`nmap.services`, `ffuf.results`, `lines`, `regex`, `text`. XML goes through `defusedxml`,
since tool output can carry external untrusted content.

Each parser has an options model (pydantic, `extra="forbid"`); an output's `options` are
validated when the definition is saved. User-supplied regular expressions run with the
`regex` module and a 2 s timeout so a pathological pattern can't wedge a worker thread.
`GET /api/parsers` lists parsers with their option fields and a built-in sample
(`castor_server/parser_samples/`); `POST /api/parsers/{name}/test` (`{input, options}`,
input capped at 2 MB) returns `{value, error, option_errors}` for the Parsers page.

## Command library

Definitions (`commands/*.yaml` built-ins, plus user definitions created as YAML in the UI)
live in `command_definitions`. A card can point at one (`command_ref` + `command_args`); the
server renders the command line onto `task.command`, so dispatch is unchanged. Rendering
shell-quotes every argument value (`command_render.py`), so a value is always exactly one
shell word (or one word per list item). While a card is attached, its raw command line is
read-only through the API; detaching (`command_ref: null`) keeps the rendered line as a plain
shell command.

## Variables

Reusable typed values that commands consume: credentials, SSH keys, listeners, tokens, or
anything else (`variables.py`, `api/variables.py`, the Variables page).

* A **kind** (`variable_kinds`) is a template of typed fields. Field types: `text`, `number`,
  `boolean`, `host`, `port`, `list`, `secret`, `file`, `secret_file`. Built-in kinds
  (`credential`, `ssh-key`, `listener`, `api-token`) are synced from code at startup and are
  read-only; users add their own.
* A **set** (`variable_sets`) is one filled-in instance of a kind. Plain values are JSON;
  `secret` values and the contents of every file are encrypted with Fernet (`vault.py`) using
  `CASTOR_VAULT_KEY`, or a key generated once into `<data_dir>/vault.key`. Back that key up
  with the database: without it, stored secrets can't be read. Secrets are never returned
  by the list/get endpoints; `POST /api/variables/sets/{id}/reveal/{field}` returns one and
  records a `variable.revealed` activity event. A set can be limited to some projects
  (`project_ids`; empty = all).
* **One-off sets** (`ephemeral`): values typed with "Enter manually…" on a card or in the
  Run dialog, without saving them to the library. They are hidden from listings, owned by
  the first card (`task_id`) or playbook run (`playbook_run_id`) that binds them, usable
  only there, and deleted with it (FK cascade) or when the card picks another set.
  Unclaimed ones are dropped after a day. `PATCH` with `ephemeral: false` and a name
  promotes one into the library.
* A command declares **slots** (`vars: [{name: creds, kind: credential}]`) and uses
  `{{ creds.username }}` in its template. That renders as `"${CASTOR_VAR_CREDS_USERNAME}"`:
  no value, secret or not, is ever part of `task.command` or `job.command`. A card binds slots
  to sets in `task.var_bindings`; the job snapshots the bindings (`job.var_bindings`) when
  queued.
* **Dispatch** resolves the bindings (`variables.resolve`) just before `job.run`: plain and
  secret fields go in `env`, file fields in `files[] {env, path, data (base64), mode}`. The
  values exist only in that message. If a set is missing, of the wrong kind, not allowed in
  the project or incomplete, the job fails with `error: "variables: …"` instead of being sent.
* The **agent** writes `files` under `<job dir>/.castor/vars/` (mode 0600), sets each file
  variable to its path (`/work/...` in a container, absolute on the host), passes variables
  to containers by name only (`-e NAME`, value from the runtime's environment) so they
  never show in the process list, deletes `.castor/` when the job ends, and never uploads
  it as an artifact.
* `display_command` is `command` as the job will run it, for the `$ …` first line of the
  output (the agent prints it instead of the raw command; `job.command` keeps the
  references). Values are single-quoted where they form a word (`-u 'admin'`), secrets show
  as `'••••'`, files as the path the job sees, and `${X:+word}` / `${X:-word}` are expanded
  the way the shell will (an unset optional field's `-d …` disappears).
* The gateway masks each secret value (4+ characters) as `••••` in `job.log` chunks and the
  job's `error` before storing or broadcasting them. Plain field values from the same job
  (username, domain, host…) are protected first, so they stay readable even when they
  contain or equal a secret. A secret split across two log chunks is not caught.
* Playbooks take sets through `varset` inputs and bind them per step with `vars:` (see
  [PLAYBOOK_SPEC.md](PLAYBOOK_SPEC.md)).

## Playbooks

The authoring format is specified in [PLAYBOOK_SPEC.md](PLAYBOOK_SPEC.md) (self-contained,
meant to be handed to whoever or whatever writes playbooks). A playbook (`playbooks/*.yaml` built-ins, user definitions edited as YAML) is a DAG of
steps (`playbook_spec.py`). A **command step** names a library command and binds its args
to literals, `{{ inputs.x }}`, or `{{ steps.<id>.outputs.<name> }}`; each one becomes a
card when the run starts. A **transform step** runs no command and has no card: it takes
`transform.input` (one or more step-output references, concatenated), applies a fixed list
of ops (`filter`, `pluck`, `format`, `unique`, `sort`, `limit`; `playbook_transforms.py`,
data only, regexes with a timeout) and publishes `transform.output.name`. Every reference
in args or input is added to `depends_on` when the spec is parsed, so the graph always
matches the bindings. Unknown keys are errors (except `x-...`), and saving also checks the
spec against the command library (`playbook_library.check_against_library`: commands,
argument names and values, referenced outputs); `POST /api/playbooks/validate` runs both
checks without saving. `skip_if_empty: true` skips a step when a value it takes from
upstream is empty.

`services._advance` drives a run: whenever one of its jobs finishes (after its outputs are
parsed) it runs every step whose dependencies are satisfied (succeeded or skipped),
repeating until nothing changes, since a transform finishing can make the next step ready
at once. Transform results, skips and steps that could not start are stored in
`playbook_runs.step_results`; command steps otherwise take their state from their card's
latest job. `step_statuses` derives one status per step (`pending, queued, running,
succeeded, failed, cancelled, skipped, blocked`; blocked = an upstream step failed) and
`PlaybookRunOut.step_status` carries it to the UI. The run succeeds when every step is
succeeded or skipped, and fails when nothing can move any more. Re-running a failed card
reopens a failed run. Events: `playbook_run.updated` on every change, plus
`task.created|updated` for the step cards.

`playbooks/pipeline-demo.yaml` and the `commands/demo-*` commands are a fake recon
pipeline (fan-out, three branches, three transforms, fan-in) that runs in seconds on an
agent with `--allow-host` and touches no network; its `fail_at` / `empty_at` inputs
exercise failure and skip handling. `server/tests/test_playbook_transforms.py` runs it
end to end with each command executed under `/bin/sh`.

## Dispatcher

The dispatcher lives in the server process. `pump()` runs whenever a job is enqueued, an
agent connects, a job finishes, or on a 5 s timer. For each queued job (FIFO) it picks the
first online agent that: matches the task's selector, has spare concurrency, and supports
the required runtime (a job with an `image` needs a container runtime). It sends `job.run`
and marks the job `dispatched`. Jobs with no eligible agent stay `queued`.

## UI realtime (`/ws/ui`)

Authenticated by the session cookie. The server broadcasts every domain event as
`{"type":"event","name":"task.moved","project_id":"…","data":{…}}`. Event names:
`project.updated`, `column.*`, `task.created|updated|moved|deleted`, `job.updated`,
`job.log`, `job.outputs`, `comment.created`, `attachment.created`, `agent.updated`,
`playbook_run.updated`.
The client keeps page state in sync by applying these to its stores.

## Auth

Single local admin for M1. `POST /api/auth/login` sets an httpOnly `castor_session`
cookie (JWT). The same token is accepted as `Authorization: Bearer` for scripts.
The first admin is created from `CASTOR_ADMIN_USERNAME` / `CASTOR_ADMIN_PASSWORD` on
first start.

## Roadmap

* **M1** — this document. Board, cards, agents, container jobs, streamed output,
  comments, attachments/screenshots, custom fields, activity feed.
* **M2** (in progress, see [PHASE2.md](PHASE2.md)) — command library + playbooks as connected blocks with typed inputs/outputs,
  parsers (nmap XML → hosts/ports, etc.), per-agent tokens, multi-user + roles.
* **M3** — reporting, run summaries, artifact export, agent auto-scaling.
