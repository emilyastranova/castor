# Actions beyond commands (plan)

Status: **design only; nothing here is built yet.** This is the design the work follows: change it
here first when a decision changes, then build. The roadmap entry ("Actions beyond commands")
points here.

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
  url: "{{ vars.jira.base_url }}/rest/api/3/issue"
  auth: {basic: ["{{ vars.jira.email }}", "{{ vars.jira.token }}"]}
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
  - {name: issue, parser: json, options: {select: "key"}}
```

What happens when it runs:

- **Placeholders and escaping:** placeholders are filled per place: `json` values are put in as
  JSON values (a string stays a string, a list argument becomes a JSON array), the URL path and
  query are URL-encoded, form fields form-encoded, headers checked for newlines. `vars.*` secrets
  are resolved by the runner just before sending, from the vault.
- **The log** on the card shows what an engineer would want and nothing secret: the request line,
  the headers with secret values masked, the size of the body sent, then the response status and
  headers, timing, and the body (truncated over a limit, the whole body kept for parsers).
- **Result:** the response body is the job's stdout, so the existing parsers and field maps read
  it. A response that's a file (by `Content-Type` or `save_as: report.pdf`) is stored as the job's
  artifact. Exit is 0 when `expect` holds, else 1 with the reason (`status 403, expected 201`).
- **Paging and batching:** see [Paging](#paging) below.
- **Server-side safety** (server runner only):
  - requests to link-local, loopback and cloud metadata addresses (169.254.0.0/16, ::1, fd00::/8…)
    are refused unless an admin allows them;
  - `CASTOR_HTTP_ALLOW` (or an app setting) can restrict outbound hosts to a list;
  - redirects are followed only to the same host by default;
  - responses are capped (`CASTOR_HTTP_MAX_BYTES`).

  An agent-run request follows the agent's own network instead; that's the point of choosing one.
- **On an agent:** `job.run` carries `kind: "http"` and the request spec (with variables as today:
  values only in the message, never in a command line). The agent makes the request with stdlib
  `urllib`, so it keeps its single dependency, and streams the same log lines back. Older agents
  don't advertise `http` in their capabilities, so the dispatcher doesn't send them such jobs.

#### Paging

A `paginate` block says how to get the next page and where the records are. The styles below
cover nearly every API:

```yaml
paginate:
  style: cursor            # cursor | link | offset | page
  items: "$.results"       # where each page's records are (JSONPath); default: the body if it's a list
  # cursor: take a value from the response, send it back as a parameter
  cursor: {from: "$.next_cursor", param: cursor}
  # link:   follow a URL in the body ("$.next") or the Link header (rel="next")
  # offset: {param: offset, size_param: limit, size: 100}
  # page:   {param: page, start: 1, size_param: per_page, size: 100}
  stop: {max_pages: 50, max_items: 10000}   # also stops on an empty page or no next cursor
```

- **One result:** the records of every page are joined into one JSON array, and that array is the
  job's stdout. Parsers see a single response, as if the API had returned everything at once.
- **The log** shows one line per page (`page 3: 100 items, 412 ms`), not every body.
- **Progress is saved:** after each page the runner records the cursor or offset it reached in
  `jobs.progress`. A retry continues from there instead of starting over, so a failure on page 40
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
  the URL (Streamable HTTP; SSE for older servers) and auth (a bearer token or headers from the
  vault), with **Test** listing the server's tools.
- **stdio servers** are programs that speak MCP over stdin and stdout, and most published MCP
  servers are this kind. Castor never runs them on the server. An agent hosts them instead:
  - the connection names what to run the way a command does: a tool from the tool library (an
    image or a build recipe, so `npx some-mcp-server` runs in a container with Node in it) or, on
    agents started with `--allow-host`, a host command, plus an agent selector (tags or one agent);
  - its environment comes from a variable set, so API keys reach the program as env vars by name
    (`-e NAME`), never on a command line;
  - the agent starts the program when a call needs it and bridges JSON-RPC between the server and
    the program's stdio (stdlib only: a subprocess and its pipes). It keeps the program running
    between calls for a few minutes (`idle_s`, default 300), then stops it;
  - **Test** and tool listing go through the same agent, so the Settings page shows what the
    program offers before anyone uses it.

  An `mcp` job for a stdio connection is dispatched to a matching agent like a command; one for an
  HTTP connection runs on the server.
- **An `mcp` action** names a connection and a tool:

  ```yaml
  id: shodan-host
  name: Shodan host lookup (MCP)
  kind: mcp
  server: shodan               # a connection's id
  tool: host_lookup
  args:                        # can be generated from the tool's input schema, then edited
    - {name: ip, type: string, required: true}
  arguments: {ip: "{{ ip }}"}  # the tool call's arguments; default: the args as they are
  outputs:
    - {name: host, parser: json}
  ```

- **Arguments:** "New action from an MCP tool" in the Library reads the tool's `inputSchema` and
  writes the `args` for you (types, descriptions, required).
- **Result:** the tool's structured result (or its text content) is the job's stdout, images and
  resources are artifacts, and an error result exits 1 with the message.
- **Untrusted output:** the content another server returns is untrusted. It's shown and parsed
  like job output, never followed as instructions. This is the same rule Castor's own MCP server
  states for job output.

### `plugin`: an action a plugin defines

A plugin manifest's `actions` today are buttons (on a project, card or asset) with a file at most.
This adds **typed actions** that work as library definitions:

```yaml
# castor-plugin.yaml
library_actions:
  - id: hashcat-crack
    name: Crack hashes with hashcat
    args:
      - {name: hashes, type: list, required: true}
      - {name: mode, type: number, default: 1000}
    outputs:
      - {name: cracked, type: list<object>}
```

- **Library entries:** each appears in the library as a definition with `origin: plugin:<id>` and
  `kind: plugin`, read-only like other plugin content. It's usable on cards and as playbook steps
  in projects where the plugin is enabled.
- **Running it:** the runner calls the plugin through its SDK (`ctx.actions.on("hashcat-crack",
  async (input, job) => …)`) with the arguments and resolved variables. `job.log()` streams lines,
  and the return value is the outputs (already typed, so no parser is needed, though `parser` still
  works on a returned string).
- **Limits:** it runs with the plugin's own permissions and network, as its buttons do. A sandboxed
  plugin's action is as contained as the plugin.

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
  scope, kept in the database so a restart doesn't reset them). An agent-run request asks the
  server for room before sending (`job.permit` → `job.permit_ok`), so a credential's limit holds
  across every agent using it.
- **Visible:** a variable set and a plugin show how much of their limit is in use and what's
  waiting.

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
- `tasks` and `jobs`: no new tables. `jobs.kind` (`command` default) and `jobs.request`, the
  request as sent, secrets masked, for the log and for `ai`. `jobs.agent_id` is null for
  server-run jobs; `jobs.runner` says `server`, `agent` or `plugin:<id>`. `jobs.progress` (JSON:
  the page cursor or offset and batches done, for resuming) and `jobs.resume_at` (with a new
  status `waiting`).
- `mcp_connections`: id, name, transport (`http`, `sse`, `stdio`), url and auth (vault tokens) for
  HTTP; for stdio, the tool or host command, arguments, a variable set for its environment, an
  agent selector and `idle_s`. Enabled, last checked, a cache of its tool list.
- `rate_limits`: scope (`variable_set`, `plugin`, `task`, `step`) and the scope's id, the limit
  (count, period, concurrent), and the bucket's state (tokens, updated, paused until). The
  per-step limit lives in the playbook spec and gets its row when the run starts.
- App settings: outbound HTTP allow-list, allow private addresses, response cap, runner slots.

## UI

- **Library:** the Library page shows each definition's kind as an icon and a facet (command, HTTP,
  MCP, plugin). A definition's detail pane shows its request (HTTP) or its server and tool (MCP),
  and has a **Try it** that runs it once, outside any card, against arguments you give: the parser
  playground, for actions. "New" offers *Command*, *HTTP request*, *From an MCP tool*.
- **Cards:** a card using an HTTP or MCP action looks like a command card: arguments, variables,
  Run. In place of the shell preview, "Will run" shows the request (method and URL, folded like the
  command preview). The agent picker shows "Castor server" for server-run kinds, plus agents for
  HTTP.
- **Playbooks:** the palette lists actions of every kind together, filterable by kind, and steps
  look the same in the graph apart from their icon.
- **Settings → MCP servers** (admins) and the outbound HTTP settings under Settings → General.

## Phases

1. **Foundations + HTTP on the server:**
   - `kind` in definitions;
   - the server runner, dispatcher routing and cancel/timeout;
   - HTTP actions with escaping, vault secrets, the log, `expect`, artifacts, outbound safety
     (applied to alert channels too);
   - paging, batching and resuming from `jobs.progress`;
   - rate limits on credentials and cards, the fixed retry rules, the `waiting` status;
   - Library and card UI;
   - a few built-in examples: a generic webhook, Jira issue, Slack message, ntfy, and one paged
     API (GitHub's issues or a scanner's findings).
2. **HTTP on agents and AI recovery:** `job.run` for `http`, the agent capability and the agent
   picker, `job.permit`; `on_failure: ai` with the recovery moves.
3. **MCP:** HTTP connections on the server, stdio connections hosted by agents, tool listing,
   `mcp` actions, "from an MCP tool", result handling.
4. **Plugin actions:** the manifest field, the SDK handler, the runner bridge, plugin rate
   limits, castor-plugins examples.
5. **Approvals and polish:** `confirm: true`, MCP tools for managing connections and limits.

Each phase ships on its own and keeps commands exactly as they are.

## Open questions

- **A stdio server shared by projects.** A warm stdio program on an agent holds whatever state it
  keeps between calls. Should a connection be limited to projects (like agents are), and should
  two projects' calls ever share one running program, or does each project get its own?
- **Huge paged results.** Joining every page into one array is simple, but 200,000 records is a
  lot of stdout. Past some size, should the runner write JSON lines to an artifact and point the
  parsers at it?
- **Credential limits in exports.** A variable set's rate limit travels with it in an export, but
  its bucket state shouldn't. Confirm that's the right split.

Settled and moved elsewhere: incoming webhooks belong to "Triggers beyond assets" on the roadmap
and need authentication; they're on the back burner.
