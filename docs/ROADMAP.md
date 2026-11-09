# Roadmap

What's planned for Castor, grouped by area rather than by date. Nothing here is a promise or
in a fixed order. What's already built is in [the wiki](https://github.com/emilyastranova/castor/wiki)
and, for how it works, [ARCHITECTURE.md](ARCHITECTURE.md#milestones).

## People and access

### Single sign-on
Sign in with an identity provider instead of a Castor password: OpenID Connect first (Okta,
Entra ID, Google, Keycloak, Authentik…), then SAML. Accounts are created on first sign-in, and a
provider's groups or claims can set a person's role and [group](#groups). Local accounts stay
for the first admin and for recovery (`castor-admin`), and an instance can turn password
sign-in off.

### Groups
Put people in groups (a team, a client's testers, on-call):
- **Mentions:** `@group` mentions everyone in it, and cards can be assigned to a group.
- **Access:** project membership by group, so adding someone to a group gives them its projects.
- **Alerts and approvals:** a group can receive an alert or approve a waiting trigger.

Groups can also be kept in step with the SSO provider's groups.

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

### AI summaries of runs
When a job or playbook run finishes, Castor's AI writes a short summary on the card: what was
found, what failed and what to look at next. It's based on the parsed results and the end of
the output, and is just as careful with secrets as the rest of the AI features. It's optional
per project, with a button to write one again.

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
- **Export:** to Markdown, HTML and PDF.

### Renaming library tags
Card tags (per project) and agent tags can be renamed and merged; library tags (commands and
playbooks) can't yet, because they live in each definition's YAML and the built-in ones are
re-read from files at startup.

### Themes
Beyond the colour seed each person can pick, a few complete looks: high contrast, a denser
"compact" layout, and themes shipped as CSS files an instance can add to. They'd be built on
the same Material 3 colour roles, so every page follows.

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
