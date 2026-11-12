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
BloodHound bridge, DOCX report and Watch folder); next is the public directory.

## People and access

### SAML sign-on
Single sign-on works with OpenID Connect (Settings → Sign-on). SAML would add identity
providers that only speak it. A provider's groups should also be able to put people in
[groups](#groups), not only set their role.

### More for groups
Groups (Settings → Groups) can already be mentioned, share projects, take cards and follow a
sign-on provider's groups. Still to come:
- **Alerts and approvals:** a group receiving an alert, or approving a waiting trigger.

## Running work

### Triggers beyond assets
Today a trigger fires on new or changed [assets](https://github.com/emilyastranova/castor/wiki/Assets), and on new
relationships between them (a credential that works on a new host).
More events should be able to start a command or playbook:
- a job or run finishing (succeeded, failed, or with a parsed result that matches);
- a card moving to a column;
- a schedule (cron, or "every N hours" while a project is active);
- an incoming webhook;
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

## Data and results

### Every file and screenshot in a project
A project view of every attachment and job artifact, from every card and run, with filters by
card, run, step, agent, type and date. A gallery shows every image (screenshots and pasted
images) at a glance, with a download of a selection.

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
- **Comparing runs:** what changed in a run's results since the previous one (new hosts,
  closed ports, new findings).
- **Secrets from an external vault** (HashiCorp Vault, 1Password, cloud secret managers) as
  variable sets, read at dispatch.
- **Exporting artifacts:** a zip of a run's or a project's files.
- **Agent pools and auto-scaling:** start agents on demand (containers or cloud instances) when
  jobs queue for a tag, and stop them when idle.
- **Retention:** how long to keep job logs, artifacts and activity, per project.
- **Postgres in CI**, so the Postgres path gets the same test coverage as SQLite.
