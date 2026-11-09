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
| SSH hosts | `server/castor_server/orchestrator/ssh.py` | asyncssh | The other way around: the server connects to a host over SSH and runs jobs there as an agent |
| Libraries | `commands/`, `tools/`, `playbooks/` | YAML | Built-in commands (typed arguments), tool environments and playbooks, synced into the database at startup |
| Reverse proxy | `compose.yaml` (Caddy) | | Single origin: `/api` and `/ws` go to the server, everything else to the web app |

All services run under `podman compose` / `docker compose` from `compose.yaml`.
Agents can also run outside compose on any host that can reach the server.

## Data model (core)

The core tables; later features (playbooks, variables, assets, alerts, AI, API tokens) add
their own, described in their sections below.

```
Client 1──* Project 1──* BoardColumn 1──* Task 1──* Job 1──* JobLogChunk
                                                         └──* JobOutput
                        │                  ├──* Comment
                        ├──* Attachment ◄──┘ (optional task_id / job_id)
                        └──* ActivityEvent
Agent 1──* Job
      1──* AgentToken (an unused token has no agent yet)
      1──1 SshHost (only for a host the server connects to over SSH)
User *──* Project (project_members; admins see every project)
```

* **BoardColumn.role** is one of `backlog`, `todo`, `active`, `review`, `done` or null.
  Moving a command task into an `active` column enqueues a Job (if `run_on_move`).
  On success, if the project setting `auto_advance` is on and a `review` column exists,
  the task is moved there.
* **Task.kind** is `manual` or `command`. Command tasks carry `command`, optional
  `image` (container image), and an `agent_selector`: `{}` (any agent), `{agent_id}`,
  `{tags: [...]}` (any one agent with all of them) or `{tags, all: true}` (every connected
  agent with them; see Dispatcher), normalised by `services.check_selector`.
* **Job.status** flows `queued → dispatched → running → succeeded | failed | cancelled | lost`.
  `lost` means the agent disconnected mid-run.
* Logs are stored as ordered `JobLogChunk` rows (`stdout`/`stderr`/`system`) so the UI can
  stream and replay them; `Job.stdout_bytes` / `stderr_bytes` are totals.
* `custom_fields` on Task, `settings` on Project, `capabilities` on Agent are JSON.
* `VariableKind` / `VariableSet` hold typed reusable values; `Task.var_bindings` and
  `Job.var_bindings` map command slots to sets (see Variables below).

IDs are UUIDs. Timestamps are UTC.

## Agent protocol (`/ws/agent`)

JSON text frames. The agent authenticates in `hello` with its own **token** (preferred) or
the shared `CASTOR_AGENT_SECRET` (`agent_tokens.authenticate`):

* Tokens (`castor_` + 32 random bytes) are created on the Agents page (`POST
  /api/agent-tokens`) and shown once; `agent_tokens` stores only their SHA-256 and a short
  prefix. The first agent to connect with an unused token is bound to it (and gets the
  token's tags). From then on the token decides which agent this is: a different `--name`
  renames that agent.
* Rotating (`POST /api/agent-tokens/{id}/rotate`) replaces the token and revoking (`…/revoke`)
  disables it; either way the connected agent gets `auth.revoked` and the socket closes
  with 4401. A refused agent exits instead of retrying.
* The shared secret works while `CASTOR_ALLOW_SHARED_AGENT_SECRET` is true (the default),
  but never as an agent that has, or had, a token: that would let anyone with the secret
  impersonate it, or undo a revoke. Forgetting the agent deletes its tokens.
* `agent_tokens.changed` is published whenever tokens change (including an enrollment).

Agent → server

| type | fields |
| --- | --- |
| `hello` | `token` or `secret`, `name, hostname, os, username, version, capabilities{runtimes[], default_image, host_install, tools[], tty}, tags[], max_concurrency` |
| `heartbeat` | `running[]` (job ids), `tools[]` (cached tool image tags) |
| `tool.prepared` | `tool_id, ref, ok, error?, tools[]` (answer to `tool.prepare`) |
| `job.started` | `job_id, started_at` |
| `job.log` | `job_id, seq, stream ("stdout"\|"stderr"\|"system"), data` |
| `job.finished` | `job_id, exit_code, finished_at, error?` |
| `job.prompt` | `job_id, text` (an interactive job is waiting at this prompt; `null`: no longer) |

Server → agent

| type | fields |
| --- | --- |
| `hello.ok` | `agent_id, heartbeat_interval` |
| `hello.error` | `reason` (then close 4401; the agent exits) |
| `auth.revoked` | `reason` (token rotated or revoked; then close 4401) |
| `job.run` | `job{id, command, display_command?, image?, runtime ("container"\|"host"), env{}, files[], timeout_s, tty, tty_size?{cols, rows}, artifacts{token, collect[]}, tool?}` |
| `job.cancel` | `job_id` |
| `job.stdin` | `job_id, data` (keystrokes for an interactive job's terminal) |
| `job.resize` | `job_id, cols, rows` |
| `job.signal` | `job_id, name ("INT"\|"TERM"\|"QUIT")` (INT is sent as Ctrl+C through the terminal) |
| `tool.prepare` | `tool` (same shape as `job.tool`; build or pull it now) |

Agents send a `heartbeat` every `heartbeat_interval` seconds (`CASTOR_AGENT_HEARTBEAT_INTERVAL`,
15). An agent the server hasn't heard from for `CASTOR_AGENT_OFFLINE_AFTER` seconds (45) is
treated as disconnected (`gateway.sweep_stale`, run by the dispatcher before each pump): marked
offline, its in-flight jobs lost, and its socket closed with 4408 so a live agent reconnects.
Keep the timeout a few heartbeats long.

### Job directories and artifacts

Each job runs in its own directory on the agent (`<workdir>/<job_id>/`, a temp dir by
default), mounted at `/work` for container jobs and used as the working directory for host
jobs. Either way `$CASTOR_WORK` is that directory: the agent (or SSH host) sets it for host
jobs, and the dispatcher puts `CASTOR_WORK=/work` in a container job's env (so agents that predate
it pass it in too). Command templates say `{{ work }}`, which `command_render` turns into
`"${CASTOR_WORK}"`; `work` is reserved as an argument name. Output `source:` paths may start with
`{{ work }}/` (quoted in YAML) or `/work/`: `command_spec.work_path` stores both as `/work/...`, the
job-relative form the collectors and `outputs` match against. `job.run` carries a one-off upload token and the paths to collect: the
command definition's output `source` paths plus `/work/artifacts/**`. After the process exits
and before `job.finished`, the agent uploads matching files to
`POST /api/jobs/{id}/artifacts` (multipart `file` + `path`, header `X-Castor-Job-Token`).
The server stores only the token's SHA-256, accepts uploads until 10 minutes after the job
finishes, caps size and count (`CASTOR_ARTIFACT_*`), and saves each file as an Attachment
with `kind = artifact` and `job_id` set.

### SSH hosts

An admin can add a host Castor connects to (Agents → Add SSH host; `api/ssh_hosts.py`, admin
only) instead of running an agent on it. `ssh_hosts` keeps how to connect (host, port,
username, work directory, container network, whether host commands and host installs are
allowed) and the private key as a vault token; the host's name, tags and slots are on its
`agents` row like any agent's. Castor generates an ed25519 key (the UI shows the
`authorized_keys` line) or takes a pasted key, decrypting it once if it has a passphrase.

`orchestrator/ssh.py` runs one `HostLink` per host: it keeps one SSH connection (asyncssh,
keepalives, reconnect with backoff), probes the host (work dir, OS, podman or docker), and
registers it with the gateway as an `AgentConnection` whose `ws` is an in-process `SshSocket`.
The gateway sends it the usual `job.run`, `job.cancel`, `tool.prepare` and `job.stdin`,
`job.resize` and `job.signal` messages, and the link answers with `job.started`, `job.log`,
`job.finished`, `job.prompt`, `tool.prepared` and `heartbeat`. So dispatch, log masking, alerts and
lost jobs work as for any agent. `AgentOut.ssh_host_id` marks these agents, and a connecting
agent can't register under an SSH host's name.

On the host each job gets `<workdir>/<job_id>/`. Over SFTP, Castor writes the variable files to
`.castor/` (owner-only), the env vars to `.castor/env` and a `.castor/run.sh`. The script
records its pid (sshd makes each session its own process group, so cancel is
`kill -- -<pid>`), sources and deletes the env file (values never appear on a command line),
then execs the command in `/bin/sh`, or in `podman|docker run` with the job dir at `/work`.
Every command is sent as `/bin/sh -c '<quoted>'`, whatever the account's login shell. An
interactive job gets a pty. Afterwards `.castor/` is removed, and the declared outputs and
`artifacts/**` are fetched over SFTP into attachments (`artifacts.store`, which the upload
endpoint uses too), skipping symlinks and anything resolving outside the job dir.

Hosts can be reached through jump hosts. One instance-wide SSH config, in `ssh_config` format
(app setting `ssh_config`, `GET|PUT /api/ssh-config`, `POST /api/ssh-config/check`, admin
only), is parsed by Castor itself (`ssh_config.py`), never by asyncssh or OpenSSH. Only
`Host`, `HostName` (`%h`), `Port`, `User`, `ProxyJump`, `ConnectTimeout` and `ServerAlive*`
are used. `ProxyCommand`, `Match` and `Include` are refused, since they would run commands or
read files on the server, and other keywords are ignored with a warning. OpenSSH's rules
apply: the first value wins, a host's own port and username fields beat the config, and in
`ProxyJump a,b` each hop is tunnelled through the one before, with every jump host getting its
own config. `ssh_config.route` gives the hops (`SshHostOut.route`). `connect` opens them in
turn, each with the host's own key, and closes them together. Saving the config reconnects
every SSH host.

Host keys are trusted on first use: the first key seen is stored in `host_key`, and each jump
host's in `jump_host_keys` (by `user@host:port`). If the host (or a
jump host) later presents another key (captured from that same connection in
`host_key_pending` or `jump_host_keys_pending`), the link stops with state `host_key_changed` until an admin accepts it
(`POST /api/ssh-hosts/{id}/accept-host-key`). Link state (`connecting`, `connected`, `error`,
`host_key_changed`, `disabled`) and the last error are in `GET /api/ssh-hosts`, and changes
publish `ssh_hosts.changed`. Each job uses one SSH channel, plus short-lived ones for SFTP
and cancel, so keep a host's slots below sshd's `MaxSessions` (10).

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
`url`/`list<url>` (`{url, status, length, words, lines, title, content_type, tech[]}`),
`smb_host`/`list<smb_host>` (`{host, port, name, domain, os, signing, smbv1}`), and
`object`/`list<object>`: whatever fields the parser gives (a table of log entries, users…),
shown as a table with a column per field. Built-in
parsers: `nmap.hosts`, `nmap.services`, `ffuf.results`, `httpx.results`, `netexec.smb`,
`lines`, `regex`, `text`. XML goes through `defusedxml`, since tool output can carry external
untrusted content.

#### Parsers in the library

People and the AI add parsers as **declarative definitions**, or as JavaScript in a sandbox
(`parser_spec.py`, table `parser_definitions`, YAML like commands and tools): a `type`, a
`format` (`lines`, `regex`, `json`, `jsonl`, `csv`, `xml`) with `pattern` / `records` /
`delimiter`, and `fields` mapping each field of the type's items to where it comes from
(`values` sets constants). For `object` types any field names go, a regex's named groups or a
CSV's header become the fields when `fields` is left out, and `types` turns a field's text into
`int`, `float`, `bool` or `list`. With `format: javascript`, `code` defines `parse(text)` returning
the records; `js_sandbox.py` runs it in a fresh V8 isolate (mini-racer) with no file system,
network, `require`, `process` or timers, a 3 s limit and 256 MB, and the result comes back as
JSON then goes through the same field typing. Code parsers are JavaScript because Python can't
be sandboxed in-process: a parser could read the vault key and the database. The declarative
formats run on the same guarded pieces as the built-ins. All parsers carry a
`sample`, and are registered next to them (`parsers.USER`, filled at startup and on every
change by `parser_library.py`) so commands name them like built-ins; a parser a command uses
can't be deleted or change type. Their results become assets by type (`assets.mapping_for`).
`POST /api/parsers` / `PUT` / `DELETE /api/parsers/{id}`, `POST /api/parsers/validate`
(`{yaml, input?}`: check and run without saving) and `POST /api/parsers/draft` (the AI writes
one for a sample, runs it, and repairs it from the problems).

**Output that doesn't match its parser.** `outputs.is_mismatch` tells "the input was there
but the parser couldn't read it" from "there was nothing to read" (no file, empty output).
For such an output a `ParserProposal` (table `parser_proposals`, per job and output) holds a
parser the AI drafted for that job's real input, checked on it; `job.parser_proposal` events
report it. `POST /api/jobs/{id}/outputs/{name}/parser-proposal` asks for one (in the
background), `POST /api/parser-proposals/{id}/apply` (`{save_as?, use_in_command?}`) re-reads
the job with it (carried inline on the job's `output_specs` as `parser_spec`, or saved to the
library under `save_as`, a person's choice; `use_in_command` also points the card's command at
it when the command is the user's, editing its YAML in place, `parser_library.point_output_at`),
`…/dismiss` puts it aside, and
`GET /api/jobs/{id}/outputs/{name}/input` returns what the parser read. In a playbook, a
step with `on_failure: ask|ai` whose job succeeded but whose output the playbook uses couldn't
be read pauses (`step_results[step].parse`), and a draft is requested at once. With `ai` and
the step's `ai.draft_parsers` (default on), `ai.auto_parser` applies it for the run and
resolves the step `continue`; with `ask` it waits on the step, one click away. Nothing is
added to the library without a person. Parsers travel in exports (`library/parsers/*.yaml`,
before commands, and an archive's parsers are known while its commands are checked); the
Library page's export carries the parsers your commands name. MCP tools: `list_parsers`,
`get_output_input`, `check_parser`, `create_parser`, `list_parser_drafts`, `use_parser_draft`.

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
shell command. An argument with `flag` renders as `<flag> <value>` (a boolean as just the
flag) and as nothing when empty; `show_if` names a boolean argument, and while that is off
the argument is hidden in the form and ignored when rendering.

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
* Kind fields can have `choices` (text: one of; list: any of). A command argument can take
  its dropdown from a set with `choices_from: {set, field}`; `command_library.load_spec`
  (and the commands API) fill those options into `choices` on every load, so validation
  and the forms always use the set's current values.
* **Target lists** (`target-list` kind, or any set with a list field) can fill a text or
  list argument: the card stores `{"list": "<set id or name>"}`, and
  `command_library.apply_library` expands it (list args get the items, text args a comma
  join) whenever the command is rendered; `enqueue_job` re-renders cards with list
  references so each run uses the lists' current entries.
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
(hidden) card when the run starts. The run itself gets **one card on the board**
(`playbook_runs.card_id`; `TaskOut.run` carries its summary): created in In Progress, moved
to Review once when the run ends (unless someone moved it), and opened on a **Run** tab
(`RunPanel.svelte`: graph, decisions, steps). Step cards (`playbook_step_id` set) never show
on the board; one opens on top of its run card (`?task=<card>&step=<step card>`), and a link
straight to a step card redirects there. Runs from before run cards get one at startup
(`services.ensure_run_cards`). A **transform step** runs no command and has no card: it takes
`transform.input` (one or more step-output references, concatenated), applies a fixed list
of ops (`filter`, `pluck`, `format`, `unique`, `sort`, `limit`; `playbook_transforms.py`,
data only, regexes with a timeout) and publishes `transform.output.name`. Every reference
in args or input is added to `depends_on` when the spec is parsed, so the graph always
matches the bindings. Unknown keys are errors (except `x-...`), and saving also checks the
spec against the command library (`playbook_library.check_against_library`: commands,
argument names and values, referenced outputs); `POST /api/playbooks/validate` runs both
checks without saving. `skip_if_empty: true` skips a step when a value it takes from
upstream is empty.

**Branching** (`playbook_conditions.py`). `when:` is a condition tree (`all`/`any`/`not`,
value tests on inputs or upstream outputs, status tests `{step, status}`); a false
condition makes the step `not_taken`, and steps that need a not-taken step are not taken
either (unless `join: any|always`). Steps a condition references are added to
`depends_on`; a status test waits for its step to end however it ends, and a failure some
condition watches for counts as *handled*: its normal dependents are not taken instead of
blocked, and it doesn't fail the run. `expect:` checks a finished command's parsed
outputs and log; a miss counts as a failure. `on_failure: stop|continue|ask` decides what
a failure means; `ask` pauses the step (`attention`) and notifies people.

Advancing a run (`advance_playbook_run`, `resolve_step`) is serialised per run: jobs that
finish together have their outputs parsed in parallel, and without it each would queue the
same ready step. A lock per run and event loop covers one process, and the run row is read
`FOR UPDATE` (Postgres) for several; the engine re-reads jobs and cards rather than using a
session's cached copies, and never queues a card that already has an active job.

`services._advance` drives a run whenever one of its jobs finishes (after its outputs are
parsed; a job whose outputs are still being parsed counts as running), repeating until
nothing changes: judge each newly finished job once (`expect`, `ask`), record steps left
out by a branch, then start every ready step (`readiness`: join rule over its
dependencies) whose `when` holds. Transform results, skips, condition evaluations,
expectations and decisions are stored in `playbook_runs.step_results`. A command step takes
its state from its card's latest job unless the stored result names that job (`job_id`):
a judgement or decision about it. `step_statuses` derives one status per step (`pending,
queued, running, succeeded, failed, cancelled, skipped, not_taken, attention, blocked`) and
`PlaybookRunOut.step_status` carries it to the UI; `run_outcome` settles the run (running
while anything can move or waits for a person). Re-running a card reopens a finished run.

A `for_each` step has no card at the start; when it becomes ready `_advance` creates one
card per item (`tasks.playbook_item` = its position), renders each with `{{ item }}`, and
queues them up to `for_each.concurrency`. `FanOut` sums the items' latest jobs into one
status and a synthetic job id (so a judgement on the set is superseded when an item
re-runs); the step's outputs are its succeeded items' outputs merged in item order.

A paused step is decided with `POST /api/playbook-runs/{id}/steps/{step}/resolve
{action: retry|continue|skip|fail, note}` (`services.resolve_step`, which records who
decided, why and the note in `step_results[step].resolution`). `resolve_step` takes an
`actor` for decisions that aren't a person's; the AI resolver (see AI) decides through it.
Events: `playbook_run.updated` on every change, plus `task.created|updated` for
the step cards.

**Visual editor** (`web/src/routes/playbooks/edit/[id]`). Edits the spec as an author
would write it (`depends_on` only for ordering the references don't imply;
`lib/playbookEditor.ts` adds those back for drawing) and sends it, debounced, to
`POST /api/playbooks/to-yaml`, which renders minimal YAML (`playbook_yaml.authoring_dict`:
no defaults, no derived `kind`, no implied `depends_on`) and validates it. Saving goes
through the normal create/update endpoints with that YAML. Step positions are kept in the
spec's `layout` (`{step_id: {x, y}}`, ignored by the engine), and run graphs use it too.

**Notifications** (`notifications.py`, `api/notifications.py`). `notify()` writes one
`notifications` row per user (own read state; `subject` such as `run:<id>:step:<step>` lets
dealing with something clear it) and publishes `notification.created`; `clear()` marks
them read and publishes `notification.read`. A step pausing (`playbook_step.attention`) and
a run finishing (`playbook_run.succeeded|failed`) notify. `CASTOR_NOTIFY_WEBHOOKS` also
posts each one to webhooks (Slack, Discord and ntfy formats by host, JSON otherwise) in a
background thread; `CASTOR_PUBLIC_URL` makes links absolute. The web UI shows them under
the bell in the navigation rail and, if allowed, as desktop notifications.

The built-in library (`tools/`, `commands/`, `playbooks/`) ships real tooling; a built-in
whose file is removed is retired at the next start (commands and playbooks are deleted, cards
keep their command line, runs their snapshot; a tool is kept as a user tool while a command
uses it). For tests, `server/tests/fixtures/playbooks/pipeline-demo.yaml` and the
`fixtures/commands/demo-*` commands are a fake recon pipeline (fan-out, three branches, three transforms, fan-in) that runs in seconds on an
agent with `--allow-host` and touches no network; its `fail_at` / `empty_at` inputs
exercise failure and skip handling. `fixtures/playbooks/branching-demo.yaml` does the same for
conditions, failure branches, `expect`, `on_failure` and `join: any`
(`server/tests/test_playbook_conditions.py` drives it). `server/tests/test_playbook_transforms.py`
runs `pipeline-demo` end to end with each command executed under `/bin/sh`.

## Tool library

Tools (`tools/*.yaml` built-ins synced from `CASTOR_TOOLS_DIR`, plus user tools as YAML) live
in `tool_definitions`; a command names one with `tool: <id>` instead of `image:`, and the
card and job copy it (`tasks.tool`, `jobs.tool`). A tool provides itself as a published
`image`, a `build` recipe (`base` + `install` + `check`) or a `host` check/install
(`tool_spec.py`). At dispatch `ToolSpec.way_for(agent runtimes)` picks the first way the
agent can use and sends it as `job.tool`: `{id, name, kind: image|build|host, ref?, files?,
check?, install?, install_hint?}`. A recipe's tag is `castor-tools/<id>:<sha256[:12]>` of the
recipe, so any change is a new build; `files` is the build context (a Dockerfile with
`LABEL castor.tool=<id>` that runs the scripts). The agent (`castor_agent/tools.py`) pulls,
builds (one build per tag at a time) or checks the host before running the job, streaming
the output as `system` log lines; host installs run only with `--allow-host-install`.
Agents report cached tags in `hello` and heartbeats; the dispatcher prefers an agent with
the tool ready. `POST /api/tools/{id}/prepare` sends `tool.prepare` ahead of time; state is
published as `tool.status`, library edits as `tools.changed`.

## Assets

A project's inventory of what it found (`assets.py`, `api/assets.py`): hosts, services, URLs,
domains, users, credentials and shares, one record per identity key with merged `fields`,
`asset_observations` for provenance and `asset_links` between them. Parsed outputs from
`nmap.hosts`, `nmap.services`, `netexec.smb` and `ffuf.results` are recorded after each job
(`ingest_outputs`); events `assets.changed` and `asset_triggers.changed`; activity
`assets.found`. Triggers run a command or playbook for new or changed assets: automatically
inside the project's scope (`projects.settings.scope`), otherwise after approval; once per
asset and rate-limited. Command arguments can take `{"assets": {type, where, field}}`.

## AI

`llm.py` (providers: `KINDS`, one adapter per family in `call`, `complete` walks a chain with
fallback and logs `ai_calls`), `ai.py` (resolver: `suggest_resolution`, `apply_suggestion`,
`auto_resolve` for `on_failure: ai`; `generate` with validation and repair; `suggest_regex`;
`explain_job`), `api/ai.py`. Tables `ai_providers` (vault secrets per field) and `ai_calls`;
chains in `app_settings.ai_chains` (`default`, `resolver`). Endpoints: `GET /api/ai/status`,
`GET /api/ai/kinds`, `GET|POST /api/ai/providers`, `PUT|DELETE /api/ai/providers/{id}`,
`POST /api/ai/providers/test`, `POST /api/ai/providers/models`, `GET /api/ai/usage` (admins
for providers and usage); `POST /api/ai/generate` (operators), `POST /api/ai/regex`,
`POST /api/ai/explain/{job_id}`, `POST /api/playbook-runs/{id}/steps/{step}/ai/suggest|apply`.
A paused step's suggestion is `step_results[step].ai`; every decision is appended to
`step_results[step].history` (`by`, `action`, `note`, `reason`, and for the AI `ai`, `model`,
`confidence`, `suggested` when a person applied it, and `command_before`/`command_after`/`change`
when it changed the command, which also writes a `task.ai_edit` activity on the step's card).

## Interactive jobs

A job with `tty` (the card's toggle, or its library command's `tty: true`) runs under a pty on
the agent (`TERM=xterm-256color`, `COLORTERM=truecolor`; containers get `run -t -i`): one raw
output stream, stored as `stdout` chunks and rendered by xterm.js. Other jobs get stdin from
`/dev/null`. `/ws/jobs/{id}/terminal` (operators on the project) takes `{"t": "in", "d"}`,
`{"t": "resize", "cols", "rows"}` and `{"t": "signal", "name"}` and forwards them to the agent;
the first keystroke sets `jobs.input_by` (activity `job.taken_over`; keystrokes themselves are
never stored). Resizes are stored in `jobs.tty_size` and published as `job.resize` so viewers
match the size. The agent's prompt watch (a quiet line that asks for something) sends
`job.prompt`; while nobody has taken over, `interactive.on_prompt` sets `jobs.waiting_input`,
notifies the job's starter, the card's creator, assignees and Notify-me people, emits the
`input` alert event, and lists it in Needs attention (`kind: input`). Text consumers (AI log
tail, `expect.log`, `/api/jobs/{id}/output`) strip terminal escapes (`interactive.plain`);
`?raw=1` returns the exact bytes.

## Alerts

`alerts.py`, `api/alerts.py`. Tables `alert_channels` (kind, name, `fields`, vault-encrypted
`secrets` per field, `events` for everything, `long_minutes`) and `alert_deliveries` (one per
send: ok, HTTP status, error, `subject` for de-duplicating long-run alerts); `tasks.alerts`
holds a card's (or run card's) own channels and rules, and `people` told in-app
(`task.finished` notifications) when it finishes, including a manual card moved to Done
(`card.done`). Events: `job.failed|succeeded` (from
the gateway, not for playbook steps), `run.failed|succeeded` and `decision` (from
`services`), `approval` (asset triggers), `long` (a 60 s sweep). Endpoints:
`GET /api/alert-presets` (kinds with their form fields and request template, events,
variables), `GET|POST /api/alert-channels`, `PUT|DELETE /api/alert-channels/{id}`,
`POST /api/alert-channels/{id}/test`, `POST /api/alert-channels/test` (unsaved),
`GET /api/alert-channels/{id}/deliveries` (admins); `GET /api/alert-channels/brief`
(names, anyone). `PATCH /api/tasks/{id}` and the playbook run endpoints take `alerts`.
Events `alert_channels.changed`.

## Theme colour

Each user's Material You seed is `users.theme_seed` (`""` = the built-in palette from
`tokens.css`), changed through `PATCH /api/auth/me`. The browser generates every colour role
from it (`m3/theme.ts`, `material-color-utilities`) and caches both palettes for `app.html`
to apply before first paint.

## Instance settings

`app_settings` (key → JSON) holds settings admins change in Settings → General;
`app_settings.AppSettings` declares each key with its default and limits (missing or
out-of-range rows fall back to the default). `GET /api/settings` (anyone signed in),
`PATCH /api/settings` (admins; publishes `settings.changed`, activity `settings.updated`).
Keys: `notes_per_project` (default 4, 1–12, enforced when someone adds a note) and
`ai_chains` (see AI).

## Duplicating cards

`POST /api/tasks/{id}/duplicate` (`services.duplicate_task`) copies a card to the top of the
project's To Do column with new ids: always the blueprint, plus comments, uploads
(`attachments`) and finished runs with logs, outputs and artifacts (`output`) when asked;
links to copied files in the description and comments are rewritten, one-off variable sets
are cloned. Playbook run and step cards are refused (409). Activity: `task.created` on the
copy, `task.duplicated` on the source.

## Import and export

`transfer.py`, `api/transfer.py`. `POST /api/export` (JSON: library ids, variable set ids,
project ids or `everything`, plus `jobs`, `activity`, `passphrase`) streams a `.castor.zip`:
`manifest.json` (`format`, version, creator, contents, `secrets.mode` none|passphrase with
salt and check), `library/{commands,tools,playbooks}/<id>.yaml`,
`library/variable_kinds.json`, `variable_sets.json`, `projects/<id>.json` (every row of the
project: columns, runs, cards, comments, attachments, notes, one-off sets, assets and
triggers; jobs, logs, outputs and activity optionally), `files/<attachment id>`,
`users.json` (username and name; role and pictures under `avatars/` for everything; never
password hashes or tokens), `clients.json`, `set_names.json`. `POST /api/import/preview`
(multipart file, or `token` + `passphrase`) stages the upload and returns the plan: items
with state new/exists/builtin/same and choices import/skip/replace/copy, people matched by
username, warnings. `POST /api/import` applies the choices in one transaction with new ids
everywhere; `GET /api/transfer/history` lists the activity. Format versions newer than the
server's are refused. Exporting everything needs an admin, secrets an operator, a project
read access; importing needs an operator.

## People

`GET /api/people` lists everyone active with their open assigned cards and last activity;
`GET /api/people/{username}` adds their projects, 30-day numbers, assigned cards and the
comments mentioning them. Both count only what's in projects the viewer can see; the web
pages are `/people` and `/people/<username>` (the timeline is the dashboard feed with
`actor=<id>`).

## Search

`GET /api/search?q=` (`search.py`) searches everything the user can see: cards (title,
description, command, tags, custom fields), comments, parsed outputs, console logs (one hit
per run, with the matching line), attachment names, playbook runs and their inputs, pinned
notes, projects and clients, and the playbook/command/tool/parser/variable libraries, people
and agents. Every word must appear in some field (case-insensitive, over the live tables);
`in:CODE`, `type:`, `by:`, `tag:`, `is:mine` and `status:` filter. Results come back grouped,
each with a snippet and a link into the UI. Variable-set secrets are never searched.

## MCP server

`POST /api/mcp` (`mcp.py`, `api/mcp.py`) speaks the Model Context Protocol over stateless
Streamable HTTP (JSON replies; `initialize`, `ping`, `tools/list`, `tools/call`). It needs a
bearer token (no cookies). Every tool is one REST endpoint called in-process with the
caller's Authorization header, so scopes, roles and project limits apply unchanged, and
`tools/list` hides tools the token's scopes don't grant. Results with job output are prefixed
as untrusted data. Resources (`resources/list`, `resources/templates/list`, `resources/read`;
`castor://projects/{id}/board`, `cards/{id}`, `jobs/{id}/output`, `runs/{id}`) are GETs the same
way. The in-process calls carry `X-Castor-Via: <api_tokens.MCP_KEY>`, so the activity they
record has `payload.via = "mcp"`. `castor-mcp` (agent package) bridges stdio clients to it.
Tools that remove, revoke or rotate something, or trust a changed host key, set
`destructiveHint` (`Tool.destructive`); a 204 answers "Done.". Admin tools cover agents, agent
tokens (the result of creating one has a ready `castor-agent` command, from
`CASTOR_PUBLIC_URL`), SSH hosts (`PATCH /api/ssh-hosts/{id}` changes some fields) and the SSH
config.
Most tools are declared as `_auto(name, method, path, description)`. Their input schema is built
at `tools/list` from the endpoint's own OpenAPI: path and query parameters plus the JSON
body's fields, `$ref`s inlined, `body_key` for a non-object body. So they follow the API as it
changes, and a `properties` entry only adds or overrides a description. Between them the tools
cover what a person can do in the UI, except revealing secrets, user accounts, AI providers
and alert channels (keys and webhook secrets), and import/export and file downloads.

## Dispatcher

The dispatcher lives in the server process. `pump()` runs whenever a job is enqueued, an
agent connects, a job finishes, or on a 5 s timer. For each queued job (FIFO) it picks the
first online agent that: matches the task's selector, has spare concurrency, and supports
the required runtime (a job with an `image` needs a container runtime; a job with a `tool`
needs an agent that has a way to provide it; an interactive job needs an agent that
advertises `tty`). It sends `job.run`
and marks the job `dispatched`. Jobs with no eligible agent stay `queued`.

**Every agent with some tags.** A card with `agent_selector: {tags, all: true}` gets, from
`enqueue_job`, one job per connected agent that has the tags and can run it
(`broadcast_targets`, using `dispatcher.agent_can_run`). Its jobs share a `batch_id`, and each
has a `target_agent_id` the dispatcher holds it to. With one such agent (or none connected) it
is an ordinary job that any agent with the tags may take. `TaskOut.batch` sums the latest
batch up (running while any job is; then failed if any didn't succeed). The card moves to
Review once every job succeeded, checked in `gateway._advance_batch` after each job's result
is committed, so the last of several jobs finishing at the same time sees the others.
Playbook step cards never fan out. A run takes an `agent_selector` (stored on
`playbook_runs.agent_selector` and given to every step and item card), and `{tags, all}`
there starts one run per connected agent, pinned to it with `{agent_id}` and titled
"<playbook> · <agent>". The response is the first run, with the others in `sibling_run_ids`.

## Dashboard

Everything in the UI is about one **current project**, remembered per person in the browser
(`currentProject.svelte.ts`): Home (`/projects/<id>/dashboard`, where `/` redirects), Board
(`/projects/<id>`) and Assets in the navigation follow it, and opening any project URL makes
that project current (a switch that came from a link is announced). Switching is in the
project title (`ProjectSwitcher`), the account menu and Ctrl+K; `/projects` is the picker.
The library (commands, tools, playbooks, variables, parsers), agents and people are shared.
Settings → Activity log is the feed across all projects. The dashboard API still answers for
all visible projects (no `project_id`) for API clients.

A project's Home is the dashboard component (`web/src/lib/components/dashboard/`).
`GET /api/dashboard/summary` gives the headline numbers (last 24 h), **needs attention**
(playbook steps waiting for a decision; cards whose latest run failed in the last week and
aren't done) and **live now** (queued/running jobs, running playbook runs with progress,
agents and slots), the pinned notes as one line each (`pinned`), the user's own open cards (`mine`: assigned to them, opened by them) and a few
`facts` about the work in scope. The greeting's headline rotates every 8 s between what's
moving, what's yours and a random fact (those, or general trivia from
`dashboard/trivia.ts`). `GET /api/dashboard/feed` is the timeline: activity events with what
they're about attached (the comment's text, the job's outcome, duration and agent, the
card, the author's picture), filtered by kind (`comment, job, card, move, file, playbook, note,
agent, admin`) and optionally to what's about the user (`mine=true`: their assigned, opened or
commented cards and comments mentioning them, done by someone else); admin events only for admins; job queue/dispatch steps never), paged with
`before=<id>`. A project's dashboard starts with its pinned notes (`GET/POST
/api/projects/{id}/notes`, `PATCH/DELETE /api/notes/{id}`, `GET /api/notes/{id}/history`,
`POST /api/notes/{id}/restore/{index}`, `POST /api/projects/{id}/notes/order`); every edit
keeps the previous version. Images pasted into a note are project attachments without a card
(`POST /api/projects/{id}/attachments`). The page refreshes from `activity.created`, `job.updated`,
`playbook_run.updated` and `agent.updated` events.

## UI realtime (`/ws/ui`)

Authenticated by the session cookie, or an API token in the `Authorization` header. The
server sends each domain event as
`{"type":"event","name":"task.moved","project_id":"…","data":{…}}` to the sockets allowed to
see it (see Auth). Event names include `project.updated`, `column.*`,
`task.created|updated|moved|deleted`, `job.updated`, `job.log`, `job.outputs`, `job.parser_proposal`, `job.resize`,
`comment.created`, `attachment.created`, `agent.updated`, `playbook_run.updated`,
`agent_tokens.changed`, `ssh_hosts.changed`, `api_tokens.changed`, `tool.status`, `tools.changed`,
`project.notes`, `notification.created|read`, `assets.changed`, `alert_channels.changed`,
`settings.changed`, `project.members` and `user.changed`; each section above names its own.
The client keeps page state in sync by applying these to its stores.

## Auth

`POST /api/auth/login` sets an httpOnly `castor_session` cookie (JWT). The same token is
accepted as `Authorization: Bearer`. The first admin is created from
`CASTOR_ADMIN_USERNAME` / `CASTOR_ADMIN_PASSWORD` when the database has no users.

**API tokens** (`api_tokens.py`, `api/tokens.py`, `api_tokens` table): personal
`castor_pat_…` tokens (SHA-256 stored) sent as `Authorization: Bearer`. `deps.current_user`
recognises the prefix, checks revocation and expiry, and checks the request against the
token's scopes: `api_tokens.RULES` maps every method and path to a scope (`area:read` /
`area:write`, write includes read, or `any` / `session` / `public`); unmapped routes refuse
tokens. The user is loaded as usual with `user.api_token` set, so roles still apply, and
`access.py` intersects visible projects with the token's `project_ids`. Token management,
the password and the profile are session only. Sockets take the token in the header
(`/ws/ui` needs `projects:read`, a job terminal `jobs:write`). The OpenAPI schema
(`/api/openapi.json`, Swagger at `/api/docs`) lists each operation's scope as
`x-castor-scope`.

**Roles** (`users.role`): `viewer` (read-only), `operator` (changes things in their
projects, creates projects, edits the command/playbook/variable libraries, reveals
secrets), `admin` (also users, agents and agent tokens, deleting projects, and every
project). `deps.Operator` / `deps.Admin` guard endpoints by role; there must always be one
active admin. `users.disabled_at` blocks sign-in and existing sessions (checked on every
request).

**Profile pictures** (`avatars.py`, `PUT /api/users/{id}/avatar`, `POST …/avatar/upload`):
initials, an uploaded PNG/JPEG/WebP/GIF (≤ 2 MB, type checked from its bytes, no SVG,
stored in `<data_dir>/avatars/`, served with `nosniff`), an https image URL, or Gravatar
(SHA-256 of the email). Users change their own; admins anyone's. `User.avatar_url` is
what the UI loads (null: initials); comments carry `author_avatar`.

**Lockout.** The API refuses to demote or disable the last active admin, and on startup
`accounts.ensure_active_admin` promotes the longest-standing active user (or re-enables
the longest-standing one) if a database has no active admin. `castor-admin`
(`manage.py`; `list-users`, `reset-password`, `enable`, `promote`, `create-admin`) works
from the server's shell with the server's settings, for when nobody can sign in.

**Projects** are seen by admins and by their members (`project_members`); `access.py` has
the checks. Anything under a project (board, cards, jobs, logs, attachments, playbook runs,
activity) follows it, and asking for something you can't see answers 404. Whoever creates
a project becomes a member; members who are operators, and admins, edit the member list
(`PUT /api/projects/{id}/members`). Variable sets limited to some projects are seen by
whoever sees one of them. Notifications go to the admins and members of their project.

The UI socket filters events per user (`ws/ui.py`): events of a project go only to those
who see it, `bus.publish(..., user_id=)` events only to that user. `project.members` and
`user.changed` make the affected sockets reload what they may see (a disabled user's
sockets are closed).

The migration to roles (`e5b1c7a93d42`) maps `is_admin` to admin, everyone else to
operator, and makes every existing user a member of every existing project.
`run_migrations` copies a SQLite database to `data/backups/` before applying migrations.

## Milestones

* **M1** (done): board, cards, agents, container jobs, streamed output, comments,
  attachments and screenshots, custom fields, activity feed.
* **M2** (done): command library, parsers (nmap XML → hosts and
  ports, etc.), playbooks with transforms, conditions and decisions, variables,
  notifications, visual playbook editor, per-agent tokens, users with roles and project
  membership.
* **M3** (done): tool environments, @mentions and a "mine" filter,
  per-person view, pinned project notes, AI, assets, search, run cards, import and export,
  interactive jobs, alerts, theme colours, duplicating cards.
* **M4** (done): scoped API tokens and an API reference, a built-in
  MCP server for AI assistants, phone usability.

What's next is in [ROADMAP.md](ROADMAP.md).
