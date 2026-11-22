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

### More for network routes
[Routes](https://github.com/emilyastranova/castor/wiki/Routes) send jobs through SSH jumps and SOCKS
and HTTP proxies today. Still to come:
- **Tunnels as hops:** Chisel and ligolo-ng, set up and torn down by the agent like an SSH hop.
- **WireGuard, Tailscale and Headscale as hops:** a layer-3 hop, so UDP and ICMP go through too (the route box routes
  them into the tunnel instead of dropping them). WireGuard with its config and keys in the vault (the box brings the
  interface up in its own network namespace, so the agent's host is untouched); Tailscale or a self-hosted Headscale
  with an auth key (ephemeral and tagged, so the box's node disappears when the route stops), the hop being a
  tailnet node or subnet router that reaches the targets. Peers and exit nodes shown in the route editor, the
  tunnel's handshake and latency in tests.
- **Hop types from plugins:** a plugin declares a hop type in its manifest (name, icon, its fields and which are
  secrets), and the route editor offers it next to SSH and the proxies. The plugin's code says how to bring the hop up
  and down and what it gives the hop after it (a SOCKS address, or a network interface for a layer-3 hop); the route
  box runs it, sandboxed like other plugin code, with its secrets from the vault. Tests and host-key-style trust work
  the same for plugin hops.
- **Real DNS through the route:** today the route box answers lookups with stand-in addresses (198.18.0.0/15)
  that turn back into names when a TCP connection follows, so a tool that only looks names up (MX, TXT, SRV,
  the real A record) gets nothing useful. A per-route option would forward each query as DNS over TCP through
  the chain to a resolver named at the far end (the jumpbox's, a domain controller), dns2socks-style, and pass
  the real answer back. Stand-ins could stay for names the resolver doesn't know, and
  tools such as dnsx would no longer need their own proxy flag.
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

### Quick slots: a hotbar for what you paste all day
During an engagement the same few values go into the clipboard over and over: the test account's username, its UPN,
the domain, the DC's address, the client's VPN portal. Quick slots put them one click away, like a game's hotbar or
a macro pad, in the unused space on the right of a project's Home.
- **A grid of slots**, each with a colour (and a number), a short label and what it holds. Click one and its value is
  on the clipboard, with a snackbar saying which ("Copied UPN"). The value itself stays hidden until hovered or
  revealed, so it's fine to have on screen while sharing it.
- **Keyboard first:** Alt+1…9 copies a slot from anywhere in the project, and the command palette finds them by label.
- **Where values come from:** typed in, or a field of a variable set (so a secret comes from the vault, is never stored
  in the slot, and copying it is recorded like revealing a secret), an asset's field, or a short template mixing them
  (`{{ creds.username }}@{{ domain }}`).
- **More than copying:** a slot can open a link (the client's portal, a ticket, a report), copy and open at once, put
  its value into the comment or card being written, start a card or playbook, or run a plugin action, so it becomes a
  small macro pad for the project.
- **Whose slots:** the project's (shared with everyone on it) and your own on top, set per person; drag to rearrange,
  right-click to edit. On a phone they're a row of chips at the top of Home.
- Over the API and MCP like everything else, so an assistant can read a slot's label and use it without seeing a
  secret value.

## More

Further ideas, smaller or less settled:

- **Secrets from an external vault** (HashiCorp Vault, 1Password, cloud secret managers) as
  variable sets, read at dispatch.
- **Agent pools and auto-scaling:** start agents on demand (containers or cloud instances) when
  jobs queue for a tag, and stop them when idle.
- **Retention:** how long to keep job logs, artifacts and activity, per project.
- **Postgres in CI**, so the Postgres path gets the same test coverage as SQLite.
