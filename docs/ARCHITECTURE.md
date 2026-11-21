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
| Plugin agents | `server/castor_server/plugin_agents.py` | a plugin (Deno) | Machines a system already manages (Salt minions…), each an agent; the plugin runs their jobs |
| Libraries | `commands/`, `tools/`, `playbooks/` | YAML | Built-in commands (typed arguments), tool environments and playbooks, synced into the database at startup |
| Reverse proxy | `compose.yaml` (Caddy) | | Single origin: `/api` and `/ws` go to the server, everything else to the web app |

All services run under `podman compose` / `docker compose` from `compose.yaml` (published images; `compose.dev.yaml`
adds builds from the checkout).
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
  `lost` means the agent disconnected mid-run. An HTTP action can also be `waiting` (for a rate
  limit or the API's Retry-After, until `resume_at`) between `running` and its end. A job that
  needs approval starts as `approval` (before `queued`). `JobStatus.active()` is every unfinished
  status: a card has at most one job in them.
* **Job.kind** is `command` (an agent runs it) or `http` (the Castor server sends a request; see
  HTTP actions), `mcp` (a tool on another MCP server) or `plugin` (a plugin's library action). `Job.runner` is `server` for the jobs no agent ran; `Job.via_agent_id` is the agent
  an HTTP action's requests went out through, if any.
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
| `hello` | `token` or `secret`, `name, hostname, os, username, version, capabilities{runtimes[], default_image, host_install, tools[], tty, http, mcp_stdio, routes{box, ssh, proxychains}}, tags[], max_concurrency` |
| `heartbeat` | `running[]` (job ids), `tools[]` (cached tool image tags) |
| `tool.prepared` | `tool_id, ref, ok, error?, tools[]` (answer to `tool.prepare`) |
| `job.started` | `job_id, started_at` |
| `job.log` | `job_id, seq, stream ("stdout"\|"stderr"\|"system"), data` |
| `job.finished` | `job_id, exit_code, finished_at, error?, route_names?{address: name}` (the route box's stand-in addresses) |
| `job.route` | `job_id, method` (how it went through its network route: native, env, hooked, transparent) |
| `route.status`, `route.hop`, `route.host_key`, `route.tested` | a route's state on this agent, each hop's, an SSH hop's host key, a test's results ([ROUTES.md](ROUTES.md)) |
| `job.prompt` | `job_id, text` (an interactive job is waiting at this prompt; `null`: no longer) |
| `http.chunk` | `request_id, data` (base64, up to 512 KB of the response body; any number, in order) |
| `http.response` | `request_id, status, reason, headers[[name, value]], url` or `request_id, error, error_kind ("timeout"\|"connect"\|"too_big"\|"refused")` |
| `mcp.started` | `session_id, error?` (the stdio MCP program is running, or why it couldn't start) |
| `mcp.message` | `session_id, message` (one JSON-RPC message the program printed) |
| `mcp.exited` | `session_id, error` (it stopped: idle, `mcp.stop`, or it exited; with the end of its stderr) |

Server → agent

| type | fields |
| --- | --- |
| `hello.ok` | `agent_id, heartbeat_interval` |
| `hello.error` | `reason` (then close 4401; the agent exits) |
| `auth.revoked` | `reason` (token rotated or revoked; then close 4401) |
| `job.run` | `job{id, command, display_command?, image?, runtime ("container"\|"host"), env{}, files[], timeout_s, tty, tty_size?{cols, rows}, artifacts{token, collect[]}, tool?, route?}` (`route`: its network route with secrets and the ways to go through it, [ROUTES.md](ROUTES.md)) |
| `route.test` | `request_id, route, target?` (bring a route up and try it hop by hop) |
| `job.cancel` | `job_id` |
| `job.stdin` | `job_id, data` (keystrokes for an interactive job's terminal) |
| `job.resize` | `job_id, cols, rows` |
| `job.signal` | `job_id, name ("INT"\|"TERM"\|"QUIT")` (INT is sent as Ctrl+C through the terminal) |
| `tool.prepare` | `tool` (same shape as `job.tool`; build or pull it now) |
| `http.request` | `request_id, job_id, method, url, headers{}, body? (base64), timeout_s, max_bytes, route?` (one request of an HTTP action, sent from the agent's network, or only through `route`; redirects not followed) |
| `mcp.start` | `session_id, connection, command[], image?, tool? (a tool way, as in job.tool), env{}, idle_s` (start a stdio MCP server; a no-op if it runs) |
| `mcp.send` | `session_id, message` (a JSON-RPC message for its stdin) |
| `mcp.stop` | `session_id` |

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
parsers: `nmap.hosts`, `nmap.services`, `ffuf.results`, `httpx.results`, `gowitness.results`, `netexec.smb`,
`lines`, `regex`, `text`. XML goes through `defusedxml`, since tool output can carry external
untrusted content.

#### Comparing runs

`compare.py` (pure) says what changed between two runs' outputs. Lists of records are matched by
an identity key: from the type (`list<host>` by `ip`, `list<service>` by `host, port, proto`,
`list<smb_host>` by `host, port`, `list<url>` by `url`), else the first of `CANDIDATE_KEYS`
(`template-id` + `matched-at` for findings, `id`, `name`…) that every record has and none share,
else whole records. Each output is `same`, `changed`, `new` (only in this run) or `gone`, with
records `added`, `removed` and `changed` (with the fields that differ; lists cut at 500, counts
whole) and how many are `unchanged`. Lists of plain values compare as sets; other values whole,
objects field by field. An output that errored on either side is `error`, not compared.
`api/changes.py`:
- `GET /api/jobs/{id}/changes?base=` compares a job with an earlier finished job that has
  outputs. By default the closest kind of earlier run, then the latest: the same card, the same
  playbook step and command, the same library command, the same command line (all in the project).
  `candidates` lists the others (`how` says why each is comparable).
- `GET /api/playbook-runs/{id}/changes?base=` compares each step's usable results
  (`services._step_outputs`: succeeded jobs' outputs, `for_each` items merged, transform results)
  with the playbook's previous finished run in the project; steps only one run has are `new` or `gone`.

Web: the Output tab's **Changes** view (`JobChanges.svelte`, `?view=changes`), a folded summary in
`RunPanel` (`RunChanges.svelte`), both rendering `ResultChanges.svelte`; `lib/changes.ts` turns
records into a line and changes into Markdown. MCP tools `job_changes`, `run_changes`.

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

## HTTP actions

A library definition with `kind: http` (docs/ACTIONS.md, `commands/README.md`) is a request the
Castor server makes, not a shell command: `request` (method, url, query, headers, auth, one body:
`json`, `form` or text `body`), and optionally `paginate`, `batch`, `expect`, `retry` and a
suggested `rate_limit` (`http_spec.py`, parsed into `CommandSpec`). A card using one is still a
command card (`task.command` holds a one-line summary, `http_render.preview_line`); it can't be
detached or have its request edited by hand, and a definition that goes away turns its cards into
manual cards (`command_library.forget_definition`).

* **Rendering** (`http_render.py`) fills arguments and variable fields in one pass, escaped for
  where they land: a placeholder that starts the URL goes in as is (it must be an http(s)
  address), any other is percent-encoded; query and form values are URL-encoded (a list repeats
  the field, an empty one is left out); a `json` field that is only a placeholder keeps its type
  (lists, numbers; left out when empty); header values with line breaks are refused; a text body
  is escaped for its content type. Values are never read again for placeholders.
* **Running.** `enqueue_job` sets `jobs.kind`; the dispatcher hands `http` jobs to the server
  runner (`orchestrator/server_runner.py`, `CASTOR_SERVER_RUNNER_SLOTS` at once), fixing the
  request on the job when it first starts (`jobs.request`: the resolved arguments, target lists
  expanded, and the request part of the spec; never a variable value). The runner calls the
  gateway's `job.log` and `job.finished` handlers in-process with a `ServerConnection` (agent id
  None), so masking, activity, alerts, parsing, playbooks and summaries are the same as for agents.
  The log shows the request (headers like Authorization as `••••`, secrets masked) and the response
  status and headers; the body is the job's stdout, which parsers read (`json-records`,
  `json-value`). `save_as`, or a body that isn't text, is kept as an artifact instead.
* **Through an agent.** A card whose `agent_selector` names an agent or tags (step cards: never,
  they're sent from the server) gets `jobs.via_agent_id`: a connected agent with
  `capabilities.http` that may work for the project, chosen when the job starts or resumes. The
  server still runs it; each request goes out as `http.request` and comes back as `http.chunk`s and
  an `http.response` (`gateway.http_request`). The address guard doesn't apply there (it's the
  agent's network); the allowed hosts do. An agent that disconnects mid-request parks the job, and
  it carries on through whichever agent is connected. Agents started with `--no-http` don't offer
  it, nor do SSH hosts.
* **MCP actions** (`kind: mcp`, an `mcp: {server, tool, arguments?, timeout_s}` block). The
  server runner calls one tool on an `mcp_connections` row (Settings → MCP servers, admins;
  `api/mcp_connections.py`): `mcp_client.py` speaks the protocol (initialize, tools/list,
  tools/call, notifications) over Streamable HTTP (`HttpSession`, through the outbound guard) or to
  a stdio program an agent runs (`StdioHub`: `mcp.*` messages, one warm program per connection and
  agent, request ids per call; `gateway` routes the agents' messages to it and drops a lost agent's
  programs). `mcp_connections.py` fills `{{ field }}` in the connection's headers or env from its
  variable set (those values are masked in the log too: `_Run.extra`), picks the agent for stdio
  (`capabilities.mcp_stdio`, a container runtime for a tool or image, host commands otherwise),
  checks connections (`check`, its tools cached on the row) and drafts a definition from a tool's
  input schema (`draft_definition`). Arguments render in one pass like HTTP bodies
  (`http_render.mcp_arguments`). The result: `structuredContent` (else the text) as stdout,
  images/audio/resource blobs as artifacts, `isError` as a failure (a `Trouble`, so AI recovery can
  wait); log and progress notifications go to the console. A connection can be limited to projects.
* **Plugin actions** (`kind: plugin`, a `plugin: {id, action, timeout_minutes}` block, written by
  `plugin_library.action_items` from a manifest's `library_actions` as generated pack items, so
  install, update, conflicts and retiring are the pack's). The runner's `_call_plugin` checks the
  plugin is on and enabled in the project and that the person who started the card has their own
  settings, then sends the plugin the usual `action` request with `args`, `vars` (bound variable
  sets' values), `job` and that person's settings; `plugins.job_runs` sends the plugin's `log` and
  `progress` for the job's id to its console; a cancelled job sends the plugin `cancel`
  (`run.signal`). The handler's `output` is the job's stdout. `plugins.actions_rate_limit` (admins)
  adds a `plugin:<id>` scope to the job's rate limits.
* **Approvals.** A definition's `confirm: true` or a card's `tasks.confirm` makes `enqueue_job` create
  its jobs as `approval` (`services.ask_approval`: activity, a `job.approval` notification with
  subject `approval:<job id>`, `alerts.approval`); the dispatcher never picks them up. `decide_approval`
  (`POST /api/jobs/{id}/approve|reject`, operators who can change the project) queues them
  (`approved_by`, `approved_at`) or cancels them with the reason, a batch together, and clears the
  notifications; `app_settings.approval_by_someone_else` refuses approval by whoever started it.
  Approver groups (`tasks.approvers`, else `projects.settings.approvers`; see People) limit who
  approves or rejects (403 for anyone else) and who is asked.
  Home lists them under needs-attention (`kind: approval`). In playbooks `approval` maps to a queued
  step (and `waiting` to a running one), so the engine doesn't start the step again; a fan-out's
  items without a job are `unstarted`.
* **AI recovery.** When the fixed rules run out (`Trouble`) on a playbook step with
  `on_failure: ai`, `server_runner._recover` asks the resolver chain (`ai.recover_http`, masked
  context: the request without its query, the status, rate-limit headers, the start of the body,
  progress and what can change) for one move: `wait`, `smaller` (`progress.batch_size` or
  `page_size`), `slower` (the card's `rate_limit`), `skip_batch` (`progress.skipped`), `ask`, `fail`.
  Moves are counted in `progress.ai_moves` against `ai.max_retries`; `allow` and `confidence` apply
  (wait/smaller/slower as `retry`, skip_batch as `skip`). Batches count values (`progress.offset`),
  so a smaller batch carries on where the last one stopped. Ask or fail sets `progress.ai_said`
  and fails the job; `ai.auto_resolve` then leaves the step to a person.
* **Paging and batches.** `paginate` styles `cursor`, `link` (a dot path or the Link header; same
  host only), `offset` and `page`, with `items` (a dot path to each page's records) and `stop`
  limits; `batch: {arg, size}` sends one request per chunk of a list argument. Records are
  appended to `<data_dir>/actions/<job id>.jsonl` and come out as one JSON array. `jobs.progress`
  (batch, pages, items, the next cursor) is saved after each page and batch, so a retry or a
  resume carries on there.
* **Retries.** 429/503 wait for `Retry-After` (and pause everything under the credential); 5xx,
  timeouts and dropped connections retry with backoff (`retry: {attempts, backoff_s,
  max_backoff_s}`, default 3 from 2 s); other 4xx fail at once; `expect.status` (default any
  2xx) decides success. A wait over 30 s parks the job: status `waiting` with `resume_at`, its slot
  freed; `pump` starts waiting jobs whose time has come. A wait past the job's `timeout_s` fails it
  instead. Cancelling a waiting job ends it at once; a running one is cancelled in the runner. At
  startup `server_runner.recover` parks the server jobs the last process was running, to carry on.
* **Rate limits** (`rate_limits.py`): token buckets per variable set (`variable_sets.rate_limit`,
  counting every request made with its values) and per card (`tasks.rate_limit`, else the
  definition's), state in `rate_buckets`; `60/minute`, optionally `, 2 at once`. A request waits
  for room under every limit that applies. `X-RateLimit-Remaining: 0` with a reset pauses the
  credential's bucket.
* **Outbound guard** (`outbound.py`, also used by alert channels): loopback, link-local (cloud
  metadata), multicast and reserved addresses are refused unless listed in
  `app_settings.http_allow_addresses` or `CASTOR_HTTP_ALLOW_ADDRESSES`; `http_block_private` blocks
  private networks too; `http_allowed_hosts` holds HTTP actions to some hosts. The check runs on
  the resolved addresses at connect time (`GuardedBackend`, an httpcore network backend), and on
  every redirect hop (followed only on the same host). Environment proxies aren't used, and
  responses are capped at `CASTOR_HTTP_MAX_BYTES`.

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

## Network routes

Chains of hops (SSH jumps, SOCKS and HTTP proxies) a job's traffic goes through to reach networks
the agent can't reach directly: `routes` (hops with vault-encrypted secrets, `dns`, `strict`,
`segment`, `agents` (the agents it works from: ids and tags, null = any), `last_test`), chosen by `tasks.route`, `playbook_runs.route`, `projects.settings.route`
and `agents.route`; `jobs.route_info` records what a run went through and how. They run on agents (a
route box per route with a container runtime). Tools and commands say how they take one (`route:`)
and commands where its flags go (`{{ route }}`). All of it is in [ROUTES.md](ROUTES.md).

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

## AI summaries of runs

`ai_summaries` holds what the AI wrote about a finished job or playbook run (`job_id` or
`run_id`, each unique) or about a manual card (neither set). `task_id` is the card it shows on;
`status` is writing|done|failed, with `text`, `model`, and `requested_by` (null when written
automatically). `summaries.py` writes one when a project has `settings.ai_summaries` on (after a
job's outputs are parsed, when a run settles, when a manual card reaches Done) or on
`POST /api/jobs/{id}/summary`, `/api/playbook-runs/{id}/summary` or `/api/tasks/{id}/summary`, and
publishes `summary.updated`; a written summary records `summary.written` in the feed.

## Themes

Themes for the web UI live in `theme_definitions` (`id`, `origin` builtin|user, `spec`,
`source_yaml`): built-ins from `themes/*.yaml` (`CASTOR_THEMES_DIR`), custom ones from admins.
`users.theme` holds a person's choice (`""` is Castor's own look, null follows
`app_settings.default_theme`). `GET /api/themes` returns each with the CSS `theme_spec.compile_css`
makes of it and its `contrast` warnings (`theme_contrast.check`); the browser injects the CSS and
caches it for first paint. `GET /api/themes/default` (public) is the default, which
`hooks.server.ts` puts in every page for a browser that has never saved a theme (the sign-in
page). Custom themes travel in library archives (`transfer.LIBRARY`). Changes, and a new default,
publish `themes.changed`. Format: [THEME_SPEC.md](THEME_SPEC.md).

## Assets

A project's inventory of what it found (`assets.py`, `api/assets.py`): hosts, services, URLs,
domains, users, credentials, shares, files and vulnerabilities, one record per identity key with
merged `fields`, `asset_observations` for provenance (each with a `confidence` and, for a
mention, a `ref` to where it was) and `asset_links` between them. Parsed outputs from
`nmap.hosts`, `nmap.services`, `netexec.smb`, `ffuf.results`, `httpx.results` and `gowitness.results`, and
`list<string>` outputs with an `asset:` hint, are recorded after each job (`ingest_outputs`).
`assets.status` is confirmed, suggested (only mentioned: in comments, posts, card descriptions,
notes, unparsed output or a picture the AI read; `assets_text.py`) or dismissed, and
`assets.confidence` the best observation's. A host seen by name and by address is merged. Events
`assets.changed` and `asset_triggers.changed`; activity `assets.found`, `assets.suggested`,
`assets.confirmed`, `assets.merged`, `assets.described`. Each asset has a markdown `description`. The
scope (`projects.settings.scope`) is named groups of entries (`groups: [{name, include}]`) and
exclusions that win over every group; an asset reports the groups it falls in. Triggers run a command or playbook for new or changed assets: automatically
inside the project's scope (`projects.settings.scope`), otherwise after approval; once per
asset and rate-limited. Command arguments can take `{"assets": {type, where, field}}`.

Relationships (`asset_links`, kinds in `assets.LINK_KINDS`, each read both ways: `on`/has,
`resolves_to`, `valid_on` (works on / accepts), `admin_on`, `belongs_to`, `downloaded_from`/gave,
`affects`; Active Directory `joined_to`, `dc_of`, `in_domain`, `trusts`, `session_on`,
`authenticates_to`; network `uses_dns`, `routes_via`; and `related` for any two; each kind lists the
`details` usually recorded on it) carry where they came from (`source`, `parser`, `job_id`,
`task_id`, `actor_id`, `confidence`), details (`fields`: the protocol and port a login worked
over) and a `note`; `created_at` is first seen, `last_seen` the latest. Results record them
(`assets.link`: a NetExec login gives credential works on host and service, credential belongs to
user, and with Pwn3d! user admin on host; `file`/`download` hints give files on or downloaded
from shares, pointing at the job's artifact of the same name; a vulnerability affects the URL,
service or host it was found on); people add them (`POST /api/assets/{id}/links`, the kind must
fit the two types). `GET /api/assets/link-kinds`, `GET /api/projects/{id}/asset-links`
(`kind`, `type`, `asset`, `q`; counts per kind), `PATCH|DELETE /api/asset-links/{id}`. In `where`,
a kind or its reverse works as a field (`valid_on:dc01`, `has_admin:*`, `linked!=*`). New ones
between confirmed assets go to the feed (`assets.linked`, also a person's own; `assets.unlinked`)
and to triggers with `on_event: linked` (`link_kind`, optional `other_type`; once per pair,
`asset_trigger_fires.other_id`; scope is checked on the other end; templates take `{{ other.x }}`
and `{{ link.x }}`). The `file` type is a path (`\\host\share\path`, or `host:/path`).

History across runs (`asset_history.py`). Each observation keeps the agent its run went through
(`asset_observations.agent_id`). A finished, succeeded run read by a built-in parser in
`asset_history.ABSENCE` (`nmap.hosts` without `--open`, `nmap.services`, `httpx.results`) also
records what it covered and didn't see: confirmed assets of those types, first seen before the run
started, that the card's targets (`target`/`targets`/`hosts`… arguments with target lists and asset
references expanded, minus `exclude`) take in, and for a service whose port the scan tried (nmap's
`<scaninfo>`, else the card's `ports`); httpx only speaks for a host's root URL, and only when the
host didn't answer at all. Each gets an `absent` observation (once per run) and `assets.missing_since`;
seeing it again clears it. Activity `assets.missing`; triggers take `on_event: missing`.
`asset_history.timeline` reads an asset's observations as new / changed (field, before, after) /
seen / gone / still gone / back (`GET /api/assets/{id}` `history`); `GET /api/projects/{id}/assets/changes`
(`since`, `until`, `type`, `scope`, `command`, `agent`, `kind`) does the same across the project's
assets. `GET /api/projects/{id}/assets?seen=missing|present`.

Evidence (`evidence.py`, table `asset_evidence`: kind screenshot | certificate | headers | file, an
attachment for pictures and files, the job, card and agent, `look`/`look_changed` for screenshots,
`data` for a certificate or headers). A `list<url>` row's `screenshot`, `certificate`, `headers` and
`files` keys are kept on the URL (`evidence.prepare` before `observe`, `evidence.save` after); a
picture is matched to the job's artifacts by path or file name. `look` is a 256-bit difference hash plus a 4×4 brightness grid (`look_of`);
the asset's `look` field keeps its value while new screenshots are within `LOOK_DISTANCE` bits, so a
real change is an ordinary field change (history, Changes with `pictures` before/after, and a trigger
condition `{field: look, op: changed}`, `conditions_met(..., changed)`). Certificates and headers
are kept when they differ from the latest. Deleting a card keeps its evidence attachments (no card).
`GET /api/assets/{id}` `evidence`; `GET /api/projects/{id}/assets/gallery` (`type`, `where`,
`scope`, `seen`) groups assets with a screenshot by look (`evidence.group_by_look`).

Vantage points (`segmentation.py`). `agents.segment` (admins, `PATCH /api/agents/{id}`) is where an
agent's runs look from; every observation records it (`asset_observations.segment`, `assets.segment_of`).
Timelines are per segment: an absence from a segment that never saw the asset is `unreached` (and
doesn't set `missing_since`), a first sighting from another segment `reached`. `GET
/api/projects/{id}/assets/reachability` (`type`, `where`, `scope`, `batch` for one fan-out, `asset`,
`differs`) is the matrix: per asset, each segment's latest observation (seen, with the `COMPARE`
fields, or not seen) and what the segments disagree on (`reach`, field names; looks compared with
`evidence.similar`). Segmentation rules (`projects.settings.segmentation`, `GET|PUT
/api/projects/{id}/segmentation`: `{id, name, from, type, where, scope, severity}`): a finished run
from a rule's segment that reaches a matching asset (`after_ingest`) records a vulnerability
(`SEGMENTATION-<rule>-<hash>`, tool `segmentation`) that affects it; `POST
…/segmentation/check` applies them to what's known. Activity `project.segmentation`.

## Plugins

Design and status: [PLUGINS.md](PLUGINS.md). `plugin_manifest.py` (`castor-plugin.yaml`; `not_yet`
lists what this version can't run yet: hooks), `plugin_package.py`
(checking, staging and unpacking a release under `<data_dir>/plugins`), `plugin_runtime.py` (a
Deno process per plugin with only its approved permissions, JSON-RPC over stdin/stdout, the module
graph check, crash counting), `plugins.py` (install/update/uninstall, settings and vault-sealed
secrets, the service account and its per-start token, answering the plugin's calls: `api` made
in-process through `httpx.ASGITransport` as the plugin, `storage.*`, `file.read`, `progress`, `log`;
running actions), `api/plugins.py`, SDK `plugin_sdk/mod.ts`. Tables `plugins`, `plugin_projects`,
`plugin_storage`, `plugin_runs`, `plugin_logs`; `users.plugin_id`. Events for plugins
(`plugin_events.py`): `emit(db, name, project_id, data, by=)` at the point a thing happens
(`assets.settle`, the gateway's job finish, `services._settle_run`, `services.move_task`, comment
creation), delivered after commit (a SQLAlchemy `after_commit` listener) to each subscribed plugin's
queue; never to the plugin that caused it. `network:` becomes `--allow-net`; plugin API calls are
rate-limited. Per-person settings: `plugins.per_user` (the admin's choice, within the manifest's
`per_user`), values in `plugin_user_settings` (vault-sealed secrets), `/api/auth/me/plugins`
(session only); `plugins.settings_of` gives a run or event the shared settings plus the person's. Events `plugins.changed`,
`plugin_run.updated` (project); activity `plugin.*` (installs in the admin feed, runs as feed kind
`plugin`). `POST /api/projects/{id}/assets/bulk` records assets and relationships at once (source
`plugin` for a plugin's account, else `import`). Official plugins live in the
[castor-plugins](https://github.com/emilyastranova/castor-plugins) repository (`scripts/pack.py`
makes their `.zip`); `tests/test_plugins.py` runs them from a checkout beside this one (or
`CASTOR_PLUGINS_REPO`) and skips them without one.

The directory (phase E): `plugin_directory.py` reads `CASTOR_PLUGIN_DIRECTORY_URL` (`directory.json`:
publishers, each with the GitHub accounts it may list from, and plugins with `repo`, `tag_prefix`,
`tags`; validated by `Directory`), cached an hour in memory and on disk under
`<data_dir>/plugins/.directory`; `latest` reads a repository's releases from `CASTOR_GITHUB_API_URL`
(`CASTOR_GITHUB_TOKEN` optional) and picks the newest tagged `<tag_prefix>v<version>` with
`<id>-<version>.zip`; `download` checks it against `<zip>.sha256`; the preview then requires the
manifest's id, publisher and version to be the directory's. Only https, or the configured API's own
origin, is fetched. `check_updates` (daily from `check_forever`, unless `app_settings.plugin_update_checks`
is off; `POST /api/plugin-directory/check`) sets `plugins.latest_version`/`checked_at` and notifies
admins (`plugin.update`, `accounts.active_admin_ids`). `api/plugin_directory.py`: `/api/plugin-directory`,
`…/plugins/{id}` (release notes, manifest, README), `…/plugins/{id}/icon`, `…/publishers/{id}`. Web: the
Browse tab (`PluginDirectory.svelte`, `DirectoryCard.svelte`), `routes/settings/plugins/directory/[id]`
and `publishers/[id]`, `PluginInstallDialog` with `directoryId`.

Packs (library content in plugins): manifest `contents` lists kinds; files are
`library/<kind>/*.yaml` (`plugin_library.read`, at package time; a pack's commands may use its own
parsers). `plugin_library.apply` upserts them with `origin = "plugin:<id>"` (the `origin` columns are
80 long) and retires what an update dropped; `retire` on uninstall, like built-ins retire (cards
keep their command lines; a tool, parser or variable kind something else still uses stays as a user
item). An id someone else has is a preview problem (`conflicts`). Library APIs refuse to change or
delete them (`plugin_library.read_only`, `why_read_only`, `cant_delete`); web `lib/origin.ts`. A
plugin with no `main` (and no actions or events) runs nothing: status `library`. `plugins.castor_ok`
checks the manifest's `castor:` versions against `CASTOR_VERSION`.

Asset types and relationship kinds from plugins: a manifest's `asset_types` and `link_kinds`
(checked in `plugin_manifest.py`: names fit `assets.type` (16) and `asset_links.kind` (24), never
Castor's own; a kind with Castor's name only adds pairs). `assets.TYPES`, `LINK_KINDS`, `LINK_WORDS`
and `ORDER` are Castor's (`CORE_TYPES`, `CORE_LINK_KINDS`) plus every installed plugin's, rebuilt
in place by `assets.register_plugin`/`unregister_plugin` (`plugins.install`/`uninstall`, and
`plugins.load_types` at startup); `assets.conflicts` refuses a name another plugin has. A plugin
type's key is its identity fields joined with `|` and its label `label_template`. Assets of a type
no plugin declares any more are listed by `GET /api/assets/types` as `orphan_type` (`plugin:
"removed"`). Web `assetTypes` reloads on `plugins.changed`.

Unrestricted plugins (phase D): `runtime: unrestricted` runs with `UNRESTRICTED_FLAGS` (`--allow-all`, no
module graph check). `plugins.restricted(db)` is `config.plugins_restricted` (env lock) or `app_settings.plugins_restricted`
(default on, changed only by `PUT /api/plugin-mode`, which stops unrestricted plugins when it goes on); `check_runtime`
raises `PluginBlocked` at preview, install, enable and start. Installing one needs `confirm` = its id; grants record
`runtime`. Each start records `plugin.started`. `config.plugins_safe_mode`: `start_enabled` starts none and
`ensure_running` refuses. `ctx.api.upload` is the `api` call with `file` (multipart, `UPLOAD_MAX`). `page.script`
(unrestricted only): `GET /api/plugin-scripts`, loaded by web `lib/pluginScripts.ts` (`window.castorPage`).

Panels (manifest `ui`, slots `project.page`, `card.tab`, `asset.panel`): `api/plugin_panels.py`
serves a plugin's static files at `/api/plugin-ui/<id>/<sig>/<path>` (public; `sig` is an HMAC of
id and checksum under `secret_key`), HTML with `plugin_sdk/ui.js` inline and a CSP that sandboxes
it (`sandbox allow-scripts`, `connect-src 'none'`, sources named by host since a sandboxed `'self'`
matches nothing, `frame-ancestors 'self'`); `GET /api/projects/{id}/plugin-panels` lists a
project's; `POST /api/plugins/{id}/panels/{ui}/call` takes a page's requests (`kind: api`, a REST
call as the plugin through `plugins.call_api`; `kind: call`, the `ui` method to the plugin's
`ctx.ui.on` handlers, whose names and `readOnly` flags `activate` returns into
`PluginProcess.ui_handlers`/`ui_read_only`). Viewers and API tokens get GET calls and read-only
handlers only. Web: `PluginFrame.svelte` (the iframe and its postMessage bridge: context, theme
variables, requests, resize, open, notify), `pluginPanels` in `lib/plugins.svelte.ts`, placed in
`AssetDialog`, `TaskDialog` (tabs `plugin:<id>:<ui>`) and the Assets page (tabs, `?tab=plugin:…`).

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
notifies the job's starter, the card's creator, assignees (and assigned groups' people) and Notify-me people, emits the
`input` alert event, and lists it in Needs attention (`kind: input`). Text consumers (AI log
tail, `expect.log`, `/api/jobs/{id}/output`) strip terminal escapes (`interactive.plain`);
`?raw=1` returns the exact bytes.

**Shell cards** (`tasks.shell`, `shells.py`): a command card whose command is `shells.SHELL_COMMAND` (bash -l
where there is one, else sh -l), with `tty` on and no timeout; `shells.apply` turns it on or off (create/PATCH
`shell`). It never sets `waiting_input` (a shell always sits at its prompt), and leaving it is `succeeded` whatever
its last command's exit code (the gateway's `job.finished`; the code is kept). Web: New card → Shell makes it, runs
it and opens the card with `?control=<job>`, so `JobConsole`'s `autoControl` takes control once it's running.

## Manual runs

Output people bring in to be parsed (`manual_runs.py`, `api/manual_runs.py`): pasted, a comment's or timeline post's
(`text_of_writing`: their code blocks when they have any) or a run's (`text_of_job`, a shell session's say, or the
selected text). `POST /api/parsers/try` finds the text and ranks every parser that reads something from it
(`manual_runs.rank`: the ones that recognise the format by how much they read, then `GENERIC`); nothing is kept.
`POST /api/tasks/{id}/manual-runs` makes it a job on the card: `runner = "manual"`, finished, `origin` ({from, id,
edited}), the text as one stdout chunk and one output `results` read by the chosen parser, then `parse_job`, so its
results table, assets and relationships, changes and summaries work as for any run. Without a parser the output
spec has none (`outputs._specs` keeps it unvalidated for manual runs) and `parse_job` records it unread
(`manual_runs.UNREAD`, a mismatch), so `ParserFix` offers a draft; `ai: true` drafts one straight away
(`manual_runs.draft`, a `ParserProposal`); using it re-reads the run. A manual run never records absences
(`asset_history.record_absent`). Web: `ParseOutputDialog` (card menu Parse output…, a comment's button, the console's
parse button, a timeline post's menu, which picks a card or makes one); manual cards show Output when they have runs.
MCP `try_parsers`, `parse_output`.

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

## Cards waiting on cards, and due dates

**Depends on.** `tasks.waits_on` (card ids as strings; not playbook steps' `depends_on`) lists cards of the same
project a card waits on; `services.check_waits_on` refuses itself, other projects, step cards and circles (422).
A blocker is unfinished until it's in a Done or Reported column or archived (`unfinished_blockers`);
`TaskOut.blocked_by` is that list. `move_task` refuses to start a blocked card (into the active column) with
`BlockedError` unless `force`; the move and run routes answer 409 `{message, blocked_by}` and take `force`
(web `lib/blocked.ts` `startAnyway` asks, then retries). Finishing a card (moving it into Done/Reported, or
archiving it) calls `notify_unblocked`: the people (`card_people`: assignees and their groups, else the creator)
of each card it was the last blocker of get `task.unblocked`. After a card moves, is archived, restored or
deleted, `publish_waiting` sends out the cards that wait on it so boards update; a deleted card leaves the lists
(`forget_card_in_waits`). `GET /api/tasks/{id}/dependencies` gives `waits_on` and `blocks` with columns.

**Due dates.** `tasks.due_at` (the web stores a date without a time as 23:59 local). `due.py`'s sweep
(`remind_forever`, every minute, started in `app.py`) notifies a card's people once when it's due within a day
(`task.due_soon`) and once when it's past (`task.overdue`, severity warning), remembered in
`tasks.due_reminded` and cleared when `due_at` changes; finished, archived and step cards aren't reminded.
They're ordinary notifications, so the bell shows them and `notifications.svelte.ts` puts them on the desktop
for people who allow it. Web: `DueDateField` (card dialog, New card), `lib/due.ts` (`dueInfo`: label and tone),
the board card's and list row's chips, `CardDependencies` in the card dialog.
MCP: `create_card`/`update_card` take `due_at` and `waits_on`, `move_card`/`run_card` `force`, `card_dependencies`.

## Archived cards

`tasks.archived_at` / `archived_by`: an archived card is left off the board (`get_board`), the dashboard's open
and failed counts and people's open cards, but keeps everything and is still found by search (titled
"(archived)") and links. `services.archive_task` / `restore_task` record `task.archived` / `task.restored` and
the routes publish `task.updated`, which the web `BoardStore` reads as "off the board" or "back on it"
(`api/archive.py`):
- `POST /api/tasks/{id}/archive` and `…/restore`. A running card (or a run card whose run is going) and a
  playbook step's card can't be archived (409); an archived card can't be moved or run until restored (409).
- `POST /api/projects/{id}/columns/{column}/archive` archives a column's cards, skipping running ones.
- `GET /api/projects/{id}/archived?q=` lists them, most recently archived first.

A duplicate is never archived. MCP: `archive_card`, `restore_card`, `archive_column`, `list_archived_cards`.
Web: Archive / Restore in the card menu and a banner on an archived card (the board page opens one that isn't on
the board as `archivedTask`), "Archive all" in a column's header, `ArchivedCardsDialog` in the project menu.

## Cards with subtasks

`tasks.parent_id` (FK to `tasks`, `SET NULL`) makes a card a subtask of an activity card (`subtasks.py`). One
level deep: an activity can't be a subtask, a subtask can't have subtasks, both are in the same project, and
playbook run and step cards take no part (`check_parent`/`check_child`, 422 with the reason). Subtasks are left
off the board (`get_board`; the web `BoardStore.upsertTask` drops a task with `parent_id`), and `TaskOut.subtasks`
(`SubtaskSummary`: `total`, `done`, `running`, `failed`, `approvals`, `next_due`) gives an activity's progress;
finished means a Review, Done or Reported column, or archived (`DONE_ROLES`). `publish_task` publishes a
subtask's activity too so its card stays current.

Unless the project's `settings.subtasks_move_card` is `false`, the activity follows its subtasks (`follow`,
called from `move_task`, `start_run` and the gateway's auto-move to Review): into the active column when one
starts, and to Review (else Done) when every one is finished. Archiving an activity archives its subtasks at the
same moment and restoring it brings back those (not ones archived on their own before); deleting it puts its
subtasks back on the board; duplicating it copies its subtasks as fresh cards (a subtask's copy stays in the same
activity); import inserts activities before their subtasks.

- `GET /api/tasks/{id}/subtasks` (`?archived=true` for archived ones too), in `position` order.
- `POST /api/tasks/{id}/subtasks {task_ids}` adds existing cards (all or nothing);
  `POST /api/projects/{id}/tasks` with `parent_id` creates one.
- `POST /api/tasks/{id}/subtasks/order {task_ids}`, `POST /api/tasks/{id}/release` (its own card again, at
  the top of its column).
- `POST /api/tasks/{id}/subtasks/run {force}` runs every unfinished command subtask that isn't running;
  ones waiting on unfinished cards are skipped unless `force` (`{started, skipped: [{id, title, reason}]}`).
- `POST /api/projects/{id}/consolidate {title, description?, task_ids, column_id?}` makes a new activity
  holding the cards, in `column_id` or the column most of them are in (the active one rather than Done when
  some aren't finished).

- `POST /api/tasks/{id}/subtasks/many {command_ref, target_arg, targets, args?, title?, var_bindings?}` makes one subtask per
  target (`subtasks.targets_of`: a list, text, a saved list or an asset query; at most 500), each running the command with
  the target in `target_arg`.
- Run all with `at_once`: the rest wait in `tasks.subtask_run` (`{queue, at_once, force, by}`); `subtasks.pump` starts the
  next ones when a subtask's run ends (the gateway's job-finished handler, a cancel, a rejected approval);
  `POST /api/tasks/{id}/subtasks/stop` empties the queue. `SubtaskSummary.queued`/`at_once` show it.
- `GET /api/tasks/{id}/subtasks/playbook` drafts a playbook from the library-command subtasks (`waits_on` → `depends_on`,
  variable sets by name; manual ones and hand-typed commands are listed as left out); `POST /api/playbooks/{id}/activity`
  makes an activity from a playbook's command steps (inputs filled in, `depends_on` → `waits_on`; transforms, `for_each`
  and steps fed by another step's output are left out and named in its description). Nothing starts.

A subtask keeps its `position` (its place among its activity's subtasks) when it moves between columns. Home counts
an activity once (open cards), names a subtask's activity in needs-attention, search ("… in “Activity”") and on person
pages (`subtasks.activities_of`), and the feed marks entries about a subtask with `activity` and rolls up runs of
entries of one kind about one activity's subtasks into one (`dashboard._roll_up`: job.succeeded/failed/lost, moves to
the same column; `FeedItem.subtasks`).

Activities: `task.subtasks_added`, `task.subtask_released`. MCP: `consolidate_cards`, `add_subtasks`,
`list_subtasks`, `release_subtask`, `order_subtasks`, `run_subtasks` (`at_once`), `stop_subtasks`,
`add_subtasks_for_targets`, `subtasks_as_playbook`, `activity_from_playbook`, and `create_card` takes `parent_id`.
Web: `SubtaskPanel.svelte` (TaskDialog's Subtasks tab: drag to reorder, tick manual ones done, Run all or a few at
a time, a graph through `PlaybookVisualizer` when they wait on each other, Save as a playbook through the editor's
`?draft=1`; subtasks open on top via `?step=`, and the board page redirects `?task=<subtask>` there),
`ManySubtasksDialog.svelte`, "Inside a card" in `NewTaskDialog` (`parent_id` on create), "Move into a card…" in the card menu
(`MoveWritingDialog` with `what="card"`, then `add_subtasks`), a drop strip on activity cards while a card is dragged (`lib/dropInto.svelte.ts`,
TaskCard), "Start as a card with subtasks" in `RunPlaybookDialog`, `GroupCardsDialog.svelte` (the project menu's "Group cards into one…" and
"Add cards from the board"), the progress on `TaskCard`/`BoardList` (`taskStatus.subtaskState`), and a
"subtask of" banner on a subtask's card.

## Services (jobs that run until stopped)

A command definition's `service:` (`command_spec.ServiceSpec`: `stop_signal`, `stop_timeout`, `ready`,
`watch`, `parse_every`), or a typed command card's `tasks.service`, makes its jobs services: `jobs.service`
holds the settings at dispatch (`services.enqueue_job`; no `timeout_s`). The dispatcher sends them only to
agents with the `services` capability and counts them against `max_services` (`AgentConnection.services`,
`free_service_slots`), not `max_concurrency`; `job.run` carries `service: {stop_signal, stop_timeout}` and the
agent runs a container service with `--init`. `service_jobs.py`:

- `stop` (`POST /api/jobs/{id}/stop`, `/restart`; MCP `stop_service`) records `progress.stopping` and sends
  `job.stop {signal, timeout}`; the agent (`JobRunner.stop`/`_end`) signals the process group (or
  `<runtime> kill --signal`), waits, then kills. When it finishes with `progress.stopping` the gateway marks
  it succeeded whatever the exit code ("was stopped"), and `restart` runs the card again (`_restart_service`).
  Cancel goes through `_end` too (SIGTERM, 5 s, then a kill), for every job.
- `on_output` (from the gateway's `job.log`) sets `progress.ready_at` on the `ready` pattern and, for `watch`
  patterns, keeps `progress.matches` and notifies the card's people (`job.watch`, each distinct line once, at
  most `MAX_NOTES` a run); patterns run through `regex` with a timeout.
- `parse_running` (every 5 s from `app.py`) re-parses running services' outputs when there's new output and
  `parse_every` has passed (`outputs.parse_job(partial=True)`: assets recorded, nothing that waits for the end).

Web: `ServicePanel.svelte` (state, Stop, Restart, Force quit, recent matches) in TaskDialog, whose header
shows Stop for a service; "Runs until stopped" with the stop signal and time to exit for typed commands.
Tests: `tests/test_services.py` (a fake agent), agent `tests/test_runtime.py` (a real graceful stop).

## MCP tools from plugins

A plugin's manifest `mcp_tools` (`plugin_manifest.McpToolSpec`, named `<plugin>__<name>` by
`mcp_tool_name`) are added to Castor's MCP server for the caller (`mcp.plugin_tools` on `tools/list`, from
`GET /api/plugin-tools`; `tools/call` of a name with `__` goes to `_call_plugin_tool` →
`POST /api/plugin-tools/{plugin}/{tool}` in `api/plugin_tools.py`). Offered only when approved
(`plugins.grants` has `mcp_tools`, so an update adding one asks again), the plugin is on, and in its
projects; each gets a required `project_id`. The call checks read access (read-only tools) or write access
plus a token's `projects:write`, takes room under the plugin's `actions_rate_limit`, sends `mcp` to the
plugin (`ctx.mcp.on`, names from `activate` in `PluginProcess.mcp_handlers`) with `timeout_seconds`, and
records `plugin.tool.called` for tools that aren't read-only. Answers come back marked untrusted
(`mcp.FROM_PLUGIN`). Web: the install review lists them (`PluginInstallDialog`). Tests:
`tests/test_plugin_mcp_tools.py` (a real Deno plugin).

## Spoilers

`||text||` (Discord's format) hides text until someone clicks it, in titles, descriptions, comments, posts, notes and
summaries. It's display only: the API, search, the AI and exports carry the text as written. Web `lib/spoilers.ts`
(the rule, a `marked` extension for rendered markdown, `Spoilers.svelte` for plain text, `maskSpoilers`, asterisks, where text
can't be hidden (snackbars, notifications, dropdowns, search results; the pipes show only where you type), and `revealSpoilers`, one capturing click/Enter handler from the root layout that shows a spoiler and stops
the click so the card under it doesn't open); styles `.spoiler`/`.revealed` in `app.css`. A card's title with a
spoiler shows as text until edited (TaskDialog `editTitle`). The markdown toolbar has a Spoiler button. Server
`spoilers.py`: `mask` (asterisks) for chat alerts (`alerts.values_for`) and webhooks (`notifications._send_webhooks`),
`html_text` and a markdown-it rule (`|` added as a text terminator) for the timeline export, where a spoiler is a bar
that shows on hover in HTML and stays a bar in PDF (the text is still in the file).

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

**Groups** (`groups`, `group_members`, `project_groups`; `api/groups.py`; Settings → Groups):
- **Handles:** a group's `slug` is its `@handle`. Handles and usernames share one namespace (`people.handle_taken`).
- **Mentions:** `@group` in a comment mentions the group's people who can see the project (`people.mentioned`).
- **Access:** a project shared with groups is visible to their members as if each were a member (`access.member_project_ids`, `people.project_people`).
- **Assignment:** `tasks.group_assignees` holds group ids (the API also takes `@handles`). The card is everyone's in the group who can see the project: Home's *mine*, `is:mine`, the board's *assigned to me* filter, waiting-prompt notices (`people.assigned_to`, `my_group_ids`, `group_people`). Assigning notifies those people (`task.assigned`), except the actor and anyone assigned directly.
- **Approvers:** `projects.settings.approvers`, `tasks.approvers` and `asset_triggers.approvers` hold group ids (the API takes `@handles`). The card's or trigger's own, else the project's (`people.approver_groups`), decide who approves or rejects a waiting run (`decide_approval`, 403 `NotApprover`) and approves or skips a waiting trigger fire: their members and admins (`people.may_approve`). Only those people are asked (`people.approver_people`; a group with nobody in it falls back to everyone, so nothing waits unseen). Changing approvers needs someone who may approve under the current ones (403). No groups: anyone who can change the project, as before.
- **Told when it finishes:** a card's `alerts.groups` (like `alerts.people`) tells the groups' people who can see the project when it finishes (`alerts.emit`, `END_EVENTS`).
- **Notifications:** `notifications.notify` without `user_ids` reaches everyone who can see the project, members through its groups included, never a plugin's service account.
- **Sign-on:** a group with `sso_group` follows that provider group at each SSO sign-in (`sso.sync_groups`), but only when the provider sent groups.
- **Realtime:** changes publish `groups.changed` and `project.members`, and sockets reload what they may see.
- **Export:** a project export carries its groups' handles (and those of every group its cards, triggers and settings name: assignees, approvers, groups told), and an import maps them onto the local groups of the same handle (`transfer.regroup`), dropping ones it doesn't have.

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
record has `payload.via = "mcp"`, and comments they write keep it in `comments.via` (shown on the
card). `castor-mcp` (agent package) bridges stdio clients to it.
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

**Agents limited to projects.** `agents.project_ids` (ids as strings; null: any project, `[]`: none) keeps
an agent to some projects' jobs: `dispatcher.project_allowed` in `pump`, `broadcast_targets` and a playbook's
`{tags, all}` fan-out; `check_selector(…, project_id)` refuses `{agent_id}` for an agent that can't run the
project's jobs (422). Only admins set it (`PATCH /api/agents/{id}` `project_ids`; left out keeps it, null lifts
it), or the enrollment token it used (`agent_tokens.project_ids`, copied when it enrolls); an agent's `hello`
never changes it. Deleting a project takes it out of every list (`services.forget_project_for_agents`), so an
agent limited to only that project runs nothing rather than anything. `GET /api/agents` shows non-admins the
agents that may work in a project they can see. Web: `AgentProjectsPicker` (agent editor, Enroll agent), a
Projects line on agent cards, `agentSelector.agentsFor` narrows the card, New card and Run playbook pickers and
the board's "agents online".

**Agents only some people may use.** `agents.people` (`{users: [ids], groups: [ids]}`; null: everyone, empty
lists: admins only) keeps an agent to some people's work: `agent_people.allowed(people, who)` next to
`project_allowed` in `pump`, `_to_server` (an HTTP action's sending agent), `broadcast_targets`, a playbook's
`{tags, all}` fan-out and route tests. Admins always may. Whose work a job is: `jobs.run_as`, set by `enqueue_job`
(`agent_people.run_as_for`: the person starting it, else the playbook run's starter, else the card's maker, so a
trigger's card is its trigger's maker's); a job from before it falls back to `created_by`; nobody (a deleted or
disabled account) matches no limited agent. Only admins set it (`PATCH /api/agents/{id}` `people`; left out keeps
it, null lifts it); ids already stored stay even when gone, only new unknown ones are refused. A job for someone
else waits (queued). `GET /api/auth/me` carries `group_ids` so the web can tell (`agentSelector.usableBy`; keep it
the same as the server's rule): `agentsFor(agents, project, user)` narrows the pickers, TaskDialog says when the
pinned agent isn't yours, agent cards show a For line, `AgentPeoplePicker` in the agent editor. MCP servers'
stdio programs aren't limited by people (admins set those connections up for projects).

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
card, the author's picture), filtered by kind (`comment, job, card, move, board, file, playbook, library, note,
asset, agent, admin`; library changes, `library.<item>.<action>` from `services.library_activity`,
have no project but show in every project's feed, with a `link` to the item) and optionally to what's about the user (`mine=true`: their assigned, opened or
commented cards and comments mentioning them, done by someone else; each item has a `link` to what it's
about from `dashboard.link_for`: the comment, the run's output, the card's files, the post, the library
item or the page, or none when it's gone, and clicking an entry goes there); admin events only for admins; job queue/dispatch steps never), paged with
`before=<id>`. A project's dashboard starts with its pinned notes (`GET/POST
/api/projects/{id}/notes`, `PATCH/DELETE /api/notes/{id}`, `GET /api/notes/{id}/history`,
`POST /api/notes/{id}/restore/{index}`, `POST /api/projects/{id}/notes/order`); every edit
keeps the previous version. Images pasted into a note are project attachments without a card
(`POST /api/projects/{id}/attachments`).
**Snapshots** (web `lib/snapshot.ts`, `SnapshotEditor.svelte` in `SnapshotDialog.svelte` and on the `/snapshot` page): a
picture of some of a job's output (the console's camera button) or of any text (`source: 'text'`, the text in `input`:
the Snapshot page, a card's New snapshot, or a rendered code block's camera button from `copyCode.ts`, which opens the
dialog through `lib/snapshots.svelte.ts` in the root layout with the fence's language) for a report. Text is replayed
the same way as output, so colour codes in pasted text work. The browser replays the job's log chunks into an unopened xterm.js terminal
(the console's emulator, so colours, progress bars and tty output come out as shown), joins wrapped rows into
logical lines, and builds a scene (rectangles and text runs) from a recipe: the lines `[from, to)`, highlighted
lines, hidden cell ranges and regex patterns (hidden text is left out, a bar drawn instead; patterns also cover the
command line), text edited by hand (`text`: the shown lines, styled like the characters they replace; a hidden
range on an edited line hides wherever its text still appears), syntax highlighting (`language`: `ansi` for none,
`auto`, or a highlight.js language, loaded on demand; it colours only cells the terminal left in the default
colour), frame, colours (`console`, `paper`, or the current theme captured into the recipe), background and its
`colors`, padding, size, wrap and scale. Castor's own log text is written in a sentinel colour so its cells are
dropped even when they share a line with output. The look (not the lines, edits or hidden ranges) is remembered per
person in localStorage (`loadStyle`/`saveStyle`; the language only when picked by hand). Copy puts a PNG drawn ahead of time on the clipboard with an HTML
`<img>` of it, which document editors such as Google Docs take. The scene is drawn on a canvas for PNG (copy, download, attach) or written as SVG. Attaching
posts the PNG to `POST /api/tasks/{id}/attachments` with `snapshot` (the recipe as JSON, at most 64 KB) and
`job_id` (a job of that card; none for text), stored in `attachments.snapshot` (at most 512 KB); `PUT /api/attachments/{id}` (file + snapshot,
snapshots only) saves a changed one in place, so everything showing it shows the new picture
(`attachment.updated`, an activity of the same name). Drawing is browser-only, so MCP has no snapshot tool.
**Files** (`api/files.py`; web `routes/projects/[id]/files`, `lib/files.ts`, `FilePreview.svelte`): every attachment of
a project in one place, as a gallery (grouped by day or card) or a list. `GET /api/projects/{id}/files` lists them with
where each came from: `card_id` is the card on the board (a run's card for a playbook step's files, with `step` and
`run_id`/`run_name`), `agent_id` the agent of the job, `source` upload | artifact | snapshot | project (pasted into a
note or post), `category` image | text | data | document | archive | other (`files.category`, from the type and the
extension). Filters `category`/`source` (comma separated), `card` (`none`: notes and posts), `run`, `agent`, `since`/`until`
(days in `tz`) and `q` (words in the file name, card, run, step, agent or uploader); `sort`, `limit`, `offset`. `facets`
count each filter's values with the other filters applied (the rows are read once and filtered in Python).
`POST /api/projects/{id}/files/zip` (`ids`, or a `filter`) streams a zip with a folder per card, steps inside their
run's card, artifacts keeping their folders, clashing names numbered (a read: `projects:read` for tokens).
`GET /api/attachments/{id}/thumbnail?size=160|360|720` makes a WebP once with Pillow (`<data_dir>/thumbnails/<project>/`,
keyed by the stored path, so a redrawn snapshot gets a new one; removed with the attachment; 415 for SVG or what Pillow
can't read, and the gallery shows the picture or an icon instead). The page follows `attachment.*` events; the preview
shows pictures, text (the first 256 KB, with a snapshot of it a click away) and PDFs, and steps through the list with ←/→.
**Timeline exports** (`report.py`, `api/exports.py`): `GET /api/projects/{id}/timeline/export?format=html|pdf`
with the feed's filters (`kinds`, `mine`, `tag`) plus `since`/`until` (dates, inclusive), `tz` and `order` (newest or oldest first). It selects with
`dashboard.select_events` (at most 5,000, newest first, sorted by when they happened) and `_enrich(full=True)`,
renders one self-contained HTML document (markdown through markdown-it and nh3; this project's image
attachments and uploaded avatars embedded as data URIs, any other image a link), and for PDF hands that HTML to
WeasyPrint with a fetcher that allows only `data:` (`report.no_fetch`), off the event loop. Each export records
`transfer.timeline` (kept off the project's own feed). The server image installs Pango and fonts for it.
**Moving writing** (`api/moves.py`): `POST /api/tasks/{id}/comments/{cid}/move` (`to_task_id` a card of the
same project, or null for the timeline) and `POST /api/posts/{id}/move` (onto a card). The author or an admin
moves; author, text, mentions, time, edited and `via` come along. Card to card keeps the comment's row
(feed entries follow it: `_enrich` takes a comment's current card); a conversion makes the other kind with
a `created` activity backdated to when it was written, and `comment.moved` records the move (payload
`moved_comment_id` / `moved_post_id`, not `comment_id`/`post_id`, so it isn't shown as the writing itself).
Entries for a comment that no longer exists are dropped.
**Posts** (`project_posts`, `api/posts.py`) are comments on nothing, written straight into the
timeline from the composer above it: `GET/POST /api/projects/{id}/posts`, `PATCH/DELETE
/api/posts/{id}` (operators post; the author or an admin changes one). Markdown, `@mentions`
(`comment.mention` notifications linking `/projects/{id}/dashboard?post=<id>`, so they reach Needs
attention and the Mine filter), pasted images as project attachments, `via` like comments. Feed
entries are `post.created` (kind `comment`, with the post as it is now in `post`); `post.edited` and
`post.deleted` are recorded but hidden, and a deleted post's entry is dropped. Events
`post.created`/`post.updated`/`post.deleted` keep open timelines current. `#hashtags` are read from the
text on save (`posts.hashtags`, outside code and links; `web/src/lib/hashtags.ts` uses the same rule to
link them) into `project_posts.tags`; `GET /api/dashboard/feed?tag=` shows only the posts with one
(whatever the kinds), `GET /api/projects/{id}/post-tags` lists them with counts. Posts are searched
(`type:post`, `tag:`) and travel with project exports. The page refreshes from `activity.created`, `job.updated`,
`playbook_run.updated` and `agent.updated` events.

## UI realtime (`/ws/ui`)

Authenticated by the session cookie, or an API token in the `Authorization` header. The
server sends each domain event as
`{"type":"event","name":"task.moved","project_id":"…","data":{…}}` to the sockets allowed to
see it (see Auth). Event names include `project.updated`, `column.*`,
`task.created|updated|moved|deleted`, `job.updated`, `job.log`, `job.outputs`, `job.parser_proposal`, `job.resize`,
`comment.created`, `reactions.changed` (a comment's or post's reactions), `emojis.changed`, `attachment.created|updated|deleted`, `agent.updated`, `playbook_run.updated`,
`agent_tokens.changed`, `ssh_hosts.changed`, `api_tokens.changed`, `tool.status`, `tools.changed`, `themes.changed`, `summary.updated`,
`project.notes`, `notification.created|read`, `assets.changed`, `alert_channels.changed`,
`settings.changed`, `project.members` and `user.changed`; each section above names its own.
The client keeps page state in sync by applying these to its stores.

## Auth

`POST /api/auth/login` sets an httpOnly `castor_session` cookie (JWT). The same token is
accepted as `Authorization: Bearer`. The first admin is created from
`CASTOR_ADMIN_USERNAME` / `CASTOR_ADMIN_PASSWORD` when the database has no users.

**Single sign-on** (`sso.py`, `saml.py`, `api/sso.py`): OpenID Connect and SAML providers in `sso_providers`,
added by admins in Settings → Sign-on (client secret as a vault token). `GET /api/auth/sso`
lists them for the sign-in page. `…/{id}/start` redirects to the provider with `state`, `nonce`
and a PKCE challenge, kept in a signed `castor_sso` cookie (10 minutes, path `/api/auth/sso`).
`…/{id}/callback` checks the state, swaps the code at the token endpoint (`client_secret_basic`
unless only `_post` is offered), and verifies the `id_token` against the provider's JWKS:
asymmetric algorithms only, issuer, audience, expiry and nonce. It fills in email or groups
from UserInfo if the token lacks them, then `resolve_user` picks the account:
1. A linked `user_identities` row (provider + `sub`).
2. Otherwise, with `link_by_email` and a verified email, the one account with that email.
3. Otherwise a new account with `default_role`.

Allowed email domains (verified emails only) and groups are checked first. With a
`role_map`, the role follows the groups at every sign-in, but the last active admin is never
demoted. On failure the person goes back to `/login?sso_error=…`; on success the usual session
cookie is set and they go to a same-site `next`.

**SAML 2.0** (`saml.py`): a provider with `protocol = "saml"`. Its `issuer` is the identity
provider's entity ID and `sso_providers.saml` holds the rest (`SamlConfig`): sign-on URL, signing
certificates (PEM, any may sign), optional metadata URL, Castor's entity ID (default: its metadata
URL), NameID format, attribute names and `trust_email`. Admins fill it from the IdP's metadata
(`POST /api/sso-providers/saml/metadata`, URL or pasted, read with defusedxml); Castor's own is at
`GET /api/auth/sso/{id}/saml/metadata`. Only SP-initiated sign-in:
1. `…/start` redirects with a deflated AuthnRequest (HTTP-Redirect, unsigned) and a RelayState,
   both remembered with the request ID in the `castor_sso` cookie.
2. The IdP POSTs to `…/{id}/saml/acs`. A cross-site POST doesn't carry the SameSite=Lax cookie,
   so `read_response` checks what it can alone: no DTD (defusedxml first), success status,
   `Destination`, exactly one assertion and no encrypted one, the XML signature (signxml, against
   the stored certificates, assertion or whole response; only `signed_xml` is read afterwards),
   issuer, `Conditions` times and audience, a bearer `SubjectConfirmation` for this ACS with
   `InResponseTo`, and the assertion ID not seen before (in memory). A transient NameID is refused
   unless a subject attribute is set.
3. It redirects to `…/{id}/saml/done?r=…` with the result vault-encrypted (2 minutes). That
   top-level GET carries the cookie: the RelayState and the request ID must match the ones started
   in this browser. Then `resolve_user` as above; the groups attribute (`groups_claim`) sets roles
   and Castor groups only when the assertion has it.

Not supported: encrypted assertions, signed AuthnRequests, IdP-initiated sign-in, single logout.

`users.email` is set by admins only, one account per address, so nobody can point their own
account at someone else's sign-on. `AppSettings.password_login` turns password sign-in off,
but only while a provider is enabled, and only by an admin who has signed in through one.
`castor-admin password-login on` turns it back on.

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
request). `DELETE /api/users/{id}` (admins; not yourself, not the last active admin) deletes an account:
the foreign keys cascade their tokens, sign-on links, memberships and notifications and null them out of
everything else (comments and posts then show "Deleted user"); their id is taken out of card
assignees and alert recipients, their uploaded picture removed, and `user.changed` closes their sockets.

**Companion** (a personal preference): `users.companion` turns it on; `POST/GET/DELETE /api/auth/me/companion`
(session only) uploads, serves (only to its owner) and removes someone's own picture, kept like avatars in
`<data_dir>/companions` (PNG/WebP/GIF/JPEG checked from the bytes, ≤ 3 MB); `users.companion_seat` (0.05–0.95)
is where the picture's seat line is, 0.605 for the built-in one. The template is `web/static/companion/template.png`.

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
* Since: plugins (phases A to E), snapshots, a project's Files, SAML sign-on, comparing runs.

What's next is in [ROADMAP.md](ROADMAP.md).
