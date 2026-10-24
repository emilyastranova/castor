# Castor

Kanban task manager and job orchestrator for workflow automation.

Cards on a project board are either **manual tasks** for people or **command tasks** for
agents. Drag a command card into *In Progress* and Castor dispatches it to an agent, which
runs it in a container and streams the output straight back into the card. Cards carry
output logs, screenshots, comments, and custom fields, so the board doubles as the
workflow's execution log.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and the agent protocol.

## Components

| Directory | What it is |
| --- | --- |
| `web/` | SvelteKit UI with a Material 3 design system |
| `server/` | FastAPI API, UI WebSocket, agent gateway, dispatcher (PostgreSQL or SQLite) |
| `agent/` | Python agent that runs jobs in podman/docker containers or on the host |
| `commands/` | YAML command definitions (reusable, typed arguments) |
| `deploy/` | Caddy reverse-proxy config used by `compose.yaml` |

## Quick start (compose)

```bash
cp .env.example .env     # edit the secrets
docker compose up -d --build
```

Open <http://localhost:8080> and log in with the admin credentials from `.env`.
Add `--profile agent` to also run an agent inside the stack.

## Development

Python 3.12+ and Node 22+. One virtualenv at the repo root serves both Python packages.

```bash
python3 -m venv .venv
.venv/bin/pip install -e "./server[dev]" -e "./agent[dev]"
(cd web && npm install)
```

Run the three pieces in separate terminals:

```bash
.venv/bin/castor-server                 # API on :8000, SQLite in ./data by default
```

```bash
cd web && npm run dev                   # UI on :5173, proxies /api and /ws to :8000
```

```bash
.venv/bin/castor-agent --secret change-me-agent-secret --name dev-agent --tags lab
```

Server settings are environment variables prefixed `CASTOR_` (see `server/castor_server/config.py`);
agent flags are listed by `castor-agent --help`. Tests:

```bash
.venv/bin/python -m pytest server/tests
```

## Running an agent on a real box

```bash
pip install ./agent
castor-agent --server ws://castor.example.internal:8080/ws/agent \
             --secret <CASTOR_AGENT_SECRET> --name jumpbox-1 --tags internal,linux \
             --workdir /opt/castor-work
```

With podman or docker present, command cards that name an `image` run inside that image
with `--network host`. Each job gets its own directory under `--workdir` (a temp directory
by default), mounted at `/work`; files under `/work/artifacts/` and a command's declared
outputs are uploaded back to the card. Cards without an image run in the
agent's shell (disable with `--no-allow-host`).

## License

MIT
