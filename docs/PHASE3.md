# Phase 3 plan

Status: in progress (2026-11-05); 3a, 3b, 3c, 3d, 3g and 3h are built; 3e, 3f and 3i are left. Roadmap item **M3** in
[ARCHITECTURE.md](ARCHITECTURE.md); Phase 2 is done and recorded in [PHASE2.md](PHASE2.md).

Phase 3 makes Castor easier to run day to day: tools prepared on demand instead of baked
into agents, a dashboard that works as a personal inbox, and a bit of AI help when a
playbook step goes wrong.

| Slice | What you get | Depends on | Status |
| --- | --- | --- | --- |
| 3a. Tool environments | Commands name a tool; agents pull or build it on demand and cache it; the agent image stays bare | nothing | Built |
| 3b. "Mine", @mentions, assignees | Assign cards to people; mention people in comments (they get notified); a "Mine" filter on the dashboard and board | nothing | Built |
| 3c. Per-person view | Click a name to see everything that person did and has on their plate | 3b helps | Built |
| 3d. Pinned project notes | A few titled notes (status, objectives…) at the top of a project's dashboard | nothing | Built |
| 3e. AI resolver for paused steps | Gemini reads an unexpected result and retries (maybe with a fixed command), continues or skips | nothing | Idea |
| 3f. Discovered artifacts | Typed objects Castor finds (IPs, hosts, services, credentials…) merged across tools, with triggers that act on new ones | parsers (2b) | Idea |
| 3g. Search everywhere | Type-to-filter in every long picker; Ctrl+K searches everything you can see | nothing | Built |
| 3h. Playbook runs as one card | A run is a single card on the board that opens into its graph and steps, instead of a card per step | nothing | Built |
| 3i. Import and export | Move projects, commands, tools, playbooks, variables (everything) between Castor instances as files | nothing | Idea |

Suggested order: 3a (it unblocks the NetExec commands on the compose agent), 3d (small),
3b, 3c, then 3e. 3f is the largest; it can start any time after 3a (it builds on parsers).
3g's pickers are small and can go any time; 3h changes how runs look everywhere, so it's
best done before 3e (whose decisions show up on the run card).

## 3a. Tool environments

**Built.** The agent stays bare (its image has only the docker CLI); the tools commands
need are described in a library and prepared on demand. Code: `tool_spec.py`,
`tool_library.py`, `api/tools.py`, `dispatcher.tool_way`, agent `tools.py`; built-ins in
`tools/`. Decided: all three ways of providing a tool
(published images, build recipes, opt-in host installs), and a local cache per agent (no
registry yet).

### The library

Tools live in `tools/*.yaml` (built-ins, synced at startup like `commands/`) and in the
database (edited in the UI as YAML). A command names one with `tool: <id>` instead of
`image:`. A tool provides itself in one or more of three ways, tried in this order:

```yaml
id: netexec
name: NetExec
description: Network service exploitation (nxc)

# A) A published image, used as-is (pulled, then Docker's cache).
# image: docker.io/someone/netexec:1.5

# B) A recipe: built once per agent and cached by a hash of the recipe.
build:
  base: docker.io/library/python:3.13-slim-bookworm
  install: |
    apt-get update
    apt-get install -y --no-install-recommends git g++ libffi-dev libxml2-dev libxslt-dev libssl-dev
    pip install --no-cache-dir "git+https://github.com/Pennyw0rth/NetExec@<commit>"
  check: nxc --version | grep -q "<commit[:8]>"   # must exit 0; nxc --version exits 1

# C) On the agent's host, for agents without Docker (opt-in per agent).
host:
  check: command -v nxc
  install: pipx install git+https://github.com/Pennyw0rth/NetExec
```

### Preparing a tool

- **Recipes (B).** The server renders a Dockerfile from `base` + `install` (+ `check` as the
  last step, so a broken install fails the build) and tags it with a hash of everything
  that goes into it: `castor-tools/<id>:<sha256[:12]>`. Changing the recipe changes the tag,
  so stale builds are never reused. Before a job, the agent checks for the tag
  (`docker image inspect`); if it's missing it builds it, once at a time per tag, streaming
  the build output into the job's console as a "Preparing <tool>" phase, then runs the job
  in it. Builds stay in the agent's local Docker cache.
- **Images (A).** Pulled if missing; Docker caches them.
- **Host (C).** The agent runs `check`. If it fails, the agent runs `install` only when it was
  started with `--allow-host-install`; otherwise the job fails with the install command in
  the message.
- **Sharing.** Local cache only for now (decided). A registry (`CASTOR_TOOL_REGISTRY`: pull a
  build if present, push after building) can be added later without changing the format.

### Server and agents

- `job.run` carries the resolved tool: `{id, name, kind: image|build|host, ref?, files?,
  check?, install?, install_hint?}` (`files` is the build context: `Dockerfile` plus the
  install/check scripts); the agent reports the preparation phase as `system` log lines.
- Agents report the tool tags they have cached (hello + heartbeat); the dispatcher prefers
  an agent with the tool ready over one that would build it, and won't send a host-only
  tool to an agent that can't run it.
- Playbook validation checks that every command's tool is defined.

### UI

A Tools page: the library, which agents have each tool cached, **Prepare on agents** (build
or pull ahead of an engagement), and build logs. A command shows its tool and whether the
chosen agent has it ready.

### Migration

Done: `netexec-*` use `tool: netexec` (a recipe pinned to a NetExec commit, because the
v1.5.1 release no longer installs), and the nmap/ffuf commands use tools wrapping their
previous images. Existing cards keep their stored `image` until their command is
re-applied.

### References

- NetExec's Docker instructions: <https://www.netexec.wiki/getting-started/installation/using-docker>
- A published image (Parrot): <https://hub.docker.com/r/parrotsec/netexec>
- Hash-tagged images to skip rebuilds: <https://github.com/dpc/docker-source-checksum>

## 3b. "Mine", @mentions and assignees

**Built.** Decided: assignees are real users, and mention/assignment notifications are
in-app only (the team webhooks stay for run events).

- **Assignees.** `tasks.assignees` holds user ids, chosen from the people who can see the
  project (`GET /api/projects/{id}/people`: members and admins; `people.check_assignees`
  also accepts usernames). The card dialog has an Assignees picker with "Assign to me";
  cards show assignee avatars; the board has an **Assigned to me** filter. Newly assigned
  people get a `task.assigned` notification (not when you assign yourself), and changes go
  to the feed (`task.assigned`). The migration turned old free-text values that named a
  user into that user's id and dropped the rest.
- **Mentions.** Typing `@` in a comment box suggests the people on the project (Enter/Tab
  or click inserts `@username`). The server reads `@username` outside code
  (`people.mention_names`), stores the ids on the comment (`comments.mentions`) and
  notifies the people mentioned who can see the project (`comment.mention`); an edit
  notifies only people it adds. Mentions render as chips (yours highlighted).
- **"Mine".** A dashboard toggle (home and project, remembered per browser) that narrows
  the timeline to what's about you that someone else did: events on cards assigned to you,
  opened by you or commented on by you, and comments that mention you (`feed?mine=true`).
  Needs attention keeps decisions and your failed cards (tagged "for you"). Unread mentions
  of you head Needs attention in every view, and drop off once opened. The timeline's
  filters (kinds and Mine) are remembered per browser. The headline's
  "what's yours" line counts assigned cards, unread mentions and cards you opened.
- Code: `people.py`, `api/tasks.py` (`_assignees_changed`, `_mention`),
  `dashboard._mine`, web `lib/people.svelte.ts`, `lib/mentionSuggest.svelte.ts`,
  `MentionMenu`, `RichText`, `AssigneePicker`.

## 3c. Per-person view

**Built.** Decided: a team overview for everyone (not only admins), next to the person pages.

- `/people/<username>` (`GET /api/people/{username}`): picture, role, when they joined and
  were last active, their projects, numbers for the last 30 days (comments, cards created,
  runs started), their activity timeline (the dashboard feed with `actor=`), the open cards
  assigned to them ("on their plate", by priority) and the comments that mention them.
- `/people` (`GET /api/people`): everyone active, with open assigned cards and last activity;
  sort by name, most assigned or recently active; also in the rail as **People**.
- Everything about what someone did or has is counted only in projects the viewer can see.
- Names lead there: timeline names and avatars, comment authors, mention chips, assignee chips,
  Settings → Users, and people in search.
- Admins manage accounts under **Settings → Users** (`/settings/users`; `/users` redirects),
  so the rail has People for everyone and Settings for admins instead of People and Users.

## 3d. Pinned project notes

**Built.** Decided: a few notes per project rather than one.

- Up to 12 titled markdown notes per project (`project_notes`: title, body, position, who
  edited it last), shown as cards at the top of the project dashboard. Every card is the
  same fixed height so new content never shifts the page; longer notes scroll inside (with a
  fade at the bottom) and Expand uncaps one for the viewer who clicked it.
- Each note has a sticky-note color (`project_notes.color`: default, red, orange, yellow,
  green, teal, blue, navy, purple, pink, brown, gray), picked from the palette button on the
  card or in the editor. Shades are `--note-*` in `app.css` (pastels in light, muted in dark).
  A color change isn't a new version or a feed entry. Suggested titles
  for the first one: Status, Objectives, Scope, Contacts.
- Everyone who sees the project reads them; operators edit them in place (Ctrl+Enter
  saves), reorder them (Move left/right) and remove them. Pasted images work as in
  comments: named, uploaded as project attachments (`POST /api/projects/{id}/attachments`,
  no card) and embedded as markdown.
- Every edit keeps the previous version (last 20, `project_notes.history`); History shows
  them and restores one, which is itself kept, so a restore can be undone.
- Changes go to the activity feed (`note.created|updated|deleted`, feed kind `note`) and
  publish `project.notes`, so open dashboards reload them.
- The home dashboard's **Pinned notes** panel lists every note as its title and first line
  in plain text (`summary.pinned`), grouped by project (up to 4 each, then "+N more"), and
  can pin a new note on any project.
- Code: `api/notes.py`, `dashboard._pinned`/`first_line`,
  `web/src/lib/components/dashboard/ProjectNotes.svelte`.

## 3e. AI resolver for paused steps

Carried over from Phase 2. When a step pauses (`on_failure: ask`), an AI model (Gemini)
reads the step, its command, the unexpected output and the expectation that failed, and
picks retry (possibly with a corrected command), continue or skip, or leaves it to a
person.

- It plugs into what's there: `services.resolve_step` already takes an `actor`, and every
  decision is recorded on the run (`step_results[step].resolution`) with who decided.
- Opt-in per step, e.g. `on_failure: { ai: true, fallback: ask }`, with a confidence
  threshold below which it asks a person; the API key comes from settings
  (`CASTOR_GEMINI_API_KEY`).
- A corrected command is shown as a diff and recorded; secrets are never sent (the output
  sent is the masked log).
- **Open questions:** which decisions may it take alone (only retry and skip, or continue
  too)? A limit on retries per step?

## 3f. Discovered artifacts

Castor keeps what it finds as objects it understands, not just files and tables. When a
run turns up an IP address it stores an **IP address**; when a scan shows a host without
SMB signing it stores (or updates) that **host** with `smb_signing: false`. Several tools
reporting on the same thing add to one record, so a host collects its ports from nmap, its
signing and OS from NetExec, its web paths from ffuf, and so on: one melting pot per thing.

**Naming.** "Artifacts" already means the files a job uploads (`attachments.kind =
artifact`). Either rename those to "files"/"outputs" in the UI or call these something
else (findings, entities, loot); decide before building.

- **Types.** A small built-in set with an identity key each, so duplicates merge:
  `ip` (address), `host` (IP, or hostname), `service` (host + port/proto), `url`,
  `domain`, `user`, `credential` (user + secret in the vault, never in plain fields),
  `share`. Each type has typed fields (`host.os`, `host.smb_signing`, `service.product`,
  `service.version`…) plus free extra fields. Links between them (a service *on* a host, a
  credential *valid on* a host) are first-class, so a host page shows everything known.
- **Where they come from.** Parsers already turn output into JSON (2b); a parser (or an
  output spec on a command) also declares **mappings** from its rows to artifacts, e.g.
  `nmap-xml` → `host` + `service` per open port, NetExec SMB → `host` with `smb_signing`,
  `os`, `domain`. Artifacts can also be added by hand or imported from a list.
- **Provenance and conflicts.** Every field value remembers which job set it and when
  (`observations`), so you can see who said what. The newest observation wins by default;
  conflicting values are kept and shown. Each artifact is scoped to a project.
- **Using them.** An artifact list per project (filter by type and field, e.g. "hosts
  where smb_signing = false"), an artifact page with its fields, links, history and the
  jobs that touched it, and artifact queries as inputs: a command argument or playbook
  input can take "all hosts where …" (like the target lists from variable sets today).
- **Triggers.** Rules that run a command or playbook when artifacts appear or change:
  "every new `ip` → ping it", "a `host` gets `smb_signing: false` → queue the relay
  check", "a `service` with `http` → run ffuf". A trigger has a type, an optional
  condition (the same condition trees playbooks use for `when`), a target (command +
  argument mapping, or a playbook), and guards: once per artifact, a rate limit, and
  per-project on/off, so a scan can't snowball into thousands of jobs. Triggered runs are
  cards like any other, labelled with the trigger that made them.
- **Events.** `artifact.created|updated` on the bus, so the dashboard timeline can show
  "found 12 new hosts" and the board stays live. The dashboard's rotating headline gets a
  discoveries line ("Found 12 hosts and 3 services today").
- **Open questions:** the name; whether artifacts are per project only or can be shared
  across projects (same client network); how much of the type system is user-defined
  (like variable kinds) versus built in; and whether triggers ask before running against
  new targets (scope safety) or only inside a project's declared scope.

## 3g. Search everywhere

**Built.** Pickers: the shared `Select` gets a search box when it has more than 7 options,
and options can carry a description, a group and keywords (the command picker uses all
three; agents show host, OS and tags). Global search: `GET /api/search` (`search.py`) and
`CommandPalette.svelte` on Ctrl/Cmd+K or the rail's search button, with recent results and
places when empty, the filters below, highlighted matches, and deep links (`?task=&comment=`,
`&tab=output&job=&view=results`, `&tab=attachments`, `/commands?open=`, `/parsers?parser=`).
Decided while building: search runs over the live tables (every word must match some field,
case-insensitively) instead of a separate index, so nothing can go stale and it behaves the
same on SQLite and Postgres; a full-text index can replace it if logs get large. Secret
values are never searched (they live in their own encrypted column).

The plan, as written before building:

Two parts: long pickers you can type into, and one search box for everything.

### Searchable pickers

Several pickers are plain dropdowns that get unwieldy as libraries grow: choosing a
library command for a card, a playbook to run, a project, an agent, a variable set or
list, a tool, a parser, a person. Replace them with one **searchable select** (a combobox):

- Type to filter, matching name, id, tags and description (commands also by their tool and
  arguments), ranked by how well they match; arrow keys and Enter to pick; the current value
  stays visible.
- Grouped where it helps (built-in vs yours, by tag), with each option's one-line description
  and icon, so the right command is recognisable without opening it.
- Used wherever a list can exceed about seven options; short fixed lists (priority, role)
  stay as they are.
- The command picker in the New card dialog and the card's command field come first.

### Global search (Ctrl+K)

A command palette on **Ctrl+K** (Cmd+K on macOS) and a search icon in the navigation rail
that searches **everything the user can see**:

- Cards (title, description, tags, custom fields, the command line), comments, attachment
  and screenshot names, card assignees.
- Runs: job command lines, parsed outputs (the values in hosts, services, URLs…), and job
  logs (matched lines, with the line shown).
- Projects, clients, pinned notes, playbooks and their runs and inputs, the command and tool
  libraries, parsers, variable sets and kinds (names and non-secret fields only: never a
  secret's value), people, agents.

How it should feel:

- Results grouped by type, with the matching words highlighted and a snippet (the comment
  text, the log line, the output row), and where each one lives (project, card).
- Keyboard first: type, arrow, Enter opens it (a card opens on the right tab: the comment,
  the run's console at the matching line, the output row). Filters as prefixes or chips:
  `in:NWL26-001`, `type:comment`, `by:sam`, `tag:smb`, `is:mine`, `status:failed`.
- With nothing typed: recent items and quick actions (new card, run a playbook, go to a page).
- Permissions apply: results only come from projects the user can see; admin-only things
  (users, agent tokens) only for admins.

How it might work:

- A server endpoint (`GET /api/search?q=&types=&project=`) backed by a search index: SQLite
  FTS5 for the dev database, Postgres full-text search (`tsvector`, plus `pg_trgm` for
  partial words) in production, behind one interface.
- An index table (`search_documents`: type, ref, project, title, body, updated) kept current
  from the same events the UI already gets (`task.updated`, `comment.created`, `job.outputs`…),
  with a rebuild command (`castor-admin reindex`) and a startup check.
- Logs are the big one: index them per job in chunks, cap what's indexed per job, and let
  search say "and more in this log" rather than indexing everything.
- **Open questions:** how far back logs are searchable (all, or the last N days)? Should the
  palette also run actions (move a card, assign someone), or only find things at first?

## 3h. Playbook runs as one card

**Built.** Decided: steps stay cards in the database but never show on the board; the run
card is created in In Progress and moves to Review once when the run ends (a card someone
moved stays put); no "pop out a step" for now.

- The run card (`playbook_runs.card_id`) shows a progress bar and the run's state (running ·
  done/total, needs you, done, failed, cancelled). It opens on a **Run** tab: state, inputs,
  decisions to answer, the live graph and the steps; a step (or a for_each item) opens as its
  own card on top (`?step=`), with its console, results, files and comments as before.
  Comments, attachments and history on the run card are about the run as a whole.
- Old links to step cards (search, notifications, the feed) redirect to the run card with the
  step on top. Decisions in Needs attention and their notifications open the run card.
- Existing runs got their card at startup (`ensure_run_cards`, idempotent).
- Not done yet: grouping a run's step events under the run in the dashboard timeline.

The plan, as written before building:

Today every step of a run is its own card (Phase 2c decided this), so one pipeline puts
ten or more cards on the board and buries the hand-made ones. Instead, a run becomes
**one card** that stands for the whole run.

On the board:

- The run card shows the playbook's name and inputs (e.g. the subnet), a progress bar
  (done/total), the current step(s), and the run's state: running, needs a decision, failed,
  done. It moves through the columns by itself (queued → In Progress → Review/Done, or a
  "needs you" state when a step pauses), and can still be dragged like any card.
- Step cards no longer appear on the board. Existing runs are migrated: their step cards are
  folded into the run card (kept as steps, with their comments and files), not deleted.

Opening it (the UX to design):

- **Graph** (default while running): the run's DAG, as on the Playbooks page, live. Clicking
  a step selects it below instead of navigating away.
- **Steps**: a list (or the graph's selection) where each step shows what a card shows today:
  its command, runs and console, parsed results, files, and its own comments; for_each steps
  show their items. Decisions (`on_failure: ask`) are answered right there.
- **Overview**: inputs, who started it and when, duration, outputs of the final step (the
  report), and the run's comments (discussion about the run as a whole).
- Deep links keep working: `?task=<run card>&step=<id>` opens a step, `&comment=` a comment;
  notifications and the dashboard link there.
- Re-run a failed step, cancel the run, or start it again with the same inputs from the card's
  menu.

Elsewhere:

- The dashboard timeline groups a run's step events under the run ("Pipeline Demo: 6 of 10
  steps done") instead of one line per step; Needs attention links decisions to the run card.
- Assignees, mentions and the "Mine" filter apply to the run card (and step comments notify
  as they do now).
- **Open questions:** do steps stay tasks in the database (simplest migration: they just stop
  showing on the board) or become their own table? Should a user be able to "pop out" a step
  as a separate card when it needs its own follow-up work? Where do run cards land: a
  playbook-chosen column, or always To Do / In Progress?

## 3i. Import and export

Get things out of one Castor and into another (a second install, a fresh host, a teammate's
lab), or keep a copy: one item, a project, or everything.

### What can be exported

- **Library items**, one at a time or many: commands, tools, playbooks (already YAML, so the
  export is the same YAML they're edited as), variable kinds, and parsers' saved options if
  that ever exists. A bundle of several is one file.
- **Variable sets**: names, kinds, non-secret values and list fields. **Secrets are left out
  by default**; including them asks for a passphrase and encrypts them in the file (never the
  instance's vault key, never plain text). Importing without the passphrase brings the set
  in with its secrets empty, flagged as missing.
- **A project**: settings, columns, cards (fields, tags, priorities, assignees, custom fields,
  command and argument bindings), comments with mentions, attachments and screenshots (the
  files themselves), pinned notes, playbook runs with their step cards, and optionally job
  history (runs, logs, parsed outputs) and the activity timeline, which can be large.
- **Everything** (admins): every project plus the libraries, variable kinds and sets, users
  (usernames, names, roles, pictures; never password hashes or tokens), project members,
  clients. A migration path between hosts that doesn't depend on copying the database.

### Format

- One archive (`.castor.zip`): a `manifest.json` (format version, Castor version, what's in
  it, when, by whom), JSON per kind of thing, YAML for library items, and the attachment files
  under `files/`. Single library items also export as plain `.yaml`.
- Every id in the archive is the source's; importing maps them to new ids, so the same
  archive can be imported twice (two copies) and references inside it stay intact.
- People are matched by **username**: assignees, mentions, authors and members point at the
  user with the same username on the target, or are kept as a name only (or create the users,
  for an admin importing everything).
- The format is versioned; an importer reads older versions and refuses newer ones clearly.

### Importing

- **Preview first**: what's in the file, what's new, what already exists (same id for library
  items, same codename for projects), what would be renamed, and anything that won't come
  across (a command naming a tool that isn't there, a variable set without its secrets, a
  person who doesn't exist).
- Conflicts per item: **skip**, **replace** (library items you own; built-ins are never
  replaced), or **keep both** (renamed id/codename, e.g. `nmap-scan-2`, `NWL26-001-copy`).
- Runs in one transaction: a failed import leaves nothing behind. Imported items are recorded
  in the activity feed (who imported what, from which file).
- Imported projects come in with their history read-only as it was; nothing is re-run.

### Where

- Each library page (Commands, Tools, Playbooks, Variables) gets **Export** (selected or all)
  and **Import** buttons; a card's menu in the library offers "Export as YAML".
- A project's menu gets **Export project…** (with checkboxes for job history and activity)
  and the Projects page **Import project…**.
- **Settings → Import and export** (admins): export everything, import an archive, and a
  history of past imports/exports.
- API: `GET /api/export?...` (streams the archive), `POST /api/import/preview` (upload, returns
  the plan), `POST /api/import` (the file plus the chosen resolutions).

- **Open questions:** should exports be scheduled (a nightly "everything" archive to a folder
  or bucket as a backup)? Should an import be able to merge into an existing project (add the
  archive's cards to it) rather than always creating a new one?
