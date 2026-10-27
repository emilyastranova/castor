# Castor playbook specification

How to write a Castor playbook, written to be handed to a person or an agent as the only
reference. Everything here is enforced by `server/castor_server/playbook_spec.py`,
`playbook_transforms.py` and `playbook_library.py`. If this document and the code ever
disagree, the code wins; please fix the document.

## 1. What a playbook is

A playbook is a YAML document describing a **directed acyclic graph of steps**. Running it
against a project creates a *run*:

- **Command steps** run a command from the command library on an agent. Each one becomes a
  card on the project board, and its output files are parsed into typed values (lists of
  hosts, services, URLs, ...).
- **Transform steps** run on the server. They take values produced by earlier steps,
  filter or reshape them, and publish the result. They have no card and need no agent.

A step starts as soon as every step it depends on has finished successfully (or was
skipped). Steps that don't depend on each other run in parallel. Data flows along the
edges: a step's arguments can reference the parsed outputs of earlier steps, and a step
that references another step automatically depends on it.

## 2. Minimal example

```yaml
id: live-host-services
name: Live host services
description: Ping-sweep a range, keep the hosts that answered, version-scan them.
tags: [network, discovery]
inputs:
  - name: targets
    type: string
    description: CIDR range or host list
    required: true
    example: 192.168.1.0/24
steps:
  - id: sweep
    name: Ping sweep
    command_ref: nmap-ping-sweep
    args:
      targets: "{{ inputs.targets }}"

  - id: live
    name: Live hosts
    transform:
      input: "{{ steps.sweep.outputs.hosts }}"
      ops:
        - filter: { field: status, equals: up }
        - pluck: ip
      output: { name: ips }

  - id: services
    name: Service scan
    command_ref: nmap-service-scan
    skip_if_empty: true
    args:
      targets: "{{ steps.live.outputs.ips }}"
```

`live` depends on `sweep`, and `services` depends on `live`. You don't write
`depends_on` for these because the references imply them.

## 3. Top-level fields

| Key | Required | Type | Rules |
| --- | --- | --- | --- |
| `id` | yes | string | 1–63 chars, `^[a-z0-9][a-z0-9-]*$` (lowercase, digits, hyphens). Unique in the library. Cannot change after creation. |
| `name` | yes | string | Shown in the UI and prefixed to every card title: `[<name>] <step name>`. |
| `schema_version` | no | int | `1` (default). |
| `description` | no | string | One or two sentences: what it does and what it needs. |
| `tags` | no | list of strings | For filtering in the library. |
| `inputs` | no | list | Values asked for when the run starts (section 4). |
| `steps` | yes | list | The graph (section 5). |
| `x-...` | no | anything | Ignored. Use for YAML anchors and notes (section 11). |

**Any other key is an error** (`foo: unknown key`), at the top level and inside steps.

## 4. Inputs

Inputs are asked for in the Run dialog and referenced as `{{ inputs.<name> }}`.

```yaml
inputs:
  - name: targets          # ^[A-Za-z_][A-Za-z0-9_]*$
    type: string           # string | number | boolean | list | varset  (controls the form widget)
    description: CIDR range or host list
    required: true         # the run is refused without a value (unless there is a default)
    default: null          # used when the field is left empty
    example: 10.0.0.0/24   # shown as a hint and prefilled if there is no default
    choices: [a, b, c]     # optional: shown as a dropdown
```

- A value left empty falls back to `default`. If there's no default and `required: true`,
  the run is refused.
- `regex` and `choices` on an input only shape the form. The real validation happens when
  the value reaches a command argument, which checks its own `regex`/`choices`/type.
- Values from the UI arrive as text (`"6"`, `"true"`). Command arguments convert them to
  their declared type.
- Every input you reference must be declared, or the playbook is rejected
  (`step x uses undeclared input y`).

**Variable-set inputs.** `type: varset` asks for a variable set (a credential, an SSH key,
a listener, ...) of the given `kind`, shown as a dropdown of the sets the run's project may
use. Steps bind it to a command's variable slot with `vars:` (section 5.1).

```yaml
inputs:
  - name: creds
    type: varset
    kind: credential       # required for varset inputs
    description: Account to authenticate with
    required: true
```

The value can be given as the set's id or its name (via the API); the run stores the id.
In the Run dialog you can also choose **Enter manually…** to type one-off values for this
run (shared by its steps, not saved to Variables unless you choose to).
The run is refused if the set doesn't exist, has another kind, or isn't allowed in the
project.

## 5. Steps

Every step has these fields:

| Key | Required | Type | Rules |
| --- | --- | --- | --- |
| `id` | yes | string | 1–63 chars, `^[a-z0-9][a-z0-9_-]*$`. Unique in the playbook. Used in references. |
| `name` | yes | string | Card title and graph label. Keep it short (it truncates around 28 characters). |
| `description` | no | string | Becomes the card description. Say what the step is for. |
| `depends_on` | no | list of step ids | Only needed for ordering **without** a data reference. Referenced steps are added automatically. |
| `skip_if_empty` | no | bool | Default `false`. See section 8. |
| `vars` | no | mapping | Command steps only: variable slot → `"{{ inputs.<varset input> }}"` or a set name. See 5.1. |

A step is **either** a command step (`command_ref` + `args`) **or** a transform step
(`transform`). Giving both, or neither, is an error. Don't write `kind:` yourself; it's
derived.

### 5.1 Command steps

```yaml
  - id: smb_audit
    name: SMB signing audit
    command_ref: netexec-smb        # id of a command in the library (section 9)
    skip_if_empty: true
    args:                           # keys must be the command's argument names
      targets: "{{ steps.smb_scan.outputs.hosts }}"
```

- `command_ref` must exist in the command library.
- `args` keys must be argument names of that command. Every argument that is `required`
  with no `default` must be bound.
- An arg value is a literal (`445`, `"1-1000"`, `true`), an input reference, an upstream
  output reference, a string mixing text and references (section 6), or a saved target
  list: `targets: { list: Lab network }` (by name or id). A list's entries are filled in
  when the step starts; the list must exist when the playbook is saved.
- Literal values are type-checked against the argument when the playbook is saved.
  Referenced values are checked when the step starts. A value that fails then (wrong type,
  fails the argument's `regex`, empty but required) fails the step with
  "Could not start: …".

**Variables.** A command can declare variable *slots*, typed sets it needs, such as
`creds` of kind `credential` (see `vars` on the command, `GET /api/commands`). Bind each slot
under `vars:`, either to a `varset` input or to the name of a fixed set:

```yaml
  - id: smb_auth
    name: Authenticated SMB check
    command_ref: netexec-smb-auth
    vars:
      creds: "{{ inputs.creds }}"     # the varset input chosen at run time
    # or a fixed set:  creds: Lab admin
    args:
      targets: "{{ steps.smb_scan.outputs.hosts }}"
```

- Every `required` slot must be bound. A slot's kind must equal the input's `kind`, or the
  named set's kind.
- `vars` values can't reference step outputs.
- Values never appear in the command line or the card: the job receives them as
  environment variables and files when it runs, and secret values the tool prints are
  masked in the output.

### 5.2 Transform steps

```yaml
  - id: web_targets
    name: Web services → URLs
    transform:
      input: "{{ steps.svc_scan.outputs.services }}"   # or a list of references
      ops:                                            # applied in order
        - filter: { field: name, in: [http, http-proxy] }
        - format: "http://{{ host }}:{{ port }}/"
        - unique: true
      output: { name: urls }                          # name defaults to "items"
```

- `input` is one reference, or a list of references whose values are **concatenated**
  (fan-in of several lists into one).
- `ops` is a list of operations (section 7). An empty list passes the input through.
- `output.name` is what downstream steps reference: `{{ steps.web_targets.outputs.urls }}`.
  A reference using any other name is rejected.
- `output.type` is optional. When it's left out, it's inferred: `list<string>` if any op
  is `pluck` or `format`, otherwise the input's type.
- Transform steps take no `args`.
- A transform of an empty input succeeds with an empty list (it is **not** skipped unless
  it has `skip_if_empty: true`).
- Results are capped at 10,000 items.

## 6. References and how values are passed

Two reference forms are recognised (spaces inside the braces are optional):

```
{{ inputs.<input_name> }}
{{ steps.<step_id>.outputs.<output_name> }}
```

- `<output_name>` is a **parsed output** of the referenced step:
  - For a command step, it's one of the command's `outputs` that has a `parser`
    (section 9). Checked on save.
  - For a transform, it's its `output.name`.
- A reference to a step makes it a dependency. Cycles and self-references are rejected.
- The referenced step must exist (`references unknown step x`).

**How an upstream value becomes an argument.** When an arg is *exactly* one reference, the
upstream value is turned into text like this:

| Upstream value | Becomes |
| --- | --- |
| list of records with `ip` (`list<host>`) | IPs joined with commas: `10.0.0.1,10.0.0.2` |
| list of records with `host` (`list<service>`, `list<smb_host>`) | unique hosts, comma-joined (ports are dropped) |
| list of records with `url` (`list<url>`) | unique URLs, comma-joined |
| list of strings / numbers | comma-joined |
| empty list, missing value | `""` (empty) |
| single record | its `ip`, else `host`, else `url` |
| scalar | as text |

The command then renders that text into its command line. Each argument becomes **one
shell-quoted word**, or one word per item for `type: list` arguments, which split on commas
and newlines. Values can never inject shell syntax.

When a reference is *embedded* in a longer string (`"--hosts={{ steps.a.outputs.hosts }}"`),
the same text conversion is substituted in place.

**Rule of thumb: if you need anything other than "the list as comma-separated hosts/IPs/URLs",
put a transform in between.** For example, `host:port` pairs, only some services, URLs built
from services, or one field of a record. Don't try to build per-item strings with inline
templates: `"http://{{ steps.a.outputs.hosts }}"` gives `http://10.0.0.1,10.0.0.2`, not two URLs.

## 7. Transform operations

Each op is a mapping with exactly one key. Items are **records** (JSON objects such as a
host or a service) or **plain values** (strings or numbers). For plain values, the item
itself is addressed as `value` or by omitting `field`.

| Op | Long form | Short forms | Result |
| --- | --- | --- | --- |
| `filter` | `filter: { field: F, <test>: V, ignore_case: false }` | — | Keeps items whose field passes the test. |
| `pluck` | `pluck: { field: F }` | `pluck: F` | Each record's field; items without it (or empty) are dropped. → strings/numbers |
| `format` | `format: { template: "…{{ F }}…" }` | `format: "…{{ F }}…"` | Each item rendered through the template; missing fields render as empty. → strings |
| `unique` | `unique: { field: F }` | `unique: true`, `unique: F` | Drops repeats (by the whole item, or by a field), keeping the first. |
| `sort` | `sort: { field: F, desc: false }` | `sort: true`, `sort: F`, `sort: -F` | Numbers before text, missing last. `-F` means descending. |
| `limit` | `limit: { count: N }` | `limit: N` | The first N items. |

**Filter tests**: give exactly one per `filter`. Chain several `filter` ops for AND.

| Test | Keeps the item when the field… |
| --- | --- |
| `equals: V` | equals V |
| `not_equals: V` | doesn't equal V |
| `in: [V, …]` | equals one of the values |
| `not_in: [V, …]` | equals none of them |
| `contains: "text"` | as text, contains the substring |
| `matches: "regex"` | as text, matches the regular expression anywhere (search, not full match; 2 s timeout) |
| `exists: true` / `exists: false` | is present and non-empty / is missing or empty |

`ignore_case: true` applies to `equals`, `not_equals`, `in`, `not_in`, `contains` and
`matches`.

**Comparisons are type-sensitive.** `port` is a number, so write `in: [80, 443]`, not
`in: ["80", "443"]`. `signing` is a boolean, so write `equals: false`.

Field names follow `^[A-Za-z_][A-Za-z0-9_]*$`. There are no nested paths and no
expressions.

## 8. Execution semantics

**Step statuses**:

| Status | Meaning |
| --- | --- |
| `pending` | Waiting for its dependencies. |
| `queued` | Its job is waiting for an agent. |
| `running` | Its job is running. |
| `succeeded` | Finished; outputs available. |
| `failed` | Its job failed, it couldn't start (bad argument value), or a transform errored. |
| `cancelled` | The job or the run was cancelled. |
| `skipped` | `skip_if_empty` applied. |
| `blocked` | An upstream step failed, was cancelled or is itself blocked. It will never run. |

- **Ready:** every dependency is `succeeded` or `skipped`.
- **`skip_if_empty: true`:** if any value the step takes **from an upstream step** is
  missing or empty, the step is marked `skipped` instead of running. Its card moves to Done.
  It produces no outputs, so its dependents see empty values. Input references are never
  considered.
- **Without `skip_if_empty`:** the step runs with `""` for that value. If the argument is
  required, the step fails with "Could not start". **So set `skip_if_empty: true` on every
  command step whose required arguments come from upstream.** Leave it off for steps that
  should run anyway, such as a final report that collects whatever exists.
- **Failure:** only the failed step's descendants become `blocked`. Independent branches
  carry on to completion.
- **Run status:**
  - `succeeded` when every step is `succeeded` or `skipped`.
  - `failed` once nothing can move any more and something failed or is blocked.
  - `running` otherwise.
- **Re-running a failed card** (from the board or the Playbooks page, possibly after
  editing its command) continues the run. A failed run reopens, and blocked steps proceed
  if the re-run succeeds.
- **Command outputs are parsed after the job finishes.** Downstream steps start once
  parsing is done. A parse error leaves that output missing, which counts as empty.
- **Snapshots:** a run uses the playbook as it was when the run started. Editing the
  playbook doesn't affect runs in progress.

## 9. The command library

Built-in commands live in `commands/*.yaml`. Users can add more in the UI. List the live
library with `GET /api/commands`; each definition has `spec.args` and `spec.outputs`. The
built-ins as of this writing:

| Command | Runs on | Arguments (`*` required, `=default`) | Parsed outputs |
| --- | --- | --- | --- |
| `nmap-ping-sweep` | container | `targets`* | `hosts` list<host> (nmap.hosts) |
| `nmap-port-445` | container | `targets`*, `ports`=445 | `hosts` list<host> (nmap.hosts) |
| `nmap-service-scan` | container | `targets`*, `ports`=1-1000 | `services` list<service> (nmap.services) |
| `netexec-smb` | host | `targets`* | `smb_hosts` list<smb_host> (netexec.smb) |
| `netexec-smb-auth` | host | `targets`*; slot `creds` (credential) | `smb_hosts` list<smb_host> (netexec.smb) |
| `netexec-auth` | host | `protocol`*=smb (choices from the "NetExec protocols" set), `targets`*, `use_dns` + `dns_server` (`--dns-server`), `kerberos` (`-k`), `local_auth` (`--local-auth`); slot `creds` (credential) | `successes` list<string> (lines), `smb_hosts` list<smb_host> (netexec.smb) |
| `ffuf-dir` | container | `url`*, `wordlist`=/work/wordlists/common.txt | `paths` list<url> (ffuf.results) |
| `demo-seed-hosts` | host | `subnet`=10.20.0, `count`=6 | `hosts` list<host> (nmap.hosts) |
| `demo-service-scan` | host | `targets`* | `services` list<service> (nmap.services) |
| `demo-smb-scan` | host | `targets`* | `hosts` list<host> (nmap.hosts) |
| `demo-smb-audit` | host | `targets`* | `smb_hosts` list<smb_host> (netexec.smb) |
| `demo-dns-names` | host | `targets`* | `names` list<string> (lines) |
| `demo-dir-brute` | host | `urls`* | `paths` list<url> (ffuf.results) |
| `demo-report` | host | `findings`, `unsigned`, `names` | `report` string (text) |
| `demo-vars` | host | slots `creds` (credential), `key` (ssh-key, optional), `callback` (listener, optional) | (none) |

- Every `demo-*` command also takes `step`, `delay`, `speed`, `fail_at` and `empty_at`.
  These are knobs for testing. The `demo-*` commands invent their output and touch no
  network, so they're for exercising playbooks, not real work.
- `nmap.hosts` keeps hosts reported as down by default. After `nmap-ping-sweep`, filter
  `status: up` (as in section 2) before scanning further.
- `ffuf-dir` takes a single base `url`. It cannot fan out over a list of URLs.
- Built-in variable kinds: `target-list` (entries: hosts/IPs/ranges/domains/URLs),
  `credential` (username, password\*, nt_hash\*, domain; all
  optional), `ssh-key` (username, private_key\* file, passphrase\*), `listener` (host),
  `api-token` (token\*, base_url), `choice-list` (options: a list commands can use as
  dropdown choices via `choices_from`); \* = secret. Users define their own kinds (e.g. a `scope`
  with a list of endpoints). List them with `GET /api/variables/kinds`.
- If no existing command fits, write one: see `commands/README.md` for the command format.
  A playbook can only reference commands that exist in the library when it's saved.

### Value types and record fields

These are the record shapes you can `filter`, `pluck`, `format` and `sort` on:

| Type | Fields |
| --- | --- |
| `host` | `ip` (string), `hostnames` (list of strings), `status` (`up`/`down`/…) |
| `service` | `host` (IP string), `port` (number), `proto` (`tcp`/`udp`), `state` (`open`, …), `name` (e.g. `http`, `ssh`, `microsoft-ds`), `product`, `version` |
| `url` | `url`, `status` (HTTP code, number), `length`, `words`, `lines` (numbers) |
| `smb_host` | `host` (IP), `port` (number), `name`, `domain`, `os`, `signing` (boolean), `smbv1` (boolean) |
| `string`, `list<string>` | plain values (address them as `value`) |

## 10. Validation: check before you save

Problems are reported in two places:

1. **Parsing**, on save or validate: YAML syntax, unknown keys, id formats, a step with
   both or neither of `command_ref`/`transform`, unknown or undeclared references, wrong
   transform output names, invalid ops (unknown op, a filter with zero or two tests, a bad
   regex, a `format` with no placeholder), duplicate step ids, and cycles.
2. **The library check**, on save or validate: unknown `command_ref`, an arg that isn't an
   argument of the command, a required argument left unbound, a literal value of the wrong
   type or failing the argument's regex/choices, a referenced command output that
   doesn't exist or has no parser, and variable slots: unknown slot, required slot not
   bound, kind mismatch with the input, unknown set name.

Use the dry-run endpoint while iterating. It saves nothing:

```bash
curl -s -X POST http://localhost:8000/api/playbooks/validate \
  -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" \
  --data "$(jq -Rs '{yaml: .}' my-playbook.yaml)"
```

```json
{ "ok": false,
  "errors": ["step web: scan (nmap-service-scan) has no parsed output servicez (outputs: services)"],
  "spec": { "...": "normalised spec with kinds and implicit depends_on filled in" } }
```

Get a token with `POST /api/auth/login` (`{"username": …, "password": …}` → `{"token": …}`).
Save with `POST /api/playbooks {yaml}` (new) or `PUT /api/playbooks/<id> {yaml}`; both run
the same checks and answer 422 with the problems. Built-in playbooks (files in
`playbooks/`) are read-only in the UI.

## 11. Patterns

**Fan-out / parallel branches**: several steps reference the same upstream output.

```yaml
  - id: svc_scan
    name: Service scan
    command_ref: nmap-service-scan
    skip_if_empty: true
    args: { targets: "{{ steps.seed.outputs.hosts }}" }
  - id: smb_scan
    name: SMB port scan
    command_ref: nmap-port-445
    skip_if_empty: true
    args: { targets: "{{ steps.seed.outputs.hosts }}" }
```

**Fan-in**: one step references several upstream steps. It waits for all of them.

```yaml
  - id: report
    name: Report
    command_ref: demo-report
    args:
      findings: "{{ steps.interesting.outputs.urls }}"
      unsigned: "{{ steps.unsigned.outputs.hosts }}"
```

Or merge lists of the same kind first with a multi-input transform:

```yaml
  - id: all_hosts
    name: All hosts
    transform:
      input:
        - "{{ steps.scan_a.outputs.hosts }}"
        - "{{ steps.scan_b.outputs.hosts }}"
      ops: [{ unique: ip }]
      output: { name: hosts }
```

**Select, then act**: filter records and extract what the next command needs.

```yaml
      ops:
        - filter: { field: signing, equals: false }   # smb_host records
        - pluck: host
        - sort: true
```

```yaml
      ops:
        - filter: { field: name, matches: "^https?$|http-proxy" }   # service records
        - format: "http://{{ host }}:{{ port }}/"
        - unique: true
```

**Ordering without data**: `depends_on: [other_step]`.

**Shared arguments**: use a YAML anchor under an `x-` key and the merge key `<<`.

```yaml
x-common: &common
  speed: "{{ inputs.speed }}"
steps:
  - id: a
    name: Seed
    command_ref: demo-seed-hosts
    args: { <<: *common, step: a }
```

## 12. Checklist for authors

- [ ] `id` is kebab-case and new; step ids are unique `snake_case` or kebab-case.
- [ ] Every `command_ref` exists, and every arg name is one of its arguments (`GET /api/commands`).
- [ ] Every referenced output is a parsed output of that command, or the transform's `output.name`.
- [ ] Every referenced input is declared under `inputs`.
- [ ] Every required variable slot of each command is bound under `vars:`, to a `varset` input of the same `kind`.
- [ ] Command steps fed from upstream have `skip_if_empty: true`, unless they should run regardless.
- [ ] Anything that isn't "a list of hosts/IPs/URLs" goes through a transform first.
- [ ] Filter values have the field's real type (numbers unquoted, booleans as `true`/`false`).
- [ ] After a ping sweep, filter `status: up`.
- [ ] Names are short; descriptions say why the step exists.
- [ ] `POST /api/playbooks/validate` returns `"ok": true`.

A complete worked example that uses every feature is `playbooks/pipeline-demo.yaml`.
