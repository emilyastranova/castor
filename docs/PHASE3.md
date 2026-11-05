# Phase 3 plan

Status: planned (2026-11-04). Nothing here is built yet. Roadmap item **M3** in
[ARCHITECTURE.md](ARCHITECTURE.md); Phase 2 is done and recorded in [PHASE2.md](PHASE2.md).

Phase 3 makes Castor easier to run day to day: tools prepared on demand instead of baked
into agents, a dashboard that works as a personal inbox, and a bit of AI help when a
playbook step goes wrong.

| Slice | What you get | Depends on | Status |
| --- | --- | --- | --- |
| 3a. Tool environments | Commands name a tool; agents pull or build it on demand and cache it; the agent image stays bare | nothing | Designed |
| 3b. "Mine" and @mentions | Mention people in comments (and get notified); a dashboard filter for your cards and mentions | nothing | Idea |
| 3c. Per-person view | Click a name to see everything that person did and has on their plate | 3b helps | Idea |
| 3d. Pinned project notes | A short status/objectives note at the top of a project's dashboard | nothing | Idea |
| 3e. AI resolver for paused steps | Gemini reads an unexpected result and retries (maybe with a fixed command), continues or skips | nothing | Idea |

Suggested order: 3a (it unblocks the NetExec commands on the compose agent), 3d (small),
3b, 3c, then 3e.

## 3a. Tool environments

The agent stays bare (its image has only the docker CLI); the tools commands need are
described in a library and prepared on demand. Decided: all three ways of providing a tool
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
image: docker.io/parrotsec/netexec:7.1

# B) A recipe: built once per agent and cached by a hash of the recipe.
build:
  base: docker.io/library/python:3.12-slim
  install: |
    apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
    pip install --no-cache-dir git+https://github.com/Pennyw0rth/NetExec@v1.4.0
  check: nxc --version

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

- `job.run` carries the resolved tool: `{kind: image|build|host, ref, dockerfile?, check?,
  install?}`; the agent reports `job.log` lines for the preparation phase on a `system`
  stream.
- Agents report the tool tags they have cached (hello + heartbeat); the dispatcher prefers
  an agent with the tool ready over one that would build it, and won't send a host-only
  tool to an agent that can't run it.
- Playbook validation checks that every command's tool is defined.

### UI

A Tools page: the library, which agents have each tool cached, **Prepare on agents** (build
or pull ahead of an engagement), and build logs. A command shows its tool and whether the
chosen agent has it ready.

### Migration

Until this is built, the NetExec commands stay host commands (decided): they work on agents
with `nxc` on the host and fail on the compose agent. Then `netexec-*` move to
`tool: netexec`, and the nmap/ffuf commands to tools wrapping their current images.

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

- One markdown note per project (`projects.pinned_note`, plus who edited it and when) shown
  at the top of the project dashboard; the home dashboard shows the first line of each
  project's note under its name.
- Operators who are members (and admins) edit it in place; changes go to the activity feed
  (`project.note`) and keep a short history so an edit can be undone.
- **Open question:** one note, or a few pinned notes (status, objectives, contacts)?

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
