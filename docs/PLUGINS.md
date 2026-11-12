# Plugins (plan)

Status: planned, not built. This is the design the plugin work follows; change it here first when a
decision changes, then build. The roadmap entry points here.

Plugins let anyone extend Castor: bring in another tool's results, enrich assets from an outside
service, sync cards with an issue tracker, add a panel to a card, ship a set of commands and
playbooks. The model is Obsidian's community plugins: anyone writes and publishes one, admins
browse and install them from inside Castor, and they run sandboxed unless the admin knowingly
allows more.

## Decisions

1. **Anyone can write and publish a plugin.** There's a public directory, like Obsidian's; nothing
   in it is vetted by Castor, so the install flow is what keeps an instance safe.
2. **Sandboxed by default, as much power as possible inside the sandbox.** A plugin that needs more
   (files, programs, unrestricted network, script in Castor's own page) declares it, and Castor
   makes that unmistakable at install and on the Plugins page. Unrestricted plugins are off until
   an admin turns restricted mode off.
3. **Only admins install, update, configure and remove plugins.** Admins also choose which projects
   a plugin is enabled in; anyone in those projects can use its actions and panels.
4. **The server never runs plugin code as Python**, sandboxed or not (the rule parser code already
   follows). Plugin code runs in its own process.
5. **A plugin can do no more than its manifest asks for, and the admin approved.** An update that
   asks for more is approved again before it runs.

## What a plugin can add

| Extension | What it is | Examples |
| --- | --- | --- |
| **Library content** | Commands, tools, parsers, playbooks, themes, variable kinds, shipped in the plugin and read-only (duplicate to customise), like built-ins | An "AD toolkit" pack; a hashcat tool and command |
| **Actions** | Buttons on a card, an asset, a run or a project, and entries in Ctrl+K, that call the plugin; an action can take a file | "Import .nessus file", "Create Jira issue", "Crack with hashcat" |
| **Event handlers** | The plugin is told when something happens (`job.finished`, `assets.changed`, `task.moved`, `run.finished`, `comment.created`…) | Enrich new hosts from Shodan; post findings to a SIEM |
| **Hooks** | Synchronous decisions Castor asks the plugin for: map parsed results to assets and relationships, a playbook transform op, a trigger condition, a search provider | A parser for a tool Castor doesn't know, with its asset mapping |
| **UI slots** | A panel in a sandboxed iframe: a card tab, an asset dialog panel, a project Home tile, a settings page, a Relationships graph overlay | A BloodHound attack-path tab; a Jira tab on a card |
| **Settings** | Fields the admin fills in at install (per instance, optionally per project); secret ones go to the vault | An API key, a base URL, a default severity |
| **Storage** | A key-value space per plugin, and per plugin and project | Last sync time, an id map between Castor and Jira |

Agents need nothing new: a plugin brings software into jobs the way the library does today,
as tools (container images or build recipes) with commands that use them.

## The manifest

A plugin is a folder (or a `.zip` of one) with `castor-plugin.yaml` at its root:

```yaml
id: nessus-import              # lowercase, dashes; unique in the directory
name: Nessus import
version: 1.0.0                 # semver; updates compare it
castor: ">=0.2"                # the Castor versions it works with
publisher: ridgeback           # the directory account that published it
description: Bring .nessus scan results into a project's assets, with vulnerabilities and what they affect.
homepage: https://github.com/example/castor-nessus-import
license: MIT

main: main.ts                  # the plugin's code (TypeScript or JavaScript), run by Deno
runtime: sandboxed             # sandboxed (default) or unrestricted (see below)

permissions:                   # Castor API scopes (api_tokens.SCOPES); the plugin's token gets these
  - projects:read
  - assets:write
network: []                    # hosts it may reach (sandboxed); [] is none
events: []                     # events it's told about
actions:
  - id: import
    label: Import .nessus file
    icon: upload_file
    on: [project, card]        # where the button shows
    file: { accept: [".nessus"], max_mb: 200 }
hooks: []
ui: []
settings:
  - { name: info_findings, label: "Record informational findings", type: bool, default: false }
contents: []                   # folders of library items it ships: commands/, parsers/, …
```

`network`, `events`, `actions`, `hooks`, `ui` and `settings` are all optional. Castor rejects a
manifest with unknown fields, a permission it doesn't know, or an `id` that's taken by another
publisher.

### Permissions

A plugin acts as its own **service account** (a bot user, shown by name in the feed, "via Nessus
import"), with an API token limited to `permissions` and to the projects it's enabled in. It never
acts with the person's own session. Its MCP-equivalent reach is exactly what such a token reaches
today, through the same `RULES`, so every check the API makes applies to plugins unchanged.

Beyond API scopes, a manifest can ask for:

| Field | Grants | Sandboxed |
| --- | --- | --- |
| `network: [api.shodan.io]` | Outbound HTTPS to exactly those hosts (no wildcards below a registered domain) | Yes |
| `events: [assets.changed]` | Delivery of those events for its enabled projects | Yes |
| `secrets: read` | Reading the values of variable sets it's given (not every set) | Yes, loudly marked |
| `ui: […]` | Its slots | Yes (iframes) |
| `runtime: unrestricted` | Files, programs, any network, environment, and `ui.script` in Castor's own page | **No** |

### Restricted and unrestricted

- **Sandboxed (the default).** The plugin's Deno process starts with only the permissions its
  manifest lists: `--allow-net=<network hosts>`, nothing else (no `--allow-read`, `--allow-write`,
  `--allow-run`, `--allow-env`, `--allow-ffi`). It reaches Castor only through the SDK, over its
  stdin/stdout, so it can't call the API with more than its token allows. Memory, CPU time and
  concurrency are capped per plugin.
- **Unrestricted (opt-in).** The same process with `--allow-all`: it can read the server's files
  (including Castor's configuration and database credentials), run programs and reach anything. It
  may also ship `ui.script`, which runs inside Castor's own page with the viewer's session.

Making it extra clear:

- **Restricted mode** is on by default (Settings → Plugins; `CASTOR_PLUGINS_RESTRICTED=1` locks it
  on). While it's on, unrestricted plugins can't be installed or enabled.
- **Installing** an unrestricted plugin shows a red section above everything else: what it can do,
  in plain words, and that Castor can't limit it. The admin types the plugin's id to confirm.
- **Afterwards** it carries a permanent "Unrestricted" badge on the Plugins page and in its
  actions' tooltips, and each start writes an audit entry.
- **Safe mode:** `CASTOR_PLUGINS_SAFE_MODE=1` starts Castor with every plugin stopped, to recover
  from a bad one.

## Runtime

**Deno**, one process per enabled plugin, started and supervised by the server (a crash restarts
it with backoff; three crashes in a minute stop it and tell the admins). Why Deno: its permission
flags are exactly the sandbox/unrestricted split, it runs TypeScript without a build step,
`fetch` and Web APIs are built in, and it's MIT-licensed. It is one more binary in the server image.

The existing V8 isolate (`js_sandbox.py`) stays for small inline code (a parser's `code:`); it has
no network or async, so it can't host whole plugins.

### Protocol

JSON-RPC 2.0 over the process's stdin/stdout, both ways:

- **Castor → plugin:** `activate {settings, castor_version}`, `action {id, context, file?}`,
  `event {name, data}`, `hook {kind, input}`, `deactivate`.
- **Plugin → Castor:** `api {method, path, body}` (checked against its token, exactly like an HTTP
  call), `storage.get|set|delete|list`, `log {level, message}`, `progress {action, done, total}`,
  `notify {…}`.

Hooks have a deadline (2 s by default, configurable per hook kind); a hook that misses it is
skipped and logged. Actions and events run in the background with progress shown on the button
and in the feed.

### The SDK

`@castor/plugin` (published to JSR/npm), what plugin authors import:

```ts
import { definePlugin } from "jsr:@castor/plugin";

export default definePlugin({
  async activate(ctx) {
    ctx.actions.on("import", async ({ project, file }) => {
      const xml = await file.text();
      const found = parseNessus(xml);            // the plugin's own code
      await ctx.assets.bulk(project.id, found);  // assets, observations and relationships in one call
      ctx.progress.done(`${found.hosts.length} hosts, ${found.vulnerabilities.length} findings`);
    });
  },
});
```

It wraps the REST API with types generated from Castor's OpenAPI (`ctx.api` for anything, plus
helpers: `ctx.assets`, `ctx.cards`, `ctx.comments`, `ctx.jobs`, `ctx.storage`, `ctx.settings`,
`ctx.log`). A `castor-plugin` CLI scaffolds a plugin, runs it against a local Castor with live
reload, checks the manifest and packs a release.

### UI slots

A slot is a URL inside the plugin's folder (`ui/card-tab.html`), served by Castor from a separate
origin (`plugins.<host>`, or a sandboxed iframe with `sandbox="allow-scripts"` and no
`allow-same-origin`), so it can't read Castor's cookies or DOM. A small `postMessage` SDK gives it:
the context (project, card, asset), `api` calls made with the plugin's token, the current theme's
colour roles as CSS variables (so panels match every theme), resize, and navigation requests
("open this card"). Slots: `card.tab`, `asset.panel`, `run.panel`, `project.home`,
`project.page` (its own nav entry), `settings.page`, `graph.overlay`.

## Data model

| Table | Holds |
| --- | --- |
| `plugins` | id, name, version, publisher, manifest (JSON), runtime, source (directory, URL, upload), checksum, signature, installed_by/at, enabled, approved permissions, the service account's user id, status (running, stopped, crashed, needs approval) |
| `plugin_projects` | which projects a plugin is enabled in (or all) |
| `plugin_settings` | settings values; secret ones as vault references |
| `plugin_storage` | plugin id, project id (nullable), key, JSON value; size-capped |
| `plugin_events` | the audit trail: installed, updated, permission changes, started, crashed, stopped |

Library items a plugin ships get `origin = "plugin:<id>"` and are removed with it. Assets,
relationships, cards and comments a plugin creates are the project's: they stay when the plugin
goes (their observations say "via Nessus import").

Plugin files live under `<data_dir>/plugins/<id>/<version>/`, unpacked from the release and never
modified; updates unpack beside the old version and switch over once the new one activates.

## API

Admins only, session or a token with a new `plugins:admin` scope:

- `GET /api/plugins` (installed), `GET /api/plugins/{id}`
- `POST /api/plugins/preview` (upload or URL → manifest, permissions, what it ships, warnings),
  `POST /api/plugins` (install the previewed one, with the admin's approval of its permissions)
- `PATCH /api/plugins/{id}` (enable/disable, projects, settings), `POST /api/plugins/{id}/update`,
  `DELETE /api/plugins/{id}`
- `GET /api/plugins/{id}/logs`, `GET /api/plugins/{id}/events`
- `GET /api/plugin-directory` (the directory, cached), `GET /api/plugin-directory/{id}`

Everyone in an enabled project: `GET /api/projects/{id}/plugin-actions`, and
`POST /api/plugins/{id}/actions/{action}` (with the context and an optional file). Each gets a
token rule and an MCP tool, like every endpoint.

## The directory

Like `obsidian-releases`: a public Git repository with one `plugins.json` listing each plugin's
id, name, author, description and repository. A plugin's repository publishes GitHub releases with
`castor-plugin.yaml` and a `.zip`; Castor reads the latest release, checks its SHA-256 and the
manifest, and shows its permissions before anything runs. Adding a plugin is a pull request to
the directory, checked automatically (manifest valid, id free, release present). Signatures
(Sigstore) come later, once there are publishers to verify.

Castor checks for updates daily (admins can turn it off for air-gapped instances, and install
from a file instead) and shows them on the Plugins page; nothing updates by itself.

## Phases

Each phase ships with real plugins in `plugins/` (the official examples, which double as tests).

| Phase | Builds | Ships |
| --- | --- | --- |
| **A** | Manifest and checks; install from a file or URL; Settings → Plugins with the permission review; service accounts; the Deno runner (sandboxed only) and protocol; the SDK's API, storage, settings and logs; actions (with files); `ctx.assets.bulk` | **Nessus import**: no network, writes hosts, services, vulnerabilities and `affects` relationships. Test data: `plugins/nessus-import/samples/` |
| **B** | Event delivery; declared network; secret settings; rate limits | **Shodan enrichment**: new external hosts get ports, banners and CVEs |
| **C** | UI slots and the iframe SDK; theme variables | **BloodHound bridge**: users, groups, computers, `admin on` and sessions in; an attack-path panel on an asset |
| **D** | Unrestricted runtime, the warning flow, restricted and safe mode, `ui.script` | **DOCX report**: a plugin that legitimately needs document libraries |
| **E** | The directory, update checks, publisher pages; library content in plugins (packs) | The directory repository and its checks |

Hooks (asset mappers, transforms, trigger conditions) arrive with the phase that first needs one,
probably B or C.

## The first plugin: Nessus import

- **Action:** "Import .nessus file" on a project's Assets page and on a card (the card then records
  the import in its timeline). Takes a `.nessus` (Nessus v2 XML) up to 200 MB.
- **Parsing:** in the plugin, streaming, with no entity expansion (the plugin's own XML reader, not
  one that resolves external entities). Untrusted input like any scan file.
- **Records:** each `ReportHost` as a host (address, FQDN, NetBIOS name, OS, MAC from
  `HostProperties`); each port with a service (`port`, `protocol`, `svc_name`); each finding with
  severity 1–4 (and 0 when the setting says) as a vulnerability (plugin id and name, severity,
  CVEs, CVSS, synopsis, solution) that **affects** the service, or the host for port 0; SMB signing
  and SMBv1 findings also set those host fields. Source `parser`, confidence 85, "via Nessus
  import".
- **Test data:** two MIT-licensed samples in `plugins/nessus-import/samples/` (42 synthetic hosts;
  one real host with full detail); their README says where they came from.

## Open questions

- A `plugins.<host>` origin for slots needs a second hostname in the compose stack; a sandboxed
  `srcdoc` iframe avoids that but complicates loading the plugin's assets. Decide in phase C.
- Resource limits per plugin: fixed defaults, or set per plugin by the admin.
- Plugins on agents (new runtimes, not just tools) are out of scope for now.
