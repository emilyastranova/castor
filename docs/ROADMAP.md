# Roadmap

What's planned for Castor, grouped by area rather than by date. Nothing here is a promise or
in a fixed order. What's already built is in [the wiki](https://github.com/emilyastranova/castor/wiki)
and, for how it works, [ARCHITECTURE.md](ARCHITECTURE.md#milestones).

## Plugins

### More for plugins
Plugins are built ([PLUGINS.md](PLUGINS.md) has the design; the wiki covers using them). Still to
come:
- **Hooks:** synchronous decisions Castor asks a plugin for, with a deadline (2 s by default): map
  a parser's results to assets and relationships, a playbook transform op, a trigger condition, a
  search provider. For example, a parser for a tool Castor doesn't know, with its asset mapping.
- **Signed releases:** directory releases signed (Sigstore) and checked at install and update,
  once there are publishers to verify, on top of today's SHA-256 checks.
- **More UI slots:** `run.panel` (a playbook run's panel), `project.home` (a tile on a project's
  Home), `settings.page` and `graph.overlay` (on the Relationships graph).
- **For authors:** SDK types generated from Castor's OpenAPI, and a scaffolding CLI for a new
  plugin.

### More repositories
Today the library has two sources: what ships with Castor and the one plugin directory
(`CASTOR_PLUGIN_DIRECTORY_URL`). Admins should be able to add more repositories, such as a team's
own, a vendor's, or a community collection:
- **What a repository offers:** library definitions (tools, commands, parsers, playbooks,
  themes, variable kinds) and its own plugins, described by one index file at its root, like
  `directory.json` today.
- **Adding one:** Settings → Repositories (admins) takes a URL (a Git repository or a plain HTTPS
  address), with a token from the vault for private ones. Castor shows what it offers before
  anything is added, and checks for updates daily as the plugin directory does.
- **Choosing what to use:** subscribe to a whole repository, or take single items. Its content
  shows in the Library and the plugin Browse tab with where it came from (`origin =
  "repo:<id>"`), and it's read-only like built-ins: you copy an item to change it.
- **Trust:** each repository is marked as official, trusted or community. Plugins from one still
  go through the same install review and sandbox, and an unrestricted plugin still needs
  restricted mode off. Updates that change a command line, a tool's image or recipe, or a
  plugin's permissions wait for an admin to approve them; nothing changes under a running board.
- **Conflicts:** two repositories offering the same id are kept apart by origin. A card or step
  names which one it uses, and removing a repository retires its items the way a removed built-in
  is retired (cards keep their command line).
- **Open questions:** whether projects can be limited to some repositories, whether a
  repository's items should be pinned to a version (a Git tag or commit) per project, and signing,
  shared with signed plugin releases.

## Running work

### Ansible playbooks
Run Ansible playbooks from Castor, with their results on the card like any other run. (They're
not Castor's own playbooks, which chain library commands; an Ansible playbook could be one step
of those.)
- **Where they come from:** an Ansible playbook in the library (YAML, edited like other
  definitions) or a Git repository (a path in it and a branch or tag; roles and collections from
  its `requirements.yml`). Repositories could come through "More repositories" above.
- **Where they run:** an `ansible` tool (an image, or a recipe pinning `ansible-core` and the
  collections), on an agent like any command, with `ansible-playbook` in the job's work directory.
  The agent needs to reach the hosts it targets, so the agent picker matters as for commands.
- **Inventory from Castor:** hosts from a target list, from the project's assets (a host query,
  by scope group or tag, with services and facts as host variables), or an inventory file in the
  repository. The rendered inventory is shown before the run.
- **Credentials from the vault:** SSH keys, become passwords and Ansible Vault passwords come from
  variable sets as files and environment, never in the command line or the inventory as stored;
  secrets are masked in the log as for every job.
- **Arguments:** extra vars, tags and skip tags, limit, and check mode (`--check --diff`) as typed
  arguments on the card. A check run could be a preview a person approves before the real run.
- **Results:** a JSON callback read by a built-in parser into per-host, per-task results (ok,
  changed, failed, unreachable, skipped), shown as a table on the card, compared between runs
  (what changed since last time), and feeding assets (facts gathered, hosts unreachable).
- **In playbooks and triggers:** an Ansible playbook as a step of a Castor playbook, with
  `on_failure` (retry the hosts that failed, or only the unreachable ones), and as something a
  trigger can start.
- **Open questions:** whether it's a new kind of library item or a command definition with a
  built-in `ansible` kind; how much of AWX/Ansible Automation Platform to talk to instead of
  running Ansible directly; and how this meets "Agent types from plugins" (Ansible inventory as
  agents).

### More for network routes
[Routes](https://github.com/emilyastranova/castor/wiki/Routes) send jobs through SSH jumps and SOCKS
and HTTP proxies today. Still to come:
- **Tunnels as hops:** Chisel, ligolo-ng and WireGuard, set up and torn down by the agent like an SSH hop.
- **graftcp** for static binaries on an agent's host (in containers the route box already catches them).
- **Routes on SSH hosts and plugin machines:** the chain would have to start on that machine.
- **MCP actions through a route:** an MCP server reached through one (HTTP actions already go through agents).
- **Per-target routes in one job:** a route map by subnet, like a routing table.
- **Live connections:** what's going through a route right now, per agent, on the Routes page.

### Triggers beyond assets
Today a trigger fires on new or changed [assets](https://github.com/emilyastranova/castor/wiki/Assets), and on new
relationships between them (a credential that works on a new host).
More events should be able to start a command or playbook:
- a job or run finishing (succeeded, failed, or with a parsed result that matches);
- a card moving to a column;
- a schedule (cron, or "every N hours" while a project is active);
- an incoming webhook (later): another system calls Castor to start a run. It must authenticate
  (a per-trigger secret or a signed request, never an open URL), and its body becomes the run's
  inputs through the same typed arguments an action has;
- a listener seeing something (below).

The same rules as now apply: scope checks, approval when wanted, and a record of each firing.

### Decision models
Castor asks text models for judgements today (the playbook resolver's retry/skip/ask, HTTP
recovery moves, sorting suggested assets) and parses a JSON answer with a confidence the model
made up. Decision models answer typed questions with calibrated probabilities instead and produce
no text, which fits these better. Start with Google's [Decisions API](https://aistudio.google.com/docs/decisions-api)
(Gemini Decision 1): a request is shared input (text, JSON, PDFs, images, audio, video) and a map
of questions, each a yes/no (`noul`, the probability of yes), a pick-one (`choice`, every option's
probability) or a rating (`score`, over 2 to 255 described levels), with a confidence per answer.
- **A provider kind:** decision providers in Settings → AI next to text models (the Gemini API key
  in the vault, REST through httpx like the others), and a `decisions` purpose chain. Without one,
  Castor falls back to asking a text model, and says that the confidence isn't calibrated.
- **Branching in playbooks:** a `decide` condition in `when`/`expect`: a question about a step's
  output (or a screenshot it took), with a threshold (`yes_above: 0.8`), and for routing a
  `choice` whose option picks the branch. Below the step's confidence, it asks a person, as
  `on_failure: ask` does.
- **Castor's own judgements:** the resolver's action (retry, continue, skip, fail, ask) and the HTTP
  recovery move become choice questions with real confidence, compared with the step's
  `ai.confidence`; a text model still writes a corrected command or a parser draft. Sorting
  suggested assets (real, noise, unsure) and "is this the format the parser reads?" too.
- **Rating results:** a `score` question with a rubric (a finding's severity, how interesting a
  web page looks, whether a screenshot is a login page) on parsed results, to sort and filter them
  or feed triggers, kept with the result.
- **The same rules:** what goes to the model is masked like today (no variable values), job output
  and pictures are untrusted input (they can argue for their own answer, so high-stakes branches
  keep a threshold and a person), every call is logged in AI usage with its probabilities, and a
  decision on a step shows its answer and confidence.
- **Open questions:** other decision models later (open models scored from their token
  probabilities, through Ollama or a compatible server, for instances that can't reach Google);
  learning thresholds from the decisions people made by hand; and how answers show up in Changes
  between runs.

### Limits on AI use
Every AI call is already recorded (`ai_calls`: purpose, provider, model, tokens, who asked).
Admins should be able to cap that use, so a busy playbook or a chatty feature can't run up a bill
or use up a shared key:
- **Per provider:** requests and tokens per minute, hour, day or month (input and output
  separately or together), and optionally a spending cap from a price per million tokens the
  admin enters, set on the provider in Settings → AI.
- **Per category of use in Castor:** the purposes Castor calls models for (generating library
  items, the playbook resolver, HTTP recovery, AI summaries, reading pictures and assets
  mentioned, drafting parsers, explaining failures, and decision models later), each with its own
  budget, so summaries can't use what the resolver needs.
- **Narrower budgets too:** per project (a client engagement with its own allowance), per person
  and per plugin, on top of the provider's and the category's; a call needs room under every one
  that applies, as HTTP actions' rate limits work today.
- **When a limit is reached:** the purpose's chain falls back to its next model (another provider
  with room), else the call waits for room if it's short (a rate) or is refused with the reason if
  it's a quota (the day's or month's tokens used up). Automatic uses (summaries, picture reading)
  quietly stop until the period resets; a paused playbook step waits for a person and says why;
  admins are notified at a warning level (80 % used) and when it's reached.
- **Visible:** Settings → AI shows use against each limit for the period, by provider, category,
  project and person, from the records Castor already keeps.
- **Open questions:** whether to read limits and remaining quota from the providers' own
  rate-limit headers as well; prices per model kept by admins or shipped as defaults; and whether
  a person can be allowed to go past a budget once ("use it anyway") or only an admin.

## Reporting and the interface

### Reporting workflows
Turn a project's findings into a deliverable:
- **Templates:** report templates in Markdown with placeholders for assets, results, notes and
  screenshots.
- **Findings:** findings with severity, evidence (attachments, output) and status, tied back to
  the cards and assets they came from.
- **Review:** draft, review and final, with comments.
- **Export:** to Markdown, HTML and PDF. A project's timeline already exports to HTML and PDF
  (Home → Export), which the report export can build on.

### Renaming library tags
Card tags (per project) and agent tags can be renamed and merged; library tags (commands and
playbooks) can't yet, because they live in each definition's YAML and the built-in ones are
re-read from files at startup.

## More

Further ideas, smaller or less settled:

- **Secrets from an external vault** (HashiCorp Vault, 1Password, cloud secret managers) as
  variable sets, read at dispatch.
- **Agent pools and auto-scaling:** start agents on demand (containers or cloud instances) when
  jobs queue for a tag, and stop them when idle.
- **Retention:** how long to keep job logs, artifacts and activity, per project.
- **Postgres in CI**, so the Postgres path gets the same test coverage as SQLite.
