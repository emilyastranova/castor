<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset=".github/assets/banner-light.svg">
    <img src=".github/assets/banner.svg" alt="Castor: the Kanban board that runs the work" width="100%">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/emilyastranova/castor/wiki"><b>Documentation</b></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/emilyastranova/castor/wiki/Installation">Install</a>
  &nbsp;·&nbsp;
  <a href="docs/PLAYBOOK_SPEC.md">Playbook spec</a>
  &nbsp;·&nbsp;
  <a href="docs/ARCHITECTURE.md">Architecture</a>
  &nbsp;·&nbsp;
  <a href="docs/ARCHITECTURE.md#roadmap">Roadmap</a>
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
the workflow's execution log, and what they find becomes the project's **assets**: one
record per host, service or URL that every tool adds to, with triggers that act on new ones.

**Playbooks** chain commands into a graph. Steps run in parallel where they can,
transform steps reshape one tool's results into the next tool's input, and a run shows
on the board as one card that opens into its live graph, decisions and steps.

## A look around

<p align="center">
  <img src=".github/assets/screenshots/dashboard.png" alt="A project's Home: a greeting with what's running, four colored pinned notes (Status, Objectives, Scope, Contacts), today's numbers, a timeline of comments and card changes, and a Needs attention panel with mentions, a step waiting for a decision and a failed job." width="100%">
</p>

You work in one project at a time (pick it once; Castor remembers it). Its Home is the
dashboard: what changed, what's running, and what's waiting on you (mentions, playbook
decisions, failed runs, jobs at a prompt). The Inbox covers every project.

<table>
  <tr>
    <td width="50%"><img src=".github/assets/screenshots/board.png" alt="A project board with Backlog, To Do, In Progress and Review columns; command cards show their command line and run status, playbook runs show as single cards with a progress bar, and assignees show as avatars."></td>
    <td width="50%"><img src=".github/assets/screenshots/card.png" alt="A playbook run's card opened on its Run tab: the run's state and progress, its inputs, a step waiting for a decision with retry, continue, skip and fail buttons, and the live graph of steps."></td>
  </tr>
  <tr>
    <td><b>The board.</b> Manual and command cards; dragging a command card into In
    Progress runs it on an agent.</td>
    <td><b>A playbook run</b> is one card: its progress, decisions to answer and the live
    graph; each step opens as its own card with its console and results.</td>
  </tr>
  <tr>
    <td colspan="2"><img src=".github/assets/screenshots/playbook.png" alt="A finished playbook run drawn as a graph: a seed step fans out to a service scan, an SMB scan and DNS names; transform steps reshape results between commands; a report collects the branches."></td>
  </tr>
  <tr>
    <td colspan="2"><b>Playbooks.</b> A run drawn as a graph: commands, the transforms
    between them, and each step's status and outputs.</td>
  </tr>
  <tr>
    <td colspan="2"><img src=".github/assets/screenshots/assets.png" alt="A project's asset inventory in the light theme: filter chips for hosts, services, URLs, domains, users, credentials and shares, and a table of hosts and services with their details, links and when they were last seen."></td>
  </tr>
  <tr>
    <td colspan="2"><b>Assets.</b> Every host, service, URL and credential your jobs find,
    merged into one inventory you can filter and feed back into commands; shown here in the
    light theme.</td>
  </tr>
  <tr>
    <td colspan="2"><img src=".github/assets/screenshots/phone.png" alt="Three phone screens: a project's Home with its pinned notes, the board with a New card button, and the More sheet listing every destination above a bottom navigation bar."></td>
  </tr>
  <tr>
    <td colspan="2"><b>On a phone</b>, a bottom bar replaces the rail, so checking a run or
    answering a decision works one-handed.</td>
  </tr>
</table>

---

## How it works

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset=".github/assets/architecture-light.svg">
    <img src=".github/assets/architecture.svg" alt="The browser talks to the Castor server over HTTP and a WebSocket. Agents connect to the server over a WebSocket, receive jobs, run them in podman or docker containers or the host shell, and stream logs and files back." width="100%">
  </picture>
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

Argument values are shell-quoted when the command line is rendered, credentials reach the
job as environment variables and files rather than in the command, and a card can run under a
real terminal you take control of. The [wiki](https://github.com/emilyastranova/castor/wiki) covers all of it.

## Playbooks

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset=".github/assets/playbook-light.svg">
    <img src=".github/assets/playbook.svg" alt="A playbook graph: a seed step fans out into three parallel branches; dashed transform steps reshape the data between commands; a report step collects all three branches." width="100%">
  </picture>
</p>

A playbook is a YAML graph of steps. **Command steps** run on agents; **transform steps** run
on the server and reshape one step's results into the next step's input. Steps that don't
depend on each other run at the same time, branches can depend on conditions, and a step can
pause for a person (or AI) to decide what happens next. Read the
[Playbooks guide](https://github.com/emilyastranova/castor/wiki/Playbooks), or [docs/PLAYBOOK_SPEC.md](docs/PLAYBOOK_SPEC.md), the
self-contained authoring spec you can hand to an agent that writes playbooks.

### What's included

Each command runs in its own tool container, pulled or built on an agent the first time it's
needed, so agents stay bare.

| Area | Commands (tool) |
| --- | --- |
| Network discovery | Ping sweep, top TCP ports, UDP scan, service/version scan, SMB port 445 (nmap) |
| Web | Web probe with titles and technologies (httpx), vulnerability scan (Nuclei), directory brute force with SecLists (ffuf), security headers (curl) |
| TLS and SSH | TLS configuration scan (testssl.sh), certificate expiry (nmap), SSH configuration audit (ssh-audit) |
| DNS and recon | Passive subdomain discovery (subfinder), name resolution (dnsx) |
| Windows / SMB | SMB signing and host info, authenticated checks over any protocol, share listing (NetExec) |
| Containers and ops | Image vulnerability scan (Trivy) |

Built-in playbooks: **Network baseline**, **Web attack surface**, **TLS review**, **SMB signing
discovery** and **SSH version discovery**. Add your own [commands](https://github.com/emilyastranova/castor/wiki/Commands),
[tools](https://github.com/emilyastranova/castor/wiki/Tools) and [parsers](https://github.com/emilyastranova/castor/wiki/Parsers) in the Library.

## Quick start

With Docker or Podman Compose:

```bash
cp .env.example .env     # edit the secrets
docker compose up -d --build
```

Open <http://localhost:8080> and sign in with the admin credentials from `.env`. Add
`--profile agent` to also run an agent inside the stack; [Agents](https://github.com/emilyastranova/castor/wiki/Agents) shows how to connect one on
another host. [Installation](https://github.com/emilyastranova/castor/wiki/Installation) covers upgrades, backups, a lost admin
password and running from source.

## Documentation

The [wiki](https://github.com/emilyastranova/castor/wiki) is the manual:

- **Running Castor:** [Installation](https://github.com/emilyastranova/castor/wiki/Installation), [Agents](https://github.com/emilyastranova/castor/wiki/Agents),
  [Configuration](https://github.com/emilyastranova/castor/wiki/Configuration), [Security](https://github.com/emilyastranova/castor/wiki/Security),
  [Troubleshooting](https://github.com/emilyastranova/castor/wiki/Troubleshooting)
- **Using it:** [Projects and Boards](https://github.com/emilyastranova/castor/wiki/Projects-and-Boards), [Cards](https://github.com/emilyastranova/castor/wiki/Cards),
  [Jobs and Output](https://github.com/emilyastranova/castor/wiki/Jobs-and-Output), [People and Notifications](https://github.com/emilyastranova/castor/wiki/People-and-Notifications),
  [Assets](https://github.com/emilyastranova/castor/wiki/Assets), [Search and Shortcuts](https://github.com/emilyastranova/castor/wiki/Search-and-Shortcuts),
  [On a phone](https://github.com/emilyastranova/castor/wiki/Using-Castor-on-a-Phone)
- **The library:** [Commands](https://github.com/emilyastranova/castor/wiki/Commands), [Tools](https://github.com/emilyastranova/castor/wiki/Tools),
  [Parsers](https://github.com/emilyastranova/castor/wiki/Parsers), [Variables and the Vault](https://github.com/emilyastranova/castor/wiki/Variables-and-the-Vault)
- **Automation:** [Playbooks](https://github.com/emilyastranova/castor/wiki/Playbooks), [AI](https://github.com/emilyastranova/castor/wiki/AI), [Alerts](https://github.com/emilyastranova/castor/wiki/Alerts),
  [API and Tokens](https://github.com/emilyastranova/castor/wiki/API-and-Tokens), [MCP Server](https://github.com/emilyastranova/castor/wiki/MCP-Server),
  [Import and Export](https://github.com/emilyastranova/castor/wiki/Import-and-Export)

Contributors: [running from source](https://github.com/emilyastranova/castor/wiki/Installation#running-from-source-development) has the
dev setup, tests and checks; [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) has the data model, events and the agent
protocol.

## Repository

| Path | Contents |
|---|---|
| [`web/`](web/) | SvelteKit UI with a Material 3 design system |
| [`server/`](server/) | FastAPI API, UI WebSocket, agent gateway, dispatcher, parsers, playbook engine (SQLite or PostgreSQL) |
| [`agent/`](agent/) | The agent: runs jobs in podman/docker containers or the host shell |
| [`tools/`](tools/) | Built-in tool environments (YAML): images or build recipes commands run in |
| [`commands/`](commands/) | Built-in command definitions (YAML). Format in [`commands/README.md`](commands/README.md) |
| [`playbooks/`](playbooks/) | Built-in playbooks (YAML). Format in [`docs/PLAYBOOK_SPEC.md`](docs/PLAYBOOK_SPEC.md) |
| [`docs/`](docs/) | [Architecture](docs/ARCHITECTURE.md), agent protocol and roadmap, [playbook spec](docs/PLAYBOOK_SPEC.md) |
| [`deploy/`](deploy/) | Caddy reverse-proxy configuration for `compose.yaml` |
| [`.github/assets/`](.github/assets/) | README images, made by `build_assets.py` from the AstraLabs brand files |

## License

Castor is released under the MIT License; see [LICENSE](LICENSE).

The AstraLabs name, mark, and wordmark identify the work of Emily Astranova.
The MIT License does not give trademark rights to them.

---

<p align="center">
  <a href="https://astralabs.dev"><img src=".github/assets/footer.svg" alt="AstraLabs" width="240"></a>
</p>
