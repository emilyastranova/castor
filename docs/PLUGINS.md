# Plugins (plan)

Status: **phases A to E are built** (sandboxed plugins with actions, settings, storage, events,
declared network and panels; unrestricted plugins behind restricted mode, safe mode and page
scripts; the directory, update checks, publisher pages and packs of library content; the Nessus
import, Shodan enrichment, BloodHound bridge, DOCX report, Watch folder and Mail DNS checks), and
plugins can add asset types and relationship kinds (Source Code Review). Hooks and signed releases
are still to come. This is the design the plugin work follows;
change it here first when a decision changes, then build. The roadmap entry points here. Writing a
plugin: the wiki's Plugins page and `plugins/nessus-import/` in the
[castor-plugins](https://github.com/emilyastranova/castor-plugins) repository, which holds the
directory and the official plugins.

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
| **Asset types and relationship kinds** | Kinds of asset and of relationship Castor doesn't have, which then work like its own; more pairs for Castor's kinds | Source files and symbols, with "imports" and "calls"; a vulnerability that "affects" a file |

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

main: main.ts                  # the plugin's code (TypeScript or JavaScript), run by Deno; a release
                               # with a main.ts runs it anyway; none at all: a pack (below)
icon: icon.svg                 # optional: SVG, PNG or WebP in the plugin, square, ≤ 256 KB
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
ui:                            # its pages, shown in Castor in sandboxed frames (phase C)
  - id: paths
    slot: project.page         # project.page (a tab on the Assets page), card.tab, asset.panel
    title: Attack paths
    path: ui/paths.html        # inside the plugin; its CSS, scripts and images sit next to it
    icon: route                # a Material Symbols name
  - { id: asset-path, slot: asset.panel, title: Path to Domain Admins, path: ui/paths.html, asset_types: [user, host], height: 140 }
settings:
  - { name: info_findings, label: "Record informational findings", type: bool, default: false }
contents: [commands, tools]    # library content it ships in library/<kind>/*.yaml (packs, below)
asset_types:                   # kinds of asset it adds (see below)
  - { name: source_file, label: Source file, icon: code, identity: [repo, path], label_template: "{repo}/{path}",
      fields: [{ name: repo, label: Repository }, { name: path, label: Path }, { name: lines, label: Lines, type: number }] }
link_kinds:                    # kinds of relationship it adds, or more pairs for Castor's
  - { name: imports, forward: imports, reverse: imported by, pairs: [[source_file, source_file]], details: [{ name: line, label: Line }] }
  - { name: affects, pairs: [[vulnerability, source_file]] }
```

`network`, `events`, `actions`, `hooks`, `ui`, `settings`, `asset_types` and `link_kinds` are all optional. Castor rejects a
manifest with unknown fields, a permission it doesn't know, or an `id` that's taken by another
publisher.

### The icon

`icon:` is a file in the plugin (SVG, PNG or WebP, square, up to 256 KB), shown in the install
review (as a `data:` URL, since nothing is installed yet), on Settings → Plugins, on Your plugin
settings and while its actions run, on a white plate so brand colours read in every theme. An
SVG may only draw: Castor refuses one with scripts, event attributes, `foreignObject`, entities or
links other than `#…` and `data:image/…` (`plugin_package.check_icon`), and serves icons
(`GET /api/plugins/{id}/icon`) with a CSP that blocks anything active even if one is opened on its
own. The official plugins use their service's logo, with where it came from and its licence in
`ICON-CREDITS.md`.

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
| `runtime: unrestricted` | Files, programs, any network, environment, and a `page.script` in Castor's own page | **No** |

### Restricted and unrestricted

- **Sandboxed (the default).** The plugin's Deno process starts with only the permissions its
  manifest lists: `--allow-net=<network hosts>`, nothing else (no `--allow-read`, `--allow-write`,
  `--allow-run`, `--allow-env`, `--allow-ffi`). It reaches Castor only through the SDK, over its
  stdin/stdout, so it can't call the API with more than its token allows. Memory, CPU time and
  concurrency are capped per plugin.
- **Unrestricted (opt-in, phase D).** The same process with `--allow-all`, and remote and npm
  imports allowed (the module graph check is skipped): it can read the server's files (including
  Castor's configuration, database credentials and the vault key), run programs and reach anything.
  It may also ship a `page.script` (below), which runs inside Castor's own page with the viewer's
  session. It still reaches Castor's API only as its own account, with its approved permissions.

Making it extra clear:

- **Restricted mode** is on by default (Settings → Plugins, a switch that asks before turning it
  off; `PUT /api/plugin-mode`; the general settings endpoint refuses `plugins_restricted`).
  `CASTOR_PLUGINS_RESTRICTED=true` locks it on. While it's on, unrestricted plugins can't be
  installed, turned on or started, their panels' and scripts' files aren't served, and turning it
  on stops those that run. Turning it off or on is in the activity log (`plugin.mode`).
- **Installing** an unrestricted plugin shows a red section above everything else: what it can do,
  in plain words, and that Castor can't limit it. The admin types the plugin's id to confirm (`confirm`
  in `POST /api/plugins`). The approved grants record `runtime: [unrestricted]`, so a sandboxed
  plugin whose update becomes unrestricted is reviewed as something new.
- **Afterwards** it carries a red "Unrestricted" badge on the Plugins page and in its actions'
  tooltips, and each start writes an audit entry (`plugin.started`, the admin feed).
- **Safe mode:** `CASTOR_PLUGINS_SAFE_MODE=true` starts Castor with every plugin stopped and none
  startable (Settings → Plugins says so), to recover from a bad one.

### Page scripts (`page.script`, unrestricted only)

A `ui` entry with `slot: page.script` and a `.js` `path` is loaded as a module script into Castor's
own page for everyone who can see a project the plugin is enabled in (`GET /api/plugin-scripts`,
session only; none in safe or restricted mode). It runs as the person looking, so it can do
anything they can; the manifest refuses one on a sandboxed plugin. It gets `window.castorPage`:
`user`, `notify(text)`, `goto(path)` (Castor's own paths), `onNavigate(fn)` (after every
navigation), `onEvent(fn)` (live events), and calls the API with `fetch` and the session.

### Asset types and relationship kinds

A manifest's `asset_types` adds kinds of asset, and `link_kinds` kinds of relationship. They're
data, not code: Castor registers them when the plugin is installed (on or off, and in safe mode,
since they describe what's in projects) and they work everywhere Castor's own do: the Assets
page, adding one by hand, search, `where` filters, the Relationships graph, triggers, command
arguments, import and export, the MCP tools (`add_asset`, `link_assets`, `bulk_assets`,
`list_asset_types`, `list_link_kinds`). `GET /api/assets/types` and `GET /api/assets/link-kinds`
say which plugin added each (`plugin`).

- **A type:** `name` (lowercase, up to 16 characters; not one of Castor's or their aliases), `label`,
  `plural`, `icon` (Material Symbols), `description`, `fields` (`text`, `number`, `bool` or `list`;
  numbers and booleans are converted, lists sorted), `identity` (the fields that make two reports
  the same asset; all must be set) and `label_template` (`"{repo}/{path}"`; a part whose field is
  empty drops out; by default the identity, joined).
- **A kind:** `name` (up to 24 characters), `forward` and `reverse` (how it reads from each end),
  `pairs` (`[from type, to type]`, Castor's types or the plugin's; none means any two),
  `description` and `details` (fields usually recorded on it).
- **More pairs for Castor's kinds:** an entry with the name of one of Castor's kinds and only
  `pairs`, each with one of the plugin's own types (`affects: [[vulnerability, source_file]]`).
  Kinds that already connect anything (`related`) can't be extended.
- **Names are unique:** a plugin can't install while another installed plugin adds a type or kind
  of the same name (the preview lists the clash).
- **Removing the plugin** removes its types and kinds; assets of its types stay in their projects
  (as everything a plugin made does) and show as a plain type with no fields (`plugin: "removed"`)
  until a plugin adds it again. They can't be added or edited meanwhile. Relationships of a removed
  kind stay too, read by their name.

The install review lists the types and kinds a plugin adds. `assets.py` keeps them
(`register_plugin`, `CORE_TYPES` and `CORE_LINK_KINDS` plus the plugins'); the manifest checks
are in `plugin_manifest.py`.

## Runtime

**Deno**, one process per enabled plugin, started and supervised by the server
(`plugin_runtime.py`; the `deno` Python package, a server dependency, provides the binary). A
plugin that stops by itself is started again when it's next needed; three crashes in a minute stop
it until an admin starts it again (Settings → Plugins shows why). Why Deno: its permission
flags are exactly the sandbox/unrestricted split, it runs TypeScript without a build step,
`fetch` and Web APIs are built in, and it's MIT-licensed. It is one more binary in the server image.

Static imports are the one thing Deno loads without a permission check, so before every start
Castor lists the plugin's module graph (`deno info`) and refuses one that reaches outside the
plugin's folder and the SDK (or doesn't resolve). Remote and npm imports are off (`--no-remote`,
`--no-npm`): a plugin ships everything it needs in its `.zip`. The process gets a minimal
environment (no secrets), 512 MB of heap, and its own Deno cache under `<data_dir>/plugins/.cache`.

The existing V8 isolate (`js_sandbox.py`) stays for small inline code (a parser's `code:`); it has
no network or async, so it can't host whole plugins.

### Protocol

JSON-RPC 2.0 over the process's stdin/stdout, both ways:

- **Castor → plugin:** `activate {plugin, settings, castor_version}` (answered with the plugin's
  `actions`, `events` and `ui` handlers `[{name, read_only}]`),
  `action {run_id, action, user, project, task, asset, file?}`, `event {name, at, project, data}`,
  `ui {name, args, panel, user, project, task, asset, settings}` (a panel's `Castor.call`),
  `deactivate`; later `hook`.
- **Plugin → Castor:** `api {method, path, query, body}` or `api {method: POST, path, form, file:
  {name, content_type, base64}}` (a multipart upload) (made in-process with the plugin's token,
  exactly like an HTTP call; `/api/auth`, `/api/tokens`, `/api/plugins` and `/api/mcp` are refused
  whatever the token says), `storage.get|set|delete|list`, `file.read {run_id, offset, length}`
  (the action's file, in chunks), `log {level, message, run_id}`, `progress {run_id, done, total, text}`.

Anything else a plugin prints goes to its log, so stray output can't break the wire.

Hooks have a deadline (2 s by default, configurable per hook kind); a hook that misses it is
skipped and logged. Actions and events run in the background with progress shown on the button
and in the feed.

### The SDK

`@castor/plugin`, what plugin authors import. Castor ships it (`server/castor_server/plugin_sdk/mod.ts`)
and maps the name to it at run time; for working on a plugin, a `deno.json` import map points at
the same file (castor-plugins' `deno.json` files point at it on GitHub).

```ts
import { definePlugin } from "@castor/plugin";

definePlugin({
  activate(ctx) {
    ctx.actions.on("import", async (run) => {
      const found = parseNessus(await run.file!.text());           // the plugin's own code
      const r = await ctx.assets.bulk(run.project!.id, found);     // assets and relationships in one call
      run.progress({ text: "done" });
      return `Imported ${r.created} assets`;                       // shown to the person who ran it
    });
  },
});
```

`ctx`: `plugin`, `castorVersion`, `settings` (secret ones included), `api` (`request`, `get`,
`post`, `patch`, `put`, `delete`: any REST endpoint, throwing `CastorError` on 4xx/5xx; `upload(path,
{name, bytes, contentType}, fields?)` for endpoints that take a file, up to 20 MB), `assets.bulk`,
`storage` (`get`, `set`, `delete`, `list`, instance-wide or `{project}`), `log`, `actions.on`,
`events.on`, `ui.on` (handlers for its pages, below). With `network:`, plain `fetch` reaches those hosts (and only those). An
action gets `run`: `runId`, `user`, `project`, `task`, `asset`, `file` (`name`, `size`, `text()`,
`bytes()`, `stream()`), `progress()`, `log`. `console.log` goes to the plugin's log.
castor-plugins' `scripts/pack.py <folder>` packs a release (leaving out tests, samples and
`deno.json`; the same files always give the same .zip). Types
generated from the OpenAPI and a scaffolding CLI come later.

### UI slots (phase C)

A slot is an HTML page in the plugin's folder that Castor shows in a frame: `project.page` (a tab
on the project's Assets page, filling it), `card.tab` (a tab in the card dialog) and `asset.panel`
(a section of the asset dialog, for the `asset_types` listed or all). `run.panel`,
`project.home`, `settings.page` and `graph.overlay` come later.

**How it's kept apart.** The server serves the plugin's files at
`/api/plugin-ui/<plugin>/<signature>/<path>` (`api/plugin_panels.py`). The signature is an HMAC of
the plugin and its release under the server's secret key, so only people Castor lists the panel
for get the address, and a new release changes it. Only static kinds of file are served
(html, css, js, images, fonts, json, txt), never `main.ts`, the manifest or anything outside the
folder. Every response carries a Content Security Policy:

- `sandbox allow-scripts`: an opaque origin, so no cookies, nothing of Castor's page, no popups or
  top-level navigation. The frame also has `sandbox="allow-scripts"` (the CSP holds if a proxy
  drops it, the attribute if a proxy drops the header).
- `connect-src 'none'`: no `fetch`, XHR or WebSocket. Scripts, styles, images and fonts load only
  from the plugin's own files, named by host, since a sandboxed page's `'self'` matches nothing.
  The host is the one the browser asked, or the proxy's `X-Forwarded-Host`; the dev server's Vite
  proxy sends it with `xfwd`.
- `frame-ancestors 'self'`: only Castor can frame it.

Everything a page does goes through the Castor page around it by `postMessage`, which acts on the
person's behalf through one endpoint, `POST /api/plugins/{id}/panels/{ui}/call`.

**The page SDK** (`plugin_sdk/ui.js`) is put inline at the top of every page Castor serves, as `Castor`:

```js
const ctx = await Castor.ready();     // { plugin, panel, project, task, asset, user: { …, can_write } }
await Castor.api.get(`/api/projects/${ctx.project.id}/assets`, { type: "host" });  // REST, as the plugin
await Castor.call("paths", { sid });  // a handler in the plugin's code: ctx.ui.on("paths", …)
Castor.open({ asset: id });           // or { card: id }
Castor.notify("Saved");               // a snackbar in Castor
Castor.onTheme((t) => redraw());      // the theme changed (light/dark, colours, another theme)
```

The theme reaches the page as CSS variables on `:root`: the colour roles (`--primary`,
`--on-surface`, `--surface-container`, `--outline-variant`, `--error-container`…), `--font`,
`--mono` and `--radius-small|medium|large`; a base style already uses them. The frame's height
follows the page's content, or fills the tab for `project.page`. Opening another card or asset
loads the page again with its new context.

**Handlers.** `ctx.ui.on(name, handler, { readOnly })` in `main.ts` answers `Castor.call`. The
handler gets `call`: `name`, `args`, `panel`, `user` (with `can_write`), `project`, `task`, `asset`,
`settings` (shared plus the person's own), `log`. Register them in `activate`: Castor learns their
names when it returns.

**Who can do what.** Anyone who can see the project can open a panel. Through it, a viewer (or a
personal API token) can make `GET` API calls and reach handlers declared `readOnly: true`;
everything else needs someone who can change the project. API calls carry the plugin's token, so
the plugin's permissions and projects still apply, and the paths plugins can never call
(`/api/auth`, `/api/tokens`, `/api/plugins`, `/api/mcp`) stay refused. The card or asset in a
call must belong to the project, and the plugin must be enabled there.

## Data model

| Table | Holds |
| --- | --- |
| `plugins` | id, name, version, publisher, manifest (JSON), runtime, source (URL or upload), checksum, path, enabled, `all_projects`, `granted` (the approved permissions, network, events), `settings`, `secrets` (vault-sealed), the service account (`user_id`), status (stopped, starting, running, crashed, error) and why, installed_by |
| `plugin_projects` | which projects a plugin is enabled in (unless `all_projects`) |
| `plugin_storage` | plugin id, scope ("" or a project id), key, JSON value (≤ 1 MB) |
| `plugin_runs` | each time someone ran an action: on what, the file's name and size, status, progress, message |
| `plugin_logs` | what it logged or printed (the newest 2000 per plugin) |
| `users.plugin_id` | marks a plugin's service account: never signs in, isn't listed among people; `access.member_project_ids` makes it see the plugin's projects |

The audit trail is the activity log (`plugin.installed`, `plugin.updated`, `plugin.changed`,
`plugin.removed`; runs as `plugin.action.succeeded|failed` in their project's timeline, feed kind
Plugins).

Library items a plugin ships get `origin = "plugin:<id>"` and are removed with it. Assets,
relationships, cards and comments a plugin creates are the project's: they stay when the plugin
goes (their observations say "via Nessus import").

Plugin files live under `<data_dir>/plugins/<id>/<version>-<sha12>/`, unpacked from the release
(no paths outside it, no links, 100 MB zipped, 300 MB unpacked, 2000 files) and never modified; an
update unpacks beside the old version, which is removed once it's switched over.

## API

Admins only (session, or a token with the `admin` scope):

- `GET /api/plugins` (installed), `GET /api/plugins/{id}`
- `POST /api/plugins/preview` (an upload or an https URL → the manifest, its permissions in words,
  what this Castor can't run yet, and for an update what's new), then `POST /api/plugins` with the
  preview's token and `approve: true` (installs, or updates the plugin with that id)
- `PATCH /api/plugins/{id}` (on/off, projects, settings and secrets; it restarts),
  `POST /api/plugins/{id}/restart`, `DELETE /api/plugins/{id}`
- `GET /api/plugins/{id}/logs`, `GET /api/plugins/{id}/runs`
- `GET /api/plugin-mode` (restricted mode, whether it's locked, safe mode), `PUT /api/plugin-mode`
  `{restricted}`
- `POST /api/plugins/preview` with `directory: <id>`: the directory's newest release of it, checked
  against its SHA-256 (source `directory:<id>`)
- `GET /api/plugin-directory` (every plugin, its newest release, installed or not, an update or
  not), `GET /api/plugin-directory/plugins/{id}` (release notes, the manifest, the README, why it
  can't be installed here), `…/plugins/{id}/icon`, `GET /api/plugin-directory/publishers/{id}`,
  `POST /api/plugin-directory/check` (update check now)

Anyone who can change an enabled project: `GET /api/projects/{id}/plugin-actions`,
`POST /api/plugins/{id}/actions/{action}` (a form: `project_id`, optional `task_id`/`asset_id`, the
file), or `POST /api/plugin-actions/run` (the same as JSON, the file as base64); then
`GET /api/plugin-runs/{id}` and `GET /api/projects/{id}/plugin-runs`. `POST /api/projects/{id}/assets/bulk`
records many assets and relationships at once (what `ctx.assets.bulk` calls, open to everyone who
can change assets). MCP: `list_plugin_actions`, `run_plugin_action`, `get_plugin_run`,
`list_plugin_runs`, `bulk_assets`, and `list_plugins` for admins; installing, configuring and
removing plugins stay in the UI, where an admin approves them.

## The directory

Its own public repository, [castor-plugins](https://github.com/emilyastranova/castor-plugins),
like `obsidian-releases`, which also holds the official plugins. `directory.json`:

```json
{
  "version": 1,
  "publishers": [{ "id": "castor", "name": "Castor", "github": ["emilyastranova"], "url": "…", "description": "…" }],
  "plugins": [{ "id": "nessus-import", "name": "Nessus import", "publisher": "castor", "description": "…",
                "repo": "emilyastranova/castor-plugins", "path": "plugins/nessus-import",
                "tag_prefix": "nessus-import-v", "tags": ["import", "scanner"] }]
}
```

- **Publishers** name the GitHub accounts their plugins' repositories may be on: a plugin whose
  `repo` isn't on one of them isn't listed. A plugin's manifest `publisher:` must be its
  directory publisher, and ids are unique in the directory.
- **Releases** are GitHub releases of the plugin's repository, tagged `<tag_prefix>v<version>`
  (`tag_prefix` is empty for a repository with one plugin), carrying `<id>-<version>.zip`,
  `<id>-<version>.zip.sha256`, `castor-plugin.yaml`, and optionally `README.md` and the icon. Drafts
  and pre-releases are skipped. Castor takes the newest, downloads it, checks the SHA-256, and the
  preview refuses it unless its manifest has the directory's id and publisher and the tag's version.
  Then the usual review: nothing in the directory is vetted.
- **Adding a plugin** is a pull request to `directory.json`; its CI (`scripts/check_directory.py`,
  with Castor's own checks) reads the directory as Castor does, packs every plugin in the
  repository and has Castor read it, runs their tests in a real Castor, and checks other
  repositories' newest releases. Official plugins are released when a change to one with a new
  `version:` is merged (`scripts/release.py`).
- **In Castor:** Settings → Plugins → Browse (search and tags, Install or Update), a page per plugin
  (what it asks for, release notes, the README without its pictures, which would be fetched from
  wherever they point) and per publisher. `CASTOR_PLUGIN_DIRECTORY_URL` (empty turns browsing and
  checks off, for air-gapped instances; install from a file then), `CASTOR_GITHUB_API_URL`,
  `CASTOR_GITHUB_TOKEN` (GitHub allows 60 unauthenticated requests an hour; the directory and
  releases are cached for an hour).
- **Updates:** Castor checks daily (`CASTOR_PLUGIN_UPDATE_INTERVAL`; the Daily switch on Settings →
  Plugins, `app_settings.plugin_update_checks`) for installed plugins the directory lists with the
  same publisher, records the newer version (`plugins.latest_version`), shows it on the plugin and
  notifies admins once per version. Nothing updates by itself: Review the update opens the usual
  preview, with what it asks for that's new.
- A manifest's `castor:` (`">=0.1, <0.3"`: comparisons joined by commas) is checked at preview and
  install; the directory's plugin page says when this Castor is too old.

Signatures (Sigstore) come later, once there are publishers to verify.

## Library content (packs)

A plugin can ship library items: commands, tools, parsers, playbooks, themes and variable kinds, as
the same YAML the library uses, in `library/<kind>/*.yaml`, with the kinds listed in `contents:`.
One with no code (no `main`, no actions or events) is a **pack**: nothing runs, its status is
"Library only". `mail-dns-checks` in castor-plugins is the example.

- Each file is checked at preview like one written in the library (a pack's commands may use its
  own parsers), and the review lists them. What they name that's nowhere (a tool, a parser, a
  playbook's command) is a warning, as for built-ins.
- They join the library with `origin = "plugin:<id>"`, read-only like built-ins ("from <id>"; duplicate
  one to change it). An id the library already has from someone else (a person, Castor, another
  plugin) stops the install: a plugin never takes over what isn't its own.
- An update replaces them and retires what it no longer has; removing the plugin retires them all,
  as Castor retires a built-in whose file is gone: cards keep their command lines, and a tool, parser
  or variable kind something else still uses stays as an ordinary library item.

## Phases

Each phase ships with real plugins (the official examples, which double as tests), now in
castor-plugins' `plugins/`.

| Phase | Builds | Ships |
| --- | --- | --- |
| **A** (built) | Manifest and checks; install from a file or URL; Settings → Plugins with the permission review; service accounts; the Deno runner (sandboxed only) and protocol; the SDK's API, storage, settings and logs; actions (with files); `ctx.assets.bulk` | **Nessus import**: no network, writes hosts, services, vulnerabilities and `affects` relationships. Test data: `plugins/nessus-import/samples/` |
| **B** (built) | Event delivery; declared network; secret settings (done in A); rate limits | **Shodan enrichment**: new external hosts get ports, banners and CVEs |
| **C** (built) | UI slots (`project.page`, `card.tab`, `asset.panel`) in sandboxed frames; the page SDK and its handlers; theme variables | **BloodHound bridge**: users, computers and domains in, with `admin on`, sessions, membership and trusts; an attack-path tab and a panel on users and hosts |
| **D** (built) | Unrestricted runtime, the warning flow, restricted and safe mode, `page.script`; `ctx.api.upload` | **DOCX report** (sandboxed: it writes the document itself, no library needed) and **Watch folder** (unrestricted: it reads a folder on the server) |
| **E** (built) | The directory, update checks, publisher pages; library content in plugins (packs) | The directory repository and its checks; **Mail DNS checks**, a pack |

Hooks (asset mappers, transforms, trigger conditions) arrive with the phase that first needs one,
now D or later.

### Events (phase B)

`plugin_events.py`. A manifest's `events:` names what the plugin is told about:

| Event | When | `data` |
| --- | --- | --- |
| `asset.created` | a confirmed asset is new to the project (from results, by hand, an import, a confirmed suggestion, a plugin) | `asset`: id, type, key, label, fields, in_scope, scope_groups, confidence |
| `asset.linked` | a new relationship between confirmed assets | `link`: id, kind, forward, from, to, fields |
| `job.finished` | a card's job finished (succeeded, failed or lost) | `job`: id, status, exit_code, command_ref; `task` |
| `run.finished` | a playbook run finished | `run`: id, playbook_id, status |
| `task.moved` | a card moved to another column | `task`, `from`, `to` (id, name, role) |
| `comment.created` | someone commented on a card | `comment`: id, body, author; `task` |

Each comes with `name`, `at` and `project` (id, codename, name), only for projects the plugin is
enabled in. Events are queued on the database session and sent once it commits (a rollback drops
them). Each plugin has its own queue (500) and gets its events one at a time, in order; a delivery that fails is retried after
1, 5 and 15 s, then dropped and logged. An event the plugin caused (through its own account) isn't
sent to it, so it can't loop on itself. Queues are in memory: events during a restart are lost.

### Network (phase B)

`network:` lists exact hosts (`api.shodan.io`, `10.0.0.5:8443`, no schemes, paths or wildcards); the
process starts with `--allow-net=` those and nothing else (Deno also refuses a few ports outright).
The install review lists them with a reminder that the plugin can send there what it can read; an
update that adds a host must be approved again.

### Settings each person sets (phase B)

A setting can be one value for everyone or each person's own (an API key everyone brings). The
manifest says which it may be (`per_user: allowed`, the default; `required`; or `never`), and the
admin chooses for each `allowed` one (`plugins.per_user`; `PATCH /api/plugins/{id}` `per_user`).
People set theirs in **Your plugin settings** (`/plugins`; `GET|PUT /api/auth/me/plugins[/{id}]`,
session only, so neither API tokens nor plugins can read them); secret ones go to the vault
(`plugin_user_settings`). `ctx.settings` holds only the shared ones: an action's `run.settings`
and an event's `event.settings` are the shared ones plus the person's own. An action needs the
person's own required settings before it starts (422, with a link to set them). An event carries
the person who caused it: who did it, or for a card's run (assets found in its results, its job
finishing) who started it, and for a playbook run who started the run; changes nobody made by
hand, and those a plugin made, carry no person.

### Limits (phase B)

1200 calls to Castor's API per plugin per minute (`plugins.API_PER_MINUTE`); over it, a call fails
with a message to slow down. Events are capped by the queue above.

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

## The BloodHound bridge (phase C)

- **Action:** "Import SharpHound data" on a project's Assets page: a SharpHound .zip (BloodHound
  CE, JSON v5 and v6; read by the plugin itself, stored or deflated, no zip64 or passwords) or one
  of its `.json` files. Imports add to what's there; "Forget the BloodHound graph" drops it.
- **The graph:** users, computers, groups and domains by SID, with `MemberOf`, `AdminTo` (v5
  `LocalAdmins`, v6 `LocalGroups` by RID 544), `HasSession`, `CanRDP`, delegation, the ACL edges
  that give control (`GenericAll`, `WriteDacl`, `ForceChangePassword`, `AddMember`…), `DCSync`
  (GetChanges + GetChangesAll on a domain) and `DCFor` (a DC holds its domain). Kept packed in the
  plugin's project storage, in chunks.
- **Records:** users (`corp\alice`: SID, enabled, AdminCount, Kerberoastable, SPNs, AS-REP
  roastable, groups, description), computers as hosts (FQDN, OS, NetBIOS name, `dc` role) and
  domains, with `in_domain` (role `domain_admin` for members of Domain Admins), `joined_to`,
  `dc_of`, `session_on`, `admin_on` (a group's members, up to 200 per computer, noted "Through
  IT ADMINS…") and `trusts`. Groups and ACLs stay in the graph.
- **Panels:** an "Attack paths" tab (counts, Domain Admins, everyone with a path and how many
  steps, by kind, roastable or a search; each row opens to its path) and "Path to Domain Admins"
  on users and hosts. Shortest paths come from one breadth-first search back from the Domain
  Admins groups and the domains. Chips open the asset.
- **Test data:** `plugins/bloodhound/samples/corp-sharphound.zip`, a made-up domain written by
  `samples/make_sample.py`.

## DOCX report and Watch folder (phase D)

- **DOCX report** (sandboxed): "Write DOCX report" on a project or a card. A title page, a summary
  (the project's description, findings by severity), each finding (severity, what it `affects`,
  CVEs, tool, detail), the hosts with their services, the pinned notes. Its own writer (`docx.ts`:
  WordprocessingML and a zip with `CompressionStream`), so no document library: the plan expected
  one, but a `.docx` is a zip of XML. Attached to the card it ran on, or to a new "Report: <date>"
  card, through `ctx.api.upload`.
- **Watch folder** (unrestricted, the example of why one would be): watches a folder on the
  server (`/data/inbox`), a folder per project by code name; a file there becomes a card with the
  file attached, is handed to a plugin action that takes its extension (`POST /api/plugin-actions/run`,
  as its own account), and moves to `.imported/`.

## Source Code Review (asset types)

The example of asset types and relationship kinds (`plugins/source-code-review/`, sandboxed, no
network):

- **Types:** `repository` (name), `source_file` (repository + path: language, lines, roles,
  review state, packages it uses, SHA-256) and `code_symbol` (repository + file + name: a function,
  class, route or query, with its line and signature).
- **Kinds:** `in_repo`, `imports`, `defines`, `calls`, `flows_to` ("passes data to", with what
  passes), `tests`, `serves` (→ a URL), `deployed_to` (→ a host, URL or service), and Castor's
  `affects` from a vulnerability to a file, symbol or repository.
- **Import source code** (project or card, a `.zip`, `.tar.gz` or `.tar`): lists code and
  configuration files, works out imports for JavaScript/TypeScript (with Svelte and Vue), Python,
  Go, Java/Kotlin/Scala, C/C++, C#, PHP, Ruby and Rust (`code.ts`, regular expressions and a
  resolver that knows every path, Python packages, `go.mod` modules and C# namespaces), records
  outside packages per file, matches tests to what they test by name, and gives files roles
  (entry point, routes, auth, sql, test, config).
- **Code map** (`project.page`): folder trees per repository with filters, each file's or symbol's
  relationships, review states (not started, in progress, reviewed, flagged), and forms to add
  files, symbols and relationships. Its handlers (`map`, read-only; `add_files`, `add_symbol`,
  `link`, `unlink`, `review`) do the writing as the plugin.
- **Test data:** `samples/shop`, a small app in Python, TypeScript, Svelte and Go.

## Open questions

- Resource limits per plugin: fixed defaults, or set per plugin by the admin.
- Plugins on agents (new runtimes, not just tools) are out of scope for now.
