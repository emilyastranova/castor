# Roadmap

What's planned for Castor, grouped by area rather than by date. Nothing here is a promise or
in a fixed order. What's already built is in [the wiki](https://github.com/emilyastranova/castor/wiki)
and, for how it works, [ARCHITECTURE.md](ARCHITECTURE.md#milestones).

## Plugins

### A plugin system
Let anyone extend Castor, the way Obsidian's community plugins work: plugins that add library
content, actions on cards and assets, event handlers, hooks, settings and panels in the UI,
published in a public directory and installed by admins. They run sandboxed (a Deno process with
only the permissions they declare); one that needs more is marked unmistakably and stays off until
an admin allows unrestricted plugins. The design and phases are in [PLUGINS.md](PLUGINS.md).
Phases A to D are built (sandboxed plugins with actions, settings, storage, events, declared
network access and their own pages in sandboxed frames; unrestricted plugins behind restricted
mode, with safe mode and page scripts; Settings → Plugins; the Nessus import, Shodan enrichment,
BloodHound bridge, DOCX report and Watch folder), and plugins can add asset types and relationship
kinds (Source Code Review: repositories, source files and symbols, with imports, calls and data
flow). Phase E is built too: the plugin directory (its own repository,
[castor-plugins](https://github.com/emilyastranova/castor-plugins), which also holds the official
plugins), browsing and installing from it, daily update checks, publisher pages, and packs of
library content. Still to come: hooks, and signed releases.

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

### Agent types from plugins
Jobs run on Castor's own agents, SSH hosts, or (for HTTP actions) the server. A plugin should be
able to add another kind of agent that reaches machines through a system that already manages
them: SaltStack minions through the Salt master's API, Puppet nodes through Bolt or PuppetDB,
Ansible inventory, a cloud's run-command service, a remote management tool's API.
- **How it fits:** the same way SSH hosts do. A plugin-provided agent registers with the gateway
  and speaks the agent protocol, so the dispatcher, selectors (an agent, tags, every agent with a
  tag), project limits, logs, cancel, artifacts, parsers and playbooks all work unchanged. The
  plugin only turns `job.run` into its system's call (`cmd.run` on a minion, a Bolt task) and
  streams the output back.
- **What a plugin declares:** an agent type in its manifest (a name, an icon, the settings an
  admin fills in, such as the Salt master's URL and a token from the vault, and what it can run:
  shell commands, containers or not), and a handler in the SDK for running, cancelling and
  reporting jobs.
- **Machines as agents:** the agent type lists the machines it reaches (minions, nodes), and each
  becomes an agent in Castor with tags from its own system (Salt grains, Puppet facts, inventory
  groups), refreshed as the system changes. Admins choose which machines to bring in and which
  projects may use them, as for any agent.
- **Settings and the Agents page:** Agents → Add agents from a plugin, next to Enroll agent and Add
  SSH host; each agent shows the type it came from and whether its system is reachable.
- **The same rules:** variables reach the job the way they reach SSH hosts (never in a command
  line), secrets are masked in logs, and the plugin runs in its sandbox with the network access it
  declares. One that needs a local CLI (Bolt, `salt-call`) needs an unrestricted plugin, behind
  restricted mode like any other.
- **Open questions:** whether one Castor agent per machine scales to large fleets (thousands of
  minions), or the agent type should be one agent that fans out by target; how tools (images,
  recipes, host installs) map onto machines Castor doesn't prepare itself; and whether the
  official SaltStack and Puppet plugins live in castor-plugins.

## People and access

## Cards and the board

### Cards with subtasks
A board fills up fast when every small piece of work is its own card. A card should be able to
be an over-arching activity ("Update homelab LXCs", "Patch the DMZ hosts", "Q3 external
retest") with subtasks inside it, so the board and Home show the activity once, not twenty small
cards. Playbook runs already work this way (one card on the board, its steps hidden, a Run tab
with their progress), so this reuses that look and machinery.

The first part is built: one level of subtasks (`tasks.parent_id`), hidden from the board, with
the activity's progress on its card; a Subtasks tab (quick add, cards from the board, order, make
one its own card again, Run all); subtasks opening on top of the activity; the activity following
its subtasks (a project setting); archive, restore, duplicate, delete and export taking them into
account; and **Group cards into one** (the project menu, `POST /api/projects/{id}/consolidate`,
MCP `consolidate_cards`) for tidying a board. Still to come: the rest of this list (many at once
from a target list, a graph of their dependencies, dragging onto an activity, Home's roll-up,
playbooks).
- **On the board:** the activity is one card with its progress (5 of 12 done, 1 failed, 2
  running), its earliest due date and who's working on it, like a run card. Subtasks don't show as
  cards of their own (`parent_id`, filtered out like step cards), unless a filter asks for them.
- **Inside the card:** a **Subtasks** tab in the style of a playbook run's panel: the subtasks
  with their status, assignee, due date and latest run, in order or as a graph when they depend on
  each other (Depends on between subtasks), each opening on top of the activity as step cards do.
  Quick add (type a title, Enter), reorder by dragging, tick a manual one done in place.
- **Subtasks are real cards:** manual or command (a library command, an HTTP or MCP action),
  with their own comments, files, assignees, due dates, runs and results; they can wait on each
  other, and commands run like any card's.
- **Many at once:** one subtask per target from a target list or an asset query (one per LXC,
  one per host in a scope group), all running the same command, like a playbook's `for_each`;
  **Run all**, with how many at once, or one after another.
- **The activity moves itself (optional):** to In Progress when a subtask starts, and to Review
  or Done when every subtask is done (or skipped); a failed subtask shows on the card and in
  Home's "needs attention" without moving it back.
- **Moving work in and out:** drag a card onto an activity to make it a subtask, turn a subtask
  back into its own card, and turn a card into an activity by adding a first subtask. Archiving or
  duplicating an activity takes its subtasks along (a duplicate as fresh, unstarted ones), and
  exports carry them.
- **Home and the feed:** subtasks' activity rolls up under the activity ("3 subtasks of Update
  homelab LXCs finished"), counts on Home count the activity once, and people's own subtasks still
  show in "yours".
- **From and to playbooks:** save an activity's subtasks as a playbook to repeat it next month,
  or start an activity from a playbook, so a playbook run and an activity are one idea on the
  board.
- **Open questions:** how deep it nests (one level of subtasks, or subtasks of subtasks); whether
  a subtask can sit in a column of its own on the board when someone wants it there; and how
  search and the people pages show subtasks.

## Running work

### Actions beyond commands
Today the work a card or playbook step does is a shell command on an agent. Look into a general
kind of action that's part of Castor itself, of which a command is one sort:
- **Other sorts:** an HTTP/API call made by the server (a ticketing system, a scanner's API, a chat
  message), a tool call through an MCP server, or a plugin action, with typed inputs and outputs
  like a command's arguments and parsed results.
- **From plugins:** plugins could add new sorts of action (manifest-declared, run in their sandbox),
  usable on cards and as playbook steps next to commands, with their outputs feeding later steps,
  parsers, assets and triggers.
- **The same rules:** permissions and scope checks, approvals, secrets only through the vault and
  never rendered into what's stored, logs and results on the card, and the activity feed.
- **Design:** [ACTIONS.md](ACTIONS.md) covers where each sort runs, how retries, cancel and
  `on_failure` apply to something that isn't a process, how the library and the playbook editor
  show them, and the phases. All five phases are built: HTTP actions the server sends, with paging,
  batches, rate limits, retries and waiting, sent through an agent's network when a card chooses
  one, AI recovery for playbook steps, MCP actions (Castor as an MCP client, over HTTP or with
  stdio servers agents run), and plugin actions (a plugin's library actions on cards and as
  steps), and approvals (`confirm: true`, or a card that asks before each run). The leftovers are
  built too: Try it in the Library, a step's own rate limit and sender, limits' use shown, the older
  SSE transport for MCP, MCP servers in exports, stdio servers per project, and large paged results
  as JSON lines.

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

### Long-running background tasks
Jobs that keep running and report as they go, such as a packet capture, a Responder-style
listener, or a log tail:
- **What's new:** a "service" kind of command that runs until stopped, survives the card
  leaving In Progress, and shows as running on its agent.
- **Matches:** its output is parsed continuously, and a match can fire a trigger, for example
  a hash captured or a hostname seen in traffic.
- **Control:** start, stop and restart from the card, and a per-agent limit on how many run at once.

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

## Results and assets

Today each run's results stay with their card, Changes compares one run with an earlier run of
the same card or playbook, and assets collect what parsers recognise. These items make results
add up across runs, cards, scopes and vantage points, so the project shows what's true now and
how it got there.

### One picture from many scans
Two Nmap scans of overlapping scopes, a GoWitness run per subnet, a rescan a week later: the
results should combine into one view of the environment, wherever and however they were run.
- **Merged by identity:** every result with a known identity (a host, a service `host:port`, a
  URL, a share) folds into one asset, the way hosts seen by name and address are folded today.
  Overlapping scopes don't duplicate anything; a host scanned twice gets two observations.
- **Observations, not overwrites:** each asset keeps a dated history of what each run saw
  (`port 443 open` from scan A on the 3rd, `closed` from scan B on the 10th), with the card, job
  and agent it came from. The asset shows the latest state, and its history shows each change.
- **Seen and not seen:** a run whose scope covered an asset but didn't find it records that as
  well ("not seen by the 10th's scan"), which is different from never having been scanned.
  Scope comes from the job's targets, so a scan of 10.0.1.0/24 says nothing about 10.0.2.5.
- **Project-wide changes:** a Changes view for the whole project over a date range, not only per
  card: new hosts, ports that opened or closed, pages whose title, status or screenshot changed,
  services that went away. It filters by asset type, scope group and source, and can feed a
  trigger ("a new port opened in scope") and reports.

### Richer assets
Assets are mostly fields of text today. Scans like GoWitness, httpx or a TLS check produce more:
- **Pictures and files on an asset:** a web page's screenshot, a certificate, a response's
  headers and body, kept as the asset's evidence (with the job that captured it) rather than only
  as a job artifact. A parser output can say which file belongs to which asset.
- **Visual changes:** screenshots of the same URL compared over time (shown side by side, and a
  perceptual hash so "the page looks different" can be a change and a trigger), along with the
  title, status code, technologies and favicon hash.
- **A gallery:** web assets shown as a grid of screenshots, grouped by what they look like (the
  same login page on 40 hosts is one group), filterable like the Files page.
- **Built-in support** for GoWitness (a command, a tool and a parser that maps its results and
  screenshots onto URL assets), next to httpx.

### Comparing from different vantage points
Where something is reachable from matters as much as whether it's up: the same scan from five
agents in five network segments should answer "which VLANs can reach this, and what does each
see?"
- **Run it from everywhere:** a card or playbook already fans out to every agent with some tags
  (`{tags, all: true}`). Each result stays labelled with its agent and that agent's network
  segment (an agent setting, e.g. "VLAN 20 / user LAN"), and the batch's results can be compared
  side by side.
- **A reachability matrix:** for each host, service or URL, which segments reached it and what
  each saw: open from the server VLAN and filtered from the user LAN, or a login prompt from the
  guest network but none from the management network, or different certificates or pages.
- **Vantage point on every observation:** assets record where each observation came from, so the
  matrix builds up from ordinary runs too, not only from fan-out runs made for the purpose.
- **Differences as findings:** a difference between segments that shouldn't be there (a
  management interface reachable from the guest network) can be flagged, fire a trigger, and go
  into a report as a segmentation finding.

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

- **Scheduled runs** of a card or playbook (see [triggers](#triggers-beyond-assets)).
- **Secrets from an external vault** (HashiCorp Vault, 1Password, cloud secret managers) as
  variable sets, read at dispatch.
- **Agent pools and auto-scaling:** start agents on demand (containers or cloud instances) when
  jobs queue for a tag, and stop them when idle.
- **Retention:** how long to keep job logs, artifacts and activity, per project.
- **Postgres in CI**, so the Postgres path gets the same test coverage as SQLite.
