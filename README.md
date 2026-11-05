<p align="center">
  <img src=".github/assets/banner.svg" alt="Castor" width="100%">
</p>

<p align="center">
  <a href="docs/ARCHITECTURE.md"><b>Architecture</b></a>
  &nbsp;·&nbsp;
  <a href="docs/PLAYBOOK_SPEC.md">Playbook spec</a>
  &nbsp;·&nbsp;
  <a href="commands/README.md">Commands</a>
  &nbsp;·&nbsp;
  <a href="docs/PHASE2.md">Roadmap</a>
  &nbsp;·&nbsp;
  <a href="LICENSE">MIT</a>
</p>

<p align="center">
  <img alt="Python 3.12 or later" src="https://img.shields.io/badge/python-3.12%2B-EDE6E1?style=flat-square&labelColor=131212">
  <img alt="Node 22 or later" src="https://img.shields.io/badge/node-22%2B-EDE6E1?style=flat-square&labelColor=131212">
  <img alt="SvelteKit and FastAPI" src="https://img.shields.io/badge/stack-SvelteKit%20%2B%20FastAPI-EDE6E1?style=flat-square&labelColor=131212">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-EDE6E1?style=flat-square&labelColor=131212"></a>
</p>

Castor is a Kanban board that runs your automation. Cards on a project board are
either **manual tasks** for people or **command tasks** for agents. Drag a command
card into *In Progress* and Castor dispatches it to an agent, which runs it in a
container and streams the output straight back into the card. Parsers turn the
tool's output into typed results (hosts, services, URLs), so the board doubles as
the workflow's execution log.

**Playbooks** chain commands into a graph. Steps run in parallel where they can,
transform steps reshape one tool's results into the next tool's input, and every
step appears on the board as its own card.

> **■ Status: Phase 2 complete.** The board, agents with their own tokens, command
> library, parsers, playbooks with a visual editor, and users with roles all work. Refer
> to [docs/PHASE2.md](docs/PHASE2.md).

---

## How it works

<p align="center">
  <img src=".github/assets/architecture.svg" alt="The browser talks to the Castor server over HTTP and a WebSocket. Agents connect to the server over a WebSocket, receive jobs, run them in podman or docker containers or the host shell, and stream logs and files back." width="100%">
</p>

1. A command card names a command from the **library**: a YAML definition with
   typed arguments, an optional container image, and the outputs to collect.
2. When the card enters *In Progress*, the server queues a **job**. The
   dispatcher sends it to a connected agent that matches the card's tags and has
   the runtime the job needs.
3. The **agent** runs the job in its own `/work` directory, inside a container
   or in the host shell, and streams stdout and stderr back to the card as it
   runs.
4. After the job, the agent uploads the declared output files. The server
   **parses** them (nmap XML, NetExec, ffuf JSON, lines, regex) into typed
   results that show on the card.

Every argument value is shell-quoted when the command line is rendered, so a
value is always one shell word and can never inject shell syntax.

## Playbooks

<p align="center">
  <img src=".github/assets/playbook.svg" alt="A playbook graph: a seed step fans out into three parallel branches; dashed transform steps reshape the data between commands; a report step collects all three branches." width="100%">
</p>

A playbook is a YAML graph of steps. **Command steps** become cards and run on
agents. **Transform steps** run on the server between commands: they filter,
pluck, format, de-duplicate, sort or limit the previous step's results. A step
that references another step's output waits for it, and steps that don't depend
on each other run at the same time.

```yaml
steps:
  - id: sweep
    name: Ping sweep
    command_ref: nmap-ping-sweep
    args: { targets: "{{ inputs.targets }}" }

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
    args: { targets: "{{ steps.live.outputs.ips }}" }
```

The Playbooks page draws the graph live: step status, item counts on each edge,
and the parsed results of every step. If a step fails, only the steps that
depend on it are blocked; the other branches finish. Re-run the failed card to
continue the run.

- [docs/PLAYBOOK_SPEC.md](docs/PLAYBOOK_SPEC.md) is the complete authoring spec.
  It is self-contained, so you can give it to an agent that writes playbooks.
- `POST /api/playbooks/validate` checks a playbook against the command library
  without saving it.
- [`playbooks/pipeline-demo.yaml`](playbooks/pipeline-demo.yaml) is the graph
  above. Its commands invent their output, so it runs in seconds with no network.
  It is good for trying the UI. Its `fail_at` and `empty_at` inputs show failure
  and skip handling.

## Variables

Credentials, SSH keys, listener addresses and API tokens live on the **Variables** page as
typed *sets* (one *kind* per shape of data; make your own kinds for anything else). A
command declares what it needs:

```yaml
vars:
  - name: creds
    kind: credential
command: nxc smb {{ targets }} -u {{ creds.username }} -p {{ creds.password }}
```

and the card or playbook run picks a set. Values never enter the command line or the job
history: secrets and files are encrypted at rest, sent only to the agent running the job
(as environment variables and 0600 files that are deleted afterwards), and masked in the
output if the tool prints them. See [commands/README.md](commands/README.md#variable-sets-credentials-keys-listeners-).

## Quick start

With Docker or Podman Compose:

```bash
cp .env.example .env     # edit the secrets
docker compose up -d --build
```

Open <http://localhost:8080> and log in with the admin credentials from `.env`.
Add `--profile agent` to also run an agent inside the stack.

Add more people under **Users** (admins only). Each person is a **viewer** (read-only),
**operator** (works in their projects and edits the command, playbook and variable
libraries) or **admin** (everything). Admins see every project; everyone else sees the
projects they're a member of (a project's **Members**). Everyone can set a profile picture
(upload, image URL or Gravatar) under their account menu; admins can set anyone's.

### Locked out?

Castor always keeps one active admin: the last one can't be demoted or disabled, and if a
database ever has none, the server makes its longest-standing user an admin when it
starts. A lost password is reset from the server's shell:

```bash
docker compose exec server castor-admin reset-password admin    # compose
.venv/bin/castor-admin reset-password admin                     # dev checkout
```

`castor-admin` also has `list-users`, `enable`, `promote` and `create-admin`.

### Upgrading

The server applies database migrations when it starts. Back up first:

```bash
# Compose (Postgres)
docker compose exec -T db pg_dump -U castor castor > castor-$(date +%F).sql
git pull && docker compose up -d --build
# to restore: docker compose exec -T db psql -U castor castor < castor-YYYY-MM-DD.sql
```

A SQLite database is copied to `data/backups/` automatically before migrations run.
Upgrading to roles keeps everyone's access: users who were admins stay admins, everyone
else becomes an operator, and every existing user becomes a member of every existing
project.

## Development

Python 3.12+ and Node 22+. One virtualenv at the repository root serves both
Python packages.

```bash
python3 -m venv .venv
.venv/bin/pip install -e "./server[dev]" -e "./agent[dev]"
(cd web && npm install)
```

Run the three parts in separate terminals. Start the API first:

```bash
.venv/bin/castor-server                 # API on :8000, SQLite in ./data
```

```bash
cd web && npm run dev                   # UI on :5173, proxies /api and /ws to :8000
```

```bash
.venv/bin/castor-agent --secret change-me-agent-secret --name dev-agent \
                       --allow-host --max-concurrency 4
```

Log in as `admin` / `castor` (from `CASTOR_ADMIN_*`, created on first start).
The server applies database migrations on startup. Server settings are
environment variables prefixed `CASTOR_` (see `server/castor_server/config.py`).
Agent flags are listed by `castor-agent --help`.

| Check | Command |
|---|---|
| Server tests | `.venv/bin/python -m pytest server/tests` |
| Agent tests | `.venv/bin/python -m pytest agent/tests` |
| Lint | `.venv/bin/ruff check server/castor_server agent/castor_agent` |
| Type check (UI) | `cd web && npm run check` |
| Production build (UI) | `cd web && npm run build` |

## Run an agent on a real host

Create a token for it on the **Agents** page (**Enroll agent**). It's shown once, and the
dialog gives you the command:

```bash
pip install ./agent
castor-agent --server wss://castor.example.internal/ws/agent \
             --token <token> --name jumpbox-1 --tags internal,linux \
             --workdir /opt/castor-work
```

Each agent's token can be rotated or revoked on its own, which disconnects that agent at
once. The shared `--secret <CASTOR_AGENT_SECRET>` still works for quick setups until you set
`CASTOR_ALLOW_SHARED_AGENT_SECRET=false`.

The agent's only dependency is `websockets`, so it installs on almost any host
with Python 3.10 or later.

- With podman or docker present, command cards that name an `image` run inside
  that image with `--network host`.
- Each job gets its own directory under `--workdir` (a temp directory by
  default), mounted at `/work`. Files under `/work/artifacts/` and the command's
  declared outputs are uploaded to the card.
- Cards without an image run in the agent's shell. Disable that with
  `--no-allow-host`.
- `--tags` lets a card send its work to specific agents.

## Repository

| Path | Contents |
|---|---|
| [`web/`](web/) | SvelteKit UI with a Material 3 design system |
| [`server/`](server/) | FastAPI API, UI WebSocket, agent gateway, dispatcher, parsers, playbook engine (SQLite or PostgreSQL) |
| [`agent/`](agent/) | The agent: runs jobs in podman/docker containers or the host shell |
| [`commands/`](commands/) | Built-in command definitions (YAML). Format in [`commands/README.md`](commands/README.md) |
| [`playbooks/`](playbooks/) | Built-in playbooks (YAML). Format in [`docs/PLAYBOOK_SPEC.md`](docs/PLAYBOOK_SPEC.md) |
| [`docs/`](docs/) | [Architecture](docs/ARCHITECTURE.md) and agent protocol, [Phase 2 plan](docs/PHASE2.md), [playbook spec](docs/PLAYBOOK_SPEC.md) |
| [`deploy/`](deploy/) | Caddy reverse-proxy configuration for `compose.yaml` |
| [`.github/assets/`](.github/assets/) | README images, made by `build_assets.py` from the AstraLabs brand files |

## License

Castor is released under the MIT License. Refer to [LICENSE](LICENSE).

The AstraLabs name, mark, and wordmark identify the work of Emily Astranova.
The MIT License does not give trademark rights to them.

---

<p align="center">
  <a href="https://astralabs.dev"><img src=".github/assets/footer.svg" alt="AstraLabs" width="240"></a>
</p>
