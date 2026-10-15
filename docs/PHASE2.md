# Phase 2 design: command library, structured outputs, playbooks

Status: decided 2026-10-15. 2a (command library + per-job artifacts) and 2b (parsers +
structured outputs) are built; next is 2c. Roadmap item **M2** in [ARCHITECTURE.md](ARCHITECTURE.md).

Phase 2 turns Castor from "run this shell command on an agent" into "run a known tool with
typed inputs, get structured results, and feed them into the next tool". It has five
parts. They are listed in build order; each one is usable on its own.

| Slice | What you get | Depends on |
| --- | --- | --- |
| 2a. Command library + per-job artifacts | Pick "Nmap ping sweep" from a library, fill in typed fields, files the tool writes come back to the card | nothing |
| 2b. Parsers + structured outputs | nmap XML becomes a hosts/services table on the card | 2a |
| 2c. Playbooks (runs as cards) | A chain of blocks becomes linked cards that dispatch as their inputs become ready | 2b |
| 2d. Playbook graph editor | Drag blocks and wire outputs to inputs | 2c |
| 2e. Per-agent tokens | Enroll and revoke agents individually | nothing |
| 2f. Users and roles | Real accounts, project membership, viewer/operator/admin | nothing |

2e and 2f are independent of the playbook track and can be slotted in anywhere.

## Gaps in the current code

- **Shared agent workdir.** Every job on an agent mounts the same `/work`, so concurrent or
  repeated jobs overwrite each other's files, and nothing a tool writes reaches the server.
  Parsers need the files, so this is the first thing to fix.
- **No dependencies between cards.** Tasks have no `depends_on`; playbooks need it.
- **One shared agent secret** for every agent (`CASTOR_AGENT_SECRET`).
- **Command templates are unquoted.** `commands/*.yaml` use `nmap -sn {{ targets }}`. If that
  is rendered by string substitution, a value like `10.0.0.0/24; rm -rf ~` runs as shell.
  Rendering must shell-quote every value (details below).

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
CSV copies prefix cells starting with `= + - @` so target-controlled strings can't become
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

## 2d. Graph editor

`@xyflow/svelte` canvas: blocks from the command library on the left, steps as nodes with
typed input/output ports, edges only allowed between compatible types (`list<host>` into
an arg that accepts a host list, with an optional "join as comma list" adapter). Saves to the
same playbook model as 2c, so a YAML/list editor can come first and the canvas later.

## 2e. Per-agent tokens

Admin creates an enrollment token in the UI (shown once). The agent sends it in `hello`; the
server stores only a hash, binds it to the agent record on first use, and the agents page
gets Revoke (disconnects immediately) and Rotate. The shared secret stays as an optional
dev fallback behind a setting.

## 2f. Users and roles

User CRUD for admins, roles `admin` / `operator` / `viewer`, per-project membership, and
authorization checks in the API (viewers read-only; operators can run jobs; admins manage
users, agents and tokens). Comment/attachment author checks reuse this.

## Decisions (2026-10-15)

1. Build order: 2a → 2b → 2c → 2d; 2e and 2f later.
2. Playbook runs appear as one card per step, linked by dependencies.
3. Command definitions live in both places: built-ins as YAML in `commands/`, user
   definitions in the database (edited in the UI as YAML).
4. Playbooks ship with a step-list editor first; the graph canvas follows.
