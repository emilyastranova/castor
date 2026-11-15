# Actions beyond commands (plan)

Status: **all five phases are built**: HTTP actions the Castor server sends (with paging, batches,
rate limits, retries and waiting), sent through an agent's network when a card chooses one, AI
recovery moves for playbook steps, MCP actions (tools on other MCP servers, over HTTP or as stdio
programs agents run), plugin actions (a plugin's library actions, on cards and as steps), and
approvals (`confirm: true`). The format is in [commands/README.md](../commands/README.md#http-actions-kind-http)
and how it works in [ARCHITECTURE.md](ARCHITECTURE.md#http-actions). What's left is listed under each phase.
This is the design the work follows: change it here first when a decision changes, then build.
The roadmap entry ("Actions beyond commands") points here.

Today everything a card or a playbook step does is a shell command run by an agent. That reaches
any tool installed on a box, but not the systems Castor most often needs to talk to, which have
APIs: a ticketing system, a chat tool, a cloud scanner, another assistant's tools. This plan gives
Castor other kinds of action, and keeps everything that already works for commands working for
them.

## Decisions

1. **An action is still a job.** A run of any kind is a `jobs` row with its log, status, exit,
   parsed outputs and artifacts, on its card. So everything built around jobs keeps working
   unchanged:
   - cancel and timeouts;
   - parsers and results, assets and triggers;
   - playbook steps with bindings, `on_failure`, retries and decisions;
   - alerts, AI summaries, Changes comparison, Files, and the activity feed.

   No parallel system, no second results model.
2. **A kind is part of a library definition.** A command definition gains `kind`: `command` (today,
   the default), `http`, `mcp` or `plugin`. A card picks a library definition the same way it picks
   a command today, fills its arguments and binds its variables; playbook steps name it with
   `command_ref` as now. The word on screen stays "command" for `kind: command` and becomes
   "action" for the family.
3. **Where it runs is the job's runner.** `command` jobs run on an agent (or SSH host), as now.
   `http` jobs run on the Castor server by default, or on an agent when the card or step chooses
   one (to reach a service only inside the network the agent is in). `mcp` jobs run on the server.
   `plugin` jobs run in the plugin's process (its sandbox).
4. **The same rules as commands:**
   - **Arguments** are typed and checked by the same `ArgSpec`s.
   - **Secrets** come only from vault variables. They're filled in at the moment the request is
     made, never stored on the card or job, and masked in logs as today.
   - **Permissions:** permission and scope checks, `access.py` and API token rules apply as for
     running a command; only operators and admins write library definitions.
   - **Activity:** runs record activity like jobs.
5. **Nothing renders a value into something it can escape from.** A command's values are
   shell-quoted; an HTTP action's are escaped for where they land: JSON string, URL component, form
   field or header value. This is the rule `alerts.render` already follows. No action kind runs a
   shell.
6. **Paging is part of phase 1.** Most APIs worth calling page their results, so an HTTP action
   that can only read the first page isn't useful.
7. **Rate limits are Castor's to keep, not the API's to enforce.** Limits can be set per API
   credential, per plugin and per card or step. A request waits for room under every limit that
   applies instead of failing (see [Rate limits](#rate-limits)).
8. **Failed calls recover in layers** (see [When a call goes wrong](#when-a-call-goes-wrong)):
   - fixed rules first: `Retry-After`, backoff on 5xx, resuming from the page it stopped at;
   - then, if the step allows it, the AI picks from a short list of moves: wait and try again,
     smaller pages or batches, or ask a person. It never writes a request of its own.
9. **Alert channels stay separate from HTTP actions.** An alert is a notification about a run,
   configured by admins, with presets and a test button. An action is work on a card, with
   arguments, results and a place in playbooks. They share code, not a model: `render`, the
   outbound address guard (alert channels get it too, which they lack today), and the rate
   limiter. A person who wants an alert to do real work can point an alert at a playbook trigger
   once triggers fire on run results.

## The kinds

### `http`: a request

```yaml
id: jira-create-issue
name: Create a Jira issue
schema_version: 1
kind: http
description: Open an issue in a Jira project
tags: [ticketing]
args:
  - {name: project, type: string, required: true, example: SEC}
  - {name: summary, type: string, required: true}
  - {name: description, type: string}
vars:
  - {name: jira, kind: jira-api}          # a variable set kind: base_url, email, token (secret)
request:
  method: POST
  url: "{{ jira.base_url }}/rest/api/2/issue"
  auth: {basic: ["{{ jira.email }}", "{{ jira.token }}"]}
  headers: {Accept: application/json}
  json:                                   # or body + content_type; form: {...}
    fields:
      project: {key: "{{ project }}"}
      summary: "{{ summary }}"
      description: "{{ description }}"
  timeout_s: 30
expect:
  status: [201]                           # default: any 2xx
outputs:
  - {name: issue, parser: json-value, options: {path: key}}
```

What happens when it runs:

- **Placeholders and escaping:** placeholders are filled per place: `json` values are put in as
  JSON values (a string stays a string, a list argument becomes a JSON array), the URL path and
  query are URL-encoded, form fields form-encoded, headers checked for newlines. Variable fields
  (`{{ jira.token }}`) are resolved by the runner just before sending, from the vault, in the same
  pass as the arguments, so an argument's text is never read again for placeholders.
- **The log** on the card shows what an engineer would want and nothing secret: the request line,
  the headers with secret values masked, the size of the body sent, then the response status and
  headers, timing, and the body (truncated over a limit, the whole body kept for parsers).
- **Result:** the response body is the job's stdout, so the parsers and field maps read it
  (`json-records` and `json-value` are built for this). A response that's a file (by `Content-Type` or `save_as: report.pdf`) is stored as the job's
  artifact. Exit is 0 when `expect` holds, else 1 with the reason (`status 403, expected 201`).
- **Paging and batching:** see [Paging](#paging) below.
- **Server-side safety** (server runner only):
  - requests to loopback, link-local (cloud metadata), multicast and reserved addresses are
    refused unless an admin lists them (Settings → General, or `CASTOR_HTTP_ALLOW_ADDRESSES`); the
    check is on the resolved address at connect time, so DNS can't be used to slip past it;
  - private networks can be blocked too, and HTTP actions held to a list of hosts (Settings →
    General); alert channels follow the same address rules;
  - redirects are followed only on the same host, each hop checked;
  - responses are capped (`CASTOR_HTTP_MAX_BYTES`).

  An agent-run request follows the agent's own network instead; that's the point of choosing one.
- **Through an agent** (decided while building phase 2, replacing the first plan of the agent
  running the whole job): the server still runs the job (paging, retries, rate limits, progress,
  parking, AI recovery) and the agent only makes each request from its network, as a proxy:
  `http.request` out, `http.chunk`s and `http.response` back. The agent stays small (stdlib
  `urllib`, no redirects followed, its own proxy settings), rate limits need no permits because
  they're counted where the requests are decided, and an agent that disconnects only parks the
  job until another is connected. Agents that don't advertise `http` (older ones, `--no-http`, SSH
  hosts) aren't used. The variable values go to the agent inside the request, as they do in a
  command's `job.run`.

#### Paging

A `paginate` block says how to get the next page and where the records are. The styles below
cover nearly every API:

```yaml
paginate:
  style: cursor            # cursor | link | offset | page
  items: results           # where each page's records are (a dot path); default: the body if it's a list
  # cursor: take a value from the response, send it back as a parameter
  cursor: {from: meta.next_cursor, param: cursor}
  # link:   follow a URL in the body (next: links.next) or the Link header (rel="next")
  # offset: {param: offset, size_param: limit, size: 100}
  # page:   {param: page, start: 1, size_param: per_page, size: 100}
  stop: {max_pages: 50, max_items: 10000}   # also stops on an empty page or no next cursor
```

- **One result:** the records of every page are joined into one JSON array, and that array is the
  job's stdout. Parsers see a single response, as if the API had returned everything at once.
- **The log** shows one line per page (`page 3: 100 items, 412 ms`), not every body.
- **Progress is saved:** after each page the runner records the cursor or offset it reached in
  `jobs.progress`, and the records so far in `<data_dir>/actions/<job id>.jsonl`. A retry continues from there instead of starting over, so a failure on page 40
  doesn't cost the first 39 again.
- **Pages count against limits:** each request waits for the rate limits like any other.
- **Link-style next URLs** must stay on the same host as the first request. A next link pointing
  somewhere else is refused rather than followed.

**Batching** is the reverse problem: a list argument with more values than the API takes in one
request (500 hosts against an API that takes 100 per call).

```yaml
batch: {arg: hosts, size: 100}   # one request per 100 values; results joined like pages
```

The batch size is the knob the AI may turn down on a struggling API (see below). Batches are
saved to `jobs.progress` the same way pages are.

### `mcp`: a tool on another MCP server

Castor already *is* an MCP server. This makes it a client too.

- **Connections:** **Settings → MCP servers** (admins) lists the servers Castor may call: a name,
  an id (what actions name), the URL (Streamable HTTP, or the older HTTP+SSE transport of
  protocol 2024-11-05, whose message endpoint must be on the same origin as the URL), headers, and a variable set whose fields fill `{{ field }}` in the headers (or a stdio
  program's environment) when it's used, so the token stays in the vault. **Check** connects and
  reads the server's name and tools. A connection can be limited to projects, like agents; a stdio
  one must be (it names its projects), and each project gets its own running program, so one
  project's calls never share a process, or what it keeps between calls, with another's.
- **stdio servers** are programs that speak MCP over stdin and stdout, and most published MCP
  servers are this kind. Castor never runs them on the server. An agent hosts them instead:
  - the connection names what to run the way a command does: a tool from the tool library (an
    image or a build recipe, so `npx some-mcp-server` runs in a container with Node in it) or, on
    agents started with `--allow-host`, a host command, plus an agent selector (tags or one agent);
  - its environment (`NAME={{ field }}`) comes from the connection's variable set, so API keys
    reach the program as env vars by name (`-e NAME`), never on a command line;
  - the agent starts the program when a call needs it and bridges JSON-RPC between the server and
    the program's stdio (stdlib only: a subprocess and its pipes): `mcp.start`, `mcp.send` and
    `mcp.stop` in, `mcp.started`, `mcp.message` and `mcp.exited` out. One program per connection
    and agent stays running between calls, shared by them (each call has its own request ids), for
    `idle_s` (default 300) without use;
  - **Test** and tool listing go through the same agent, so the Settings page shows what the
    program offers before anyone uses it.

  The server still runs every MCP job (rate limits, retries, the log, results): for a stdio
  connection it picks a connected agent that can run the program (one already running it first)
  and talks to the program through it. If the program stops mid-call the call is retried, and the
  agent starts it again.
- **An `mcp` action** names a connection and a tool:

  ```yaml
  id: shodan-host
  name: Shodan host lookup (MCP)
  kind: mcp
  args:                          # can be generated from the tool's input schema, then edited
    - {name: ip, type: string, required: true}
  mcp:                           # a block of its own: commands already use a top-level `tool:`
    server: shodan               # a connection's id
    tool: host_lookup
    arguments: {ip: "{{ ip }}"}  # the tool call's arguments; default: the args as they are
    timeout_s: 120
  outputs:
    - {name: host, parser: json-records}
  ```

- **Arguments:** "New action from an MCP tool" in the Library reads the tool's `inputSchema` and
  writes the `args` for you (types, descriptions, required).
- **Result:** the tool's structured result (or its text content) is the job's stdout, images,
  audio and resource files are artifacts, links are logged, and an error result fails the job with
  what the tool said. The server's log messages and progress notifications go to the console as
  they arrive.
- **Untrusted output:** the content another server returns is untrusted. It's shown and parsed
  like job output, never followed as instructions. This is the same rule Castor's own MCP server
  states for job output.

### `plugin`: an action a plugin defines

A plugin manifest's `actions` today are buttons (on a project, card or asset) with a file at most.
This adds **typed actions** that work as library definitions:

```yaml
# castor-plugin.yaml
library_actions:
  - id: crack
    name: Crack hashes
    args:                        # the command format's args, vars and outputs
      - {name: hashes, type: list, required: true}
      - {name: rounds, type: number, default: 3}
    vars:
      - {name: creds, kind: credential, required: false}
    outputs:                     # read from what the handler returns, as a command's stdout
      - {name: cracked, parser: json-records, options: {records: cracked}}
    timeout_minutes: 30
    rate_limit: 10/minute        # a suggestion; admins set the plugin's own limit
```

- **Library entries:** each is written into the library as `<plugin id>-<action id>` with
  `kind: plugin` and `origin: plugin:<id>`, read-only like other plugin content, when the plugin is
  installed (as generated pack items, so updates, conflicts and removal work as for packs; a
  removed plugin's cards become manual cards). It's usable on cards and as playbook steps in
  projects where the plugin is enabled.
- **Running it:** the server runner calls the plugin's handler, the same `ctx.actions.on(id, …)`
  as a button action, with `run.args` (the card's values), `run.vars` (the bound variable sets'
  values, `{slot: {field: value}}`: the plugin sees secrets, as a job does), `run.job` and the
  settings of the person who started the card. `run.log` and `run.progress` go to the card's
  console as they come; `run.signal` is aborted when the job is cancelled.
- **Outputs (decided while building):** the handler returns `{message, output}`, and `output`
  (text, or anything JSON) is the job's output, read by the definition's parsers like a command's
  stdout, rather than typed values of its own: one model for results, and `json-records` or
  `json-value` make it one line.
- **Limits:** it runs with the plugin's own permissions and network, as its buttons do. A sandboxed
  plugin's action is as contained as the plugin. Admins can cap how often a plugin's library actions
  run (`plugins.actions_rate_limit`, on its page), on top of cards' limits.

## How the existing machinery treats them

| Thing | Command | HTTP / MCP / plugin |
| --- | --- | --- |
| Where | an agent's container or shell | HTTP: the server or an agent; MCP: the server (HTTP servers) or an agent (stdio servers); plugin: its process |
| Queue | dispatcher matches an agent with a free slot | dispatcher hands it to the server runner (a bounded pool, `CASTOR_SERVER_RUNNER_SLOTS`), or an agent for agent-run HTTP |
| Cancel | `killpg` on the agent | cancel the request or call (asyncio task cancel); a plugin gets `job.cancelled` |
| Timeout | `timeout_s` | `timeout_s` (default 60 s for HTTP/MCP) |
| Output | stdout/stderr, artifacts | response body / tool result as stdout; files as artifacts |
| Parsers | `outputs` with `source` (file) or stdout | `outputs` read stdout (HTTP/MCP); plugin outputs are typed |
| Variables | env and files under `.castor/` | values filled into the request at send time; never on the job |
| `on_failure` | retry / continue / skip / ask / ai | the same, after the fixed retry rules; `ai` chooses among recovery moves (below) |
| Rate limits | none | per credential, plugin, and card or step; requests wait for room |
| Resume | starts over | continues from the saved page or batch |
| Approvals | `on_failure: ask` | the same, plus optional `confirm: true`: a person approves each run (for actions with side effects outside Castor, e.g. "post to the customer's Slack") |
| Interactive | `tty` | not applicable |

Running every server-side kind through one runner (`orchestrator/server_runner.py`) means the
dispatcher stays the one place that decides what runs where, and the gateway's job handlers
(`job.started`, `job.log`, `job.finished`) are reused by having the runner speak the same messages
in-process. `SshSocket` already does exactly this for SSH hosts.

## Rate limits

A limit is a number of requests per period (`60/minute`, `5/second`, `10000/day`), optionally with
a cap on how many run at once (`concurrent: 2`). Limits are set in three places:

| Scope | Set where | Covers |
| --- | --- | --- |
| **API credential** | on the variable set that holds the token (Variables → the set → Rate limit) | every request made with that set's values, by any card, playbook or project. This matches how APIs count: per key |
| **Plugin** | the plugin's page in Settings → Plugins (admins) | every action and every outbound request the plugin's actions make |
| **Card or step** | the card's action settings, or a playbook step's `rate_limit` | that card's or step's own requests, so one big run doesn't eat a shared key's whole budget |

A definition can carry a suggested limit (`rate_limit: 100/minute` for an API known to allow
that), filled in as the default when someone first binds a variable set to it.

- **Every limit that applies must have room.** A request in a card using the `jira` set, run by
  a plugin's action, waits for the card's limit, the set's and the plugin's.
- **Waiting is not failing.** A request without room waits, and the job's log says so
  (`waiting 12 s for the jira rate limit`). A job that would wait longer than its `timeout_s`
  allows fails with that reason instead of hanging.
- **The API's own word wins.** A `429` or `503` with `Retry-After`, or `X-RateLimit-Remaining: 0`
  with a reset time, pauses everything under that credential until then, not only the request
  that hit it.
- **One counter for the whole instance.** Limits are kept on the server (a token bucket per
  scope, in `rate_buckets` so a restart doesn't reset them). Requests sent through agents are
  decided on the server too, so a credential's limit holds across every agent using it.
- **Visible:** a variable set's and a plugin's dialog shows how much of their limit is in use now
  (room left, requests in flight, a pause the API asked for; `GET /api/variables/sets/{id}/usage`,
  `GET /api/plugins/{id}/usage`, MCP `variable_set_usage`, `plugin_usage`). A waiting request also
  says so in its card's log.
- **In exports:** a variable set's limit (the setting) travels with it; the counter doesn't, so the
  importing instance starts with the full limit.
- **Large paged results:** past 20,000 records or 8 MB, the joined records are kept as a
  `records.jsonl` artifact (one record per line) instead of one array on stdout; the console shows
  the first 20, `jobs.progress.records_file` sends the output's parsers to the file, and
  `json-records` reads JSON lines.

## When a call goes wrong

Recovery goes from cheap and certain to slow and judged.

1. **Fixed rules, always on:**
   - `429`/`503` with `Retry-After`: wait that long, then try the same request;
   - `5xx`, a timeout or a dropped connection: retry with backoff (`retry: {attempts: 3,
     backoff: 2s..60s}` on the definition; the defaults are 3 attempts from 2 s);
   - `4xx` other than 429: no retry; the request itself is wrong;
   - a retry resumes from the saved page or batch.
2. **The step's `on_failure`**, once the fixed rules have run out: `retry`, `continue`, `skip`,
   `ask` and `ai`, as for commands.
3. **`ai`: recovery moves.** The AI gets what an engineer would look at, with secrets masked:
   - the request as sent, the status, headers that matter (`Retry-After`, `X-RateLimit-*`), the
     start of the body;
   - progress (pages or batches done, of how many);
   - the history of earlier attempts in this run.

   It answers with one move from a fixed list, and Castor carries the move out:

   | Move | What it does |
   | --- | --- |
   | `wait` | wait N seconds (capped by the step, default 15 minutes), then resume |
   | `smaller` | lower the page size or batch size (never below 1, never above the original), then resume |
   | `slower` | lower this card's rate limit for the rest of the run |
   | `skip_batch` | skip the batch that keeps failing, record which values it held, and continue |
   | `ask` | hand it to a person with the AI's explanation |
   | `fail` | stop, with the explanation |

   The AI can't change the URL, the method or the body, and it can't add requests. It only turns
   the knobs the definition already has. Its reason goes in the log and the activity feed like
   any other resolver decision. A step limits how many moves it may take (`max_retries`).
4. **Waiting doesn't hold a slot.** A job waiting on a limit or a `wait` move sets status
   `waiting` with a `resume_at`. The server runner frees its slot and the dispatcher picks the
   job up again when the time comes, so one slow API doesn't stall every other action.

The same layers apply to MCP calls (a tool error result that says it's rate limited, a server
that times out) and to plugin actions, which can return `{retry_after: 30}`.

## Data model

- `command_definitions`: the YAML gains `kind`, and `request` (http), `server`/`tool`/`arguments`
  (mcp) or nothing (plugin). The spec models live in `action_spec.py`, with `command_spec.py` as the
  `command` kind.
- `tasks` and `jobs`: no new tables. `jobs.kind` (`command` default) and `jobs.request` (the
  resolved arguments and the request part of the definition, fixed when the job first starts;
  never a variable value). `jobs.agent_id` is null for server-run jobs; `jobs.runner` says
  `server` (later `plugin:<id>`). `jobs.progress` (JSON: the page cursor or offset and batches
  done, for resuming) and `jobs.resume_at` (with a new status `waiting`). `tasks.rate_limit` and
  `variable_sets.rate_limit` hold the limits.
- `mcp_connections`: id, name, transport (`http`, `sse`, `stdio`), url and auth (vault tokens) for
  HTTP; for stdio, the tool or host command, arguments, a variable set for its environment, an
  agent selector and `idle_s`. Enabled, last checked, a cache of its tool list.
- `rate_buckets`: one row per limit in use (`set:<id>`, `task:<id>`; later `plugin:<id>`): tokens,
  when it was last filled, and paused until (a Retry-After). A playbook step's card is a card, so
  its limit is the card's.
- App settings: outbound HTTP allow-list, allow private addresses, response cap, runner slots.

## UI

- **Library:** the Library page shows each definition's kind as an icon and a facet (command, HTTP,
  MCP, plugin). A definition's detail pane shows its request (HTTP) or its server and tool (MCP),
  and has a **Try it** tab that sends it once in a project you pick, with arguments and credentials
  you give (`POST /api/commands/{id}/try`, MCP `try_action`): it runs on a card made for it and
  archived at once (limits, approvals and masked secrets as for any card), and the pane shows the
  run live with its parsed results. The card stays under Archived cards for the record. "New" offers *Command*, *HTTP request*, *From an MCP tool*.
- **Cards:** a card using an HTTP or MCP action looks like a command card: arguments, variables,
  Run. In place of the shell preview, "Will run" shows the request (method and URL, folded like the
  command preview). The agent picker shows "Castor server" for server-run kinds, plus agents for
  HTTP.
- **Playbooks:** the palette lists actions of every kind together, filterable by kind, and steps
  look the same in the graph apart from their icon.
- **Settings → MCP servers** (admins) and the outbound HTTP settings under Settings → General.

## Phases

1. **Foundations + HTTP on the server** (built):
   - `kind` in definitions;
   - the server runner, dispatcher routing and cancel/timeout;
   - HTTP actions with escaping, vault secrets, the log, `expect`, artifacts, outbound safety
     (applied to alert channels too);
   - paging, batching and resuming from `jobs.progress`;
   - rate limits on credentials and cards, the fixed retry rules, the `waiting` status;
   - Library and card UI;
   - built-in examples: `webhook-post`, `slack-message`, `ntfy-send`, `jira-create-issue`,
     `github-issues` (paged); variable kinds `webhook` and `jira-api`.

   Later (built): **Try it** in the Library, a step's own `rate_limit`, and usage shown on variable
   sets and plugins.
2. **HTTP on agents and AI recovery** (built): agents as a proxy (`http.request`, the `http`
   capability, `--no-http`), "Sent from" on the card (the server, an agent or a tag),
   `jobs.via_agent_id`; `on_failure: ai` with the recovery moves (wait, smaller, slower,
   skip_batch, ask, fail; `ai.max_wait_s`). A playbook step is sent from the server unless it says
   `send_from: {tags: [...]}` (or `{agent_id: ...}`).
3. **MCP** (built): HTTP connections on the server (Streamable HTTP), stdio connections hosted by
   agents (`mcp_stdio` capability), Settings → MCP servers with Check and the tool list, `mcp`
   actions, "From an MCP tool" in the Library, result handling. Later (built): the older HTTP+SSE
   transport, MCP servers in exports (`library/mcp_servers.json`, brought along by the MCP actions
   exported; admins import them), stdio servers limited to projects with a program per project, and
   MCP tools to manage connections.
4. **Plugin actions** (built): `library_actions` in the manifest, `run.args`/`run.vars`/
   `run.job`/`run.signal` and `{message, output}` in the SDK, the runner bridge (`_call_plugin`, log
   and progress forwarded through `plugins.job_runs`), the plugin's rate limit, and the
   `shodan-enrich` example (a "Shodan host lookup" library action, in its 1.2.0, for Castor 1.4).
5. **Approvals and polish** (built): `confirm: true` on a definition, or "Ask before each run" on any
   card (`tasks.confirm`): its jobs start in status `approval`; the project is told (the bell,
   Home's needs-attention, alert channels that take approvals) and an operator approves (queued,
   `jobs.approved_by`) or rejects it (cancelled, with the reason) on the card, through
   `POST /api/jobs/{id}/approve|reject` or MCP (`approve_job`, `reject_job`). A run on several
   agents is decided together; a playbook step waits as queued, and a rejected one ends cancelled.
   `app_settings.approval_by_someone_else` makes it a two-person rule. MCP tools for MCP servers
   (`list_mcp_servers`, `add_mcp_server`, `check_mcp_server`, `draft_mcp_action`…); limits already
   had theirs (`update_card`, `update_variable_set`). The leftovers of earlier phases are built too.

Each phase ships on its own and keeps commands exactly as they are.

## Open questions

None left. Settled: a stdio server names its projects and each project gets its own program; large
paged results become a JSON-lines artifact; a set's limit travels in exports but its counter
doesn't. Moved elsewhere: incoming webhooks belong to "Triggers beyond assets" on the roadmap
and need authentication; they're on the back burner.
