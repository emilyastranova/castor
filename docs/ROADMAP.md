# Roadmap

What's planned for Castor, grouped by area rather than by date. Nothing here is a promise or
in a fixed order. What's already built is in [the wiki](https://github.com/emilyastranova/castor/wiki)
and, for how it works, [ARCHITECTURE.md](ARCHITECTURE.md#milestones).

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
Today a trigger fires on new or changed [assets](https://github.com/emilyastranova/castor/wiki/Assets).
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

### Better asset detection
- More parsers mapped to assets: web technologies, TLS details, SMB shares, credentials and
  vulnerabilities from more tools.
- Merging the same thing seen by different tools (a host by IP and by name).
- Confidence and first-seen and last-seen times.
- Pulling hosts and URLs out of plain output when no parser applies, kept apart as "suggested"
  until someone confirms them.

### Relationships between findings
Record how things are connected, not only that they exist:
- these credentials worked on this host and service;
- this user is an admin on that host;
- this file was downloaded from this share on that host.

Relationships come from parsed results (a successful login, say) or are added by hand. They show
on each asset as links, can be filtered and searched, and later drive triggers ("when a credential
works on a new host…") and a graph view.

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

### More themes
Themes are built: a [theme spec](THEME_SPEC.md), AstraLabs as the first built-in, and custom
themes admins add in Settings → Themes. Still to come:
- **More looks:** high contrast, and a denser "compact" layout (which needs spacing and size
  tokens in the spec, not only colours, type and shape).
- **An instance default:** a theme admins choose for everyone who hasn't picked one, and for the
  sign-in page on a browser that has never signed in.
- **Moving themes between instances:** custom themes in library [import and export](https://github.com/emilyastranova/castor/wiki/Import-and-Export).
- **A contrast check:** the editor warning when text roles fall below 4.5:1 on their surfaces.

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
