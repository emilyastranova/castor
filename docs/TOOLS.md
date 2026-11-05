# Tool environments (design, not built yet)

Status: designed 2026-11-04; next up after the dashboard. The agent stays bare (its image
has only the docker CLI); the tools commands need are described in a library and prepared
on demand.

## The library

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

## Preparing a tool

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

## Server and agents

- `job.run` carries the resolved tool: `{kind: image|build|host, ref, dockerfile?, check?,
  install?}`; the agent reports `job.log` lines for the preparation phase on a `system`
  stream.
- Agents report the tool tags they have cached (hello + heartbeat); the dispatcher prefers
  an agent with the tool ready over one that would build it, and won't send a host-only
  tool to an agent that can't run it.
- Playbook validation checks that every command's tool is defined.

## UI

A Tools page: the library, which agents have each tool cached, **Prepare on agents** (build
or pull ahead of an engagement), and build logs. A command shows its tool and whether the
chosen agent has it ready.

## Until then

The NetExec commands stay host commands (decided): they work on agents with `nxc` on the
host and fail on the compose agent. When this is built, `netexec-*` move to `tool: netexec`,
and the nmap/ffuf commands to tools wrapping their current images.

## References

- NetExec's Docker instructions: <https://www.netexec.wiki/getting-started/installation/using-docker>
- A published image (Parrot): <https://hub.docker.com/r/parrotsec/netexec>
- Hash-tagged images to skip rebuilds: <https://github.com/dpc/docker-source-checksum>
