# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Security engineers and IT/ops teams who automate repeatable technical pipelines. The flagship
use is offensive-security work (penetration tests and assessments run as projects with
codenames, scope, targets and credentials), but nothing in the product is limited to it: any
team that runs command-line tools in sequence and tracks the results fits.

People work in teams with roles (viewer, operator, admin) and project membership. One person
runs a scan while another reviews findings, answers a paused playbook step, or picks up an
assigned card.

## Product Purpose

Castor is a Kanban board that runs the work. A card is either a manual task for a person or a
command task for an agent; dragging a command card into the active column dispatches it to an
agent, which runs the tool and streams output back into the card. Parsers turn output into
typed results, and those become the project's assets.

Success: a team plans, runs and records an engagement in one place, so the board doubles as
the execution log and nobody copies output between a terminal, a tracker and a report.

## Positioning

- **The board runs the work.** The card is the execution, not a ticket about it: output,
  parsed results, artifacts and discovered assets land on the card that ran.
- **Playbooks with judgement.** Graphs of tools with branching, expectations and decisions;
  an AI resolver can retry, continue or skip failed steps, while a changed command waits for a
  person by default.
- **Open source (MIT)** and self-hosted: the team's data, credentials and agents stay on its
  own infrastructure.

## Operating Context

- Long sessions during an engagement, often for hours, frequently in dark mode on large
  monitors.
- Also used on small laptop screens on-site at a client, and sometimes on a phone to check a
  run.
- Agents run on hosts inside target or client networks (jump boxes, lab machines); they are
  deliberately bare (one Python dependency) and pull or build tool environments on demand.
- Scripts, CI pipelines and other tools drive Castor through its REST API with scoped personal
  tokens, and AI assistants work with it through its built-in MCP server.

## Capabilities and Constraints

- Board with manual and command cards, comments, attachments, assignees, @mentions, duplicate.
- Command library (YAML, typed arguments, every value shell-quoted), tool environments, parsers
  (nmap, NetExec, ffuf, lines, regex), assets with triggers, search everywhere (Ctrl/Cmd+K).
- Playbooks: DAG of command and transform steps, conditions, `expect`, `on_failure`
  (stop/continue/ask/ai); a run is one card with its live graph.
- Variables and vault: credentials, keys and secrets never enter command lines or job history;
  they're masked in output.
- Interactive jobs: a real terminal (xterm.js over a pty) people can take control of; alerts
  when an unattended run stops at a prompt.
- Notifications, chat/webhook alerts, home and project dashboards, people pages, import and
  export between instances, per-user theme seed colour, scoped API tokens.
- Stack (existing): SvelteKit 5 + Tailwind v4 + an in-house Material 3 component library
  (`web/src/lib/m3`), FastAPI + SQLAlchemy (SQLite or PostgreSQL), Python agent.
- Terminology: project, board, column (backlog/todo/active/review/done roles), card (task),
  job, run, step, command, tool, parser, output, asset, variable kind/set, agent, playbook.

## Brand Commitments

- Name: Castor. Made by AstraLabs (Emily Astranova); the AstraLabs name, mark and wordmark
  are trademarks outside the MIT licence. README imagery comes from the brand repo
  (`../astralabs_design`) via `.github/assets/build_assets.py`.
- Voice in the UI and docs: plain, direct and specific; British spelling ("colour").

## Evidence on Hand

- README screenshots from a seeded demo instance (`.github/assets/screenshots/`,
  `seed_demo.py`, `capture.py`).
- No-network demo playbooks: `pipeline-demo`, `branching-demo`, `ai-resolver-demo`.
- None of these exist and must not be invented: customers, testimonials, usage numbers,
  benchmarks, pricing, hosted offering.

## Product Principles

1. The card is the source of truth: what ran, what it printed, what it found and who decided
   what all live on it.
2. People stay in control. Automation retries and continues; anything that changes what runs
   on a client's network waits for a person unless they opted in.
3. Secrets stay secret: never in command lines, logs, search, exports without a passphrase,
   or AI prompts.
4. Self-hosted and small at the edge: agents stay bare, tools come on demand.
5. Everything the UI does, the API can do, with credentials that can do only what they're for.

## Accessibility & Inclusion

- WCAG 2.2 AA is the floor: contrast, full keyboard use, screen-reader labels.
- Built for long sessions: dark mode is first-class (light mode too), and each person's theme
  seed colour must keep AA contrast.
- Works on small laptop screens; a phone must be enough to check a run and answer a decision.
- Motion honours prefers-reduced-motion.
