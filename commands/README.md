# Command definitions

Reusable commands with typed arguments. Files here are loaded into the command library as
read-only built-ins when the server starts; you can also create your own in the UI
(Commands page) using the same format. Cards pick a command and fill in its arguments.
Later in Phase 2 these become playbook blocks, with `outputs` wired into the next block's
`args`.

Every argument value is inserted **shell-quoted**, so a value is always a single word: a
target like `10.0.0.0/24; id` is passed to the tool literally, never run. List arguments are
inserted as one word per item, or as one joined word when `separator` is set (e.g. `","`).
Each job runs in its own work directory. Write files there as `{{ work }}/name`: it is `/work` in
a container and the job's own directory in a host shell or on an SSH host (it renders as
`"${CASTOR_WORK}"`, which every runtime sets), so never write a literal `/work`. Files listed as
output `source`s and anything under `{{ work }}/artifacts/` are uploaded back to the card when the
job finishes. `work` can't be an argument name.

```yaml
id: nmap-ping-sweep          # unique, kebab-case
name: Nmap ping sweep
schema_version: 1
description: Discover live hosts in a range
tags: [network, discovery]
tool: nmap                             # optional: a tool from tools/ or the Tools page
# image: docker.io/instrumentisto/nmap # or a container image directly (not both)
container_flags:                     # optional: extra flags passed to docker/podman run
  - --privileged
  - --network=bridge
args:
  - name: targets
    type: string                 # string | number | boolean | list
    description: CIDR range or host list
    required: true
    example: 192.168.0.0/24
    regex: '^[0-9a-zA-Z./,:-]+$' # optional validation (full match)
    # separator: ","               # list args only: join items into one word
command: nmap -sn -oX {{ work }}/ping-sweep.xml {{ targets }}   # placeholders are filled from args
outputs:                          # files collected from the work directory after the job
  - name: hosts
    parser: nmap.hosts            # turns the file into a typed value (see below)
    type: list<host>              # optional; defaults to the parser's type
    source: "{{ work }}/ping-sweep.xml"  # path or glob in the work directory (quote it: YAML); omit for stdout
    options:                      # optional parser options
      include_down: false
```

When the job finishes, each output with a `parser` is parsed and shown under **Results** on
the card. A parse failure is shown there too and doesn't fail the job. An output with only a
`source` is just collected as a file.

| Parser | Type | Reads | Options |
| --- | --- | --- | --- |
| `nmap.hosts` | `list<host>` `{ip, hostnames, status}` | nmap `-oX` XML | `include_down` (default true) |
| `nmap.services` | `list<service>` `{host, port, proto, state, name, product, version}` | nmap `-oX` XML | `include_closed` (default false: open ports only) |
| `ffuf.results` | `list<url>` `{url, status, length, words, lines}` | ffuf `-of json` | `status`: list of codes to keep |
| `lines` | `list<string>` | any text, one value per non-empty line | `match` (regex filter), `ignore_case`, `unique` |
| `regex` | `list<string>` | any text | `pattern` (required), `group` (number or name, default 0), `ignore_case`, `unique` (default true) |
| `text` | `string` | any text | |
| `json-records` | `list<object>` | JSON: an array, or the array at a dot path (an API's response) | `records` (dot path, e.g. `results` or `data.items`) |
| `json-value` | `string` | JSON: one value by dot path (a new issue's key) | `path` (required, e.g. `key` or `data.0.id`) |

The **Parsers** page in the UI lists these with their options and lets you try each one on
built-in sample data or your own pasted output, with the result updating as you type. It
also writes the `outputs:` entry for you to paste here. Options are checked when a command
is saved, so a typo or an invalid pattern is reported then rather than after a run.

## Variable sets (credentials, keys, listeners, ...)

A command that needs a credential, key file, listener address or similar declares a
**slot** of a variable *kind* instead of an argument. The card (or playbook step) then picks
a *set* of that kind from the Variables page.

```yaml
vars:
  - name: creds              # lowercase; used as {{ creds.<field> }}
    kind: credential         # credential, ssh-key, listener, api-token, or your own kind
    description: Account to log in with
    required: true           # default; false lets the card leave it empty
command: nxc smb {{ targets }} -u {{ creds.username }} -p {{ creds.password }}
```

- `{{ slot.field }}` renders as `"${CASTOR_VAR_SLOT_FIELD}"`, an environment variable the
  agent sets when the job runs. The command line itself never contains a value, so secrets
  never appear in it, in the card, or in the job history.
- File fields (e.g. `ssh-key.private_key`) are written into the job directory with mode 0600
  and the variable holds the file's path, so `-i {{ key.private_key }}` works.
- Any characters in a value are safe, including quotes, `$`, backticks, `$(…)` and `*`: the
  shell parses the command line before it expands `"${CASTOR_VAR_…}"`, and never re-parses
  the expanded value. Don't switch to single quotes, though: `'${CASTOR_VAR_…}'` isn't
  expanded at all, so the tool would receive the variable's name instead of its value.
- **Don't put a placeholder inside quotes**: it already brings its own double quotes, so
  `"user {{ creds.username }}"` leaves the value unquoted. Inside a quoted string, use the
  variable directly: `"user $CASTOR_VAR_CREDS_USERNAME"`.
- Optional fields can be used conditionally with shell expansion, e.g.
  `${CASTOR_VAR_CREDS_DOMAIN:+-d "$CASTOR_VAR_CREDS_DOMAIN"}`.
- Secret values that the tool prints are masked as `••••` in the card's output.
- Variable files are owned by the agent's user. An image that runs as a different non-root
  user can't read a 0600 file; run it as root or adjust with `container_flags`.

Every set dropdown (on the card, in New card, and in the playbook Run dialog) also offers
**Enter manually…**: type the values for just this card or run, optionally saving them to
Variables. Unsaved manual values are encrypted like any set but stay with that card or run
and are deleted with it.

Saving a command checks that every slot's kind exists and every `{{ slot.field }}` names a
field of that kind. `netexec-smb-auth` and `netexec-shares` are real examples.

## Dropdowns from a variable set (`choices_from`)

An argument can take its dropdown options from a variable set instead of a fixed `choices`
list, so editing the set updates every command that uses it:

```yaml
args:
  - name: protocol
    required: true
    default: smb
    choices_from: { set: NetExec protocols, field: options }
command: nxc {{ protocol }} {{ targets }}
```

The field must be a text or list field (the built-in **Choice list** kind has one `options`
list). The options are read whenever the command is shown, rendered or validated; a value
outside them is rejected like any `choices`. Saving the command checks that the set and
field exist. A "NetExec protocols" set is created once on first start; edit or delete it
like any set. `netexec-auth` uses it.

Kind fields can also have `choices`: a text field becomes a dropdown and a list field a set
of toggles when filling in a set.

## Optional flags (`flag`) and toggles (`show_if`)

`flag:` turns an argument into a command-line option that appears only when it has a value:

```yaml
args:
  - name: use_dns
    type: boolean
    description: Specify DNS server
  - name: dns_server
    flag: --dns-server   # --dns-server '10.0.0.53' when filled, nothing when empty
    show_if: use_dns     # only shown (and used) while use_dns is on
    required: true       # required only while shown
  - name: kerberos
    type: boolean
    flag: -k             # -k when on, nothing when off
  - name: port
    type: number
    flag: --port=        # one word: --port='445'
command: nxc ldap {{ targets }} {{ dns_server }} {{ kerberos }} {{ port }}
```

- On a text, number or list argument the flag precedes the quoted value; an empty value
  renders nothing (so an optional field needs no toggle of its own).
- On a boolean the flag renders when on and nothing when off (without `flag`, a boolean
  renders the word `true` or `false`).
- `show_if` names a boolean argument of the same command (one level, no chains). The card
  form hides the argument while the toggle is off, and the server ignores its value then,
  so a value left over from before can't sneak into the command.

## Target lists

Any text or list argument can take a saved **target list** instead of typed values: on a
card (or in New card / the playbook Run dialog), click the list button at the end of the
field to pick a list, or to import a file or pasted text with a live preview of how it
parses (separator, duplicates, comments, and what each entry looks like: IP, CIDR, range,
domain/host, URL). Lists live on the Variables page ("New target list"), so "Lab network",
"Lab exclusion list" or a single "Test computer" are managed in one place.

A picked list is **linked**: the card stores `{"list": "<id>"}` and the entries are filled in
each time the card's command is rendered or the card runs, so editing the list updates
every card that uses it. A list argument gets the entries as separate values (nmap gets
one target per argument); a text argument gets them comma-separated. Each entry is still
checked against the argument's `regex`. The nmap commands take an `exclude` list for
exclusion lists.

## HTTP actions (`kind: http`)

A definition can make an HTTP request instead of running a command: the **Castor server** sends
it (no agent, no container), and the response comes back on the card like a command's output.
Use it for ticketing systems, chat webhooks, scanners' APIs and anything else with an API.
`webhook-post`, `slack-message`, `ntfy-send`, `jira-create-issue` and `github-issues` are the
built-in examples. The design and the plans for MCP and plugin actions are in
[docs/ACTIONS.md](../docs/ACTIONS.md).

```yaml
id: jira-create-issue
name: Create a Jira issue
kind: http                       # the default is command
args:
  - {name: project, type: string, required: true, regex: '^[A-Z][A-Z0-9_]+$'}
  - {name: summary, type: string, required: true}
  - {name: labels, type: list}
vars:
  - {name: jira, kind: jira-api} # base_url, email, token (secret)
request:
  method: POST                   # GET (default), POST, PUT, PATCH, DELETE, HEAD
  url: "{{ jira.base_url }}/rest/api/2/issue"
  query: {notifyUsers: "false"}  # optional; values URL-encoded, a list repeats the parameter
  headers: {Accept: application/json}
  auth: {basic: ["{{ jira.email }}", "{{ jira.token }}"]}   # or bearer: "{{ api.token }}"
  json:                          # or form: {...}, or body: "..." with content_type
    fields:
      project: {key: "{{ project }}"}
      summary: "{{ summary }}"
      labels: "{{ labels }}"
  timeout_s: 30                  # per request (default 60)
  # save_as: report.pdf          # keep the response as a file on the card instead
expect: {status: [201]}          # what counts as success (default: any 2xx)
retry: {attempts: 3, backoff_s: 2, max_backoff_s: 60}       # the defaults
rate_limit: 100/minute           # suggested; a card can set its own
outputs:
  - {name: key, parser: json-value, options: {path: key}}
```

**Values are escaped for where they land**, the way command values are shell-quoted:

- A placeholder that starts the URL (a base URL from a variable set) goes in as it is and must be
  an `http://` or `https://` address. Any other value in the URL is percent-encoded, so it stays
  inside its path segment.
- `query` and `form` values are URL-encoded. A list argument repeats the parameter, and an empty
  one is left out.
- In `json`, a field that is exactly one placeholder keeps its type: a list argument becomes an
  array, a number a number, and an empty optional argument leaves the field out. Text around a
  placeholder makes a string.
- A header value with a line break is refused.
- A text `body` is escaped for its `content_type` (JSON, XML or HTML, form, or as is).
- Variable values (`{{ slot.field }}`) are filled in only as the request is sent. They are never
  stored on the card or the job, and secrets are masked in the log.

**The log** shows the request (`> POST https://…`, headers, Authorization as `••••`) and the
response (`< 201 Created`, its headers, the time). The response body is the job's output (stdout),
which the parsers read; `json-records` (the records of a JSON document, optionally at a dot path)
and `json-value` (one value by dot path) are made for APIs. A response that isn't text, or any
response with `save_as`, is kept as a file on the card instead.

**Every page.** `paginate` follows an API's pages and joins their records into one JSON array:

```yaml
paginate:
  style: cursor                  # cursor | link | offset | page
  items: data                    # dot path to each page's records (default: the body is the list)
  cursor: {from: meta.next_cursor, param: cursor}   # cursor: where the next one is, and how to send it
  # next: links.next             # link: a dot path to the next URL (default: the Link header)
  # param: offset                # offset and page: the parameter to send
  # size_param: limit            # and the page size parameter and size
  # size: 100
  # start: 1                     # page: the first page's number
  stop: {max_pages: 50, max_items: 10000}
```

Paging stops at an empty page, a missing next cursor or link, a short page (offset and page
styles), or the `stop` limits. Next links are only followed on the same host.

**Batches.** `batch: {arg: hosts, size: 100}` sends one request per 100 values of the list
argument `hosts`, and joins the responses (JSON arrays) the same way.

**When things go wrong:**

- A `429` or `503` with `Retry-After` waits as told. It also pauses every other request made with
  the same variable set.
- A `5xx`, a timeout or a dropped connection is retried with backoff (`retry`).
- Any other `4xx` fails at once, since the request itself is wrong.
- Progress is saved after every page and batch, so a retry carries on from where it stopped.
- A wait of more than 30 seconds parks the job (status **waiting**, with the time it carries on).
  It gives its slot back meanwhile, and Cancel ends it.

**Rate limits** keep Castor within what an API allows: `60/minute`, `5/second` or `1000/day`,
optionally `, 2 at once`. They can be set on a variable set (every request made with its values,
by any card or playbook: this is how APIs count, per key) and on a card (else the definition's
`rate_limit`). A request waits until every limit that applies has room.

**Where requests may go** is up to admins, in Settings → General:

- Loopback, link-local (cloud metadata) and reserved addresses are refused unless an admin lists
  them. That's also what `CASTOR_HTTP_ALLOW_ADDRESSES` is for.
- Private networks can be blocked too.
- HTTP actions can be limited to a list of hosts.

Alert channels follow the same address rules.
