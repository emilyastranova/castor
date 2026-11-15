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

## People and access

### More for groups
Groups (Settings → Groups) can already be mentioned, share projects, take cards and follow a
sign-on provider's groups. Still to come:
- **Alerts and approvals:** a group receiving an alert, or approving a waiting trigger.

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
  show them, and the phases. Phase 1 is built: HTTP actions the server sends, with paging,
  batches, rate limits, retries and waiting. Still to come: HTTP on agents and AI recovery, MCP
  (including stdio servers hosted by agents), plugin actions, approvals.

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
