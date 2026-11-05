# Phase 3 plan

Status: in progress (2026-11-05); 3a and 3d are built. Roadmap item **M3** in
[ARCHITECTURE.md](ARCHITECTURE.md); Phase 2 is done and recorded in [PHASE2.md](PHASE2.md).

Phase 3 makes Castor easier to run day to day: tools prepared on demand instead of baked
into agents, a dashboard that works as a personal inbox, and a bit of AI help when a
playbook step goes wrong.

| Slice | What you get | Depends on | Status |
| --- | --- | --- | --- |
| 3a. Tool environments | Commands name a tool; agents pull or build it on demand and cache it; the agent image stays bare | nothing | Built |
| 3b. "Mine" and @mentions | Mention people in comments (and get notified); a dashboard filter for your cards and mentions | nothing | Idea |
| 3c. Per-person view | Click a name to see everything that person did and has on their plate | 3b helps | Idea |
| 3d. Pinned project notes | A few titled notes (status, objectives…) at the top of a project's dashboard | nothing | Built |
| 3e. AI resolver for paused steps | Gemini reads an unexpected result and retries (maybe with a fixed command), continues or skips | nothing | Idea |
| 3f. Discovered artifacts | Typed objects Castor finds (IPs, hosts, services, credentials…) merged across tools, with triggers that act on new ones | parsers (2b) | Idea |

Suggested order: 3a (it unblocks the NetExec commands on the compose agent), 3d (small),
3b, 3c, then 3e. 3f is the largest; it can start any time after 3a (it builds on parsers).

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

## 3b. "Mine" and @mentions

Turns the dashboard into a personal inbox.

- **Mentions.** Typing `@` in a comment box suggests people who can see the project
  (members and admins). `@username` in a comment is stored with the comment (a
  `comment_mentions` table, or a `mentions` list on the comment) and rendered as a chip.
  Mentioned users get a notification (`comment.mention`, linking to the card), and only if
  they can see the project. Editing a comment notifies only newly added mentions.
- **"Mine" filter.** A dashboard toggle (home and project) that narrows the timeline to:
  cards assigned to you (`tasks.assignees`, which today holds free text and would hold
  usernames), comments that mention you, replies on cards you commented on, and steps you
  were asked to decide. Needs attention gets a "for you" section the same way.
- **Headline.** The dashboard's rotating "what's yours" line already counts cards assigned
  to you (matched by username) and cards you opened; it gains mentions, and assigned cards
  start showing once there's an assignee picker (nothing in the UI sets them yet).
- **Open questions:** should assignees become real user references (a picker, with a
  migration for existing free-text values)? Mention notifications by webhook too, or in-app
  only?

## 3c. Per-person view

- Clicking a name or avatar in the timeline (and on the Users page) opens a person page,
  `/people/<username>`: picture, role, projects, what they did recently (the feed already
  filters by actor), cards assigned to them, and comments mentioning them.
- Respects visibility: you only see their activity in projects you can see.
- **Open question:** a "team" overview (everyone's open cards side by side) for admins, or
  just the per-person page?

## 3d. Pinned project notes

**Built.** Decided: a few notes per project rather than one.

- Up to 12 titled markdown notes per project (`project_notes`: title, body, position, who
  edited it last), shown as cards at the top of the project dashboard. Suggested titles
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
