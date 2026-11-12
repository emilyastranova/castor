# Nessus import

Castor's first plugin: a `.nessus` scan (Nessus or Tenable, v2 export) becomes a project's assets.

- **Hosts** with their address, name, NetBIOS name, OS and MAC; SMB signing off and SMBv1 on set
  those host fields.
- **Services**, each *on* its host.
- **Vulnerabilities** (low and above; informational too if you turn it on), each *affecting* the
  service or host it was found on, with severity, CVEs, CVSS, synopsis, solution and plugin output.

Everything goes in through Castor's bulk assets API as the plugin, so it merges with what other
tools found (no duplicates on a second import). It reads the file a host at a time, so big scans
are fine.

**Runs sandboxed**, with no network, files or programs; it asks Castor for `projects:read` and
`assets:write` only.

## Install

```bash
python3 plugins/pack.py plugins/nessus-import
```

then Settings → Plugins → **Install plugin** with `plugins/dist/nessus-import-1.0.0.zip`, and pick
its projects. **Import .nessus file** shows on a project's Assets page and in a card's menu.

## Work on it

```bash
cd plugins/nessus-import
deno task check     # type-check against the SDK (server/castor_server/plugin_sdk/mod.ts)
deno task test      # the reader and mapping, on the samples
```

`samples/` has two MIT-licensed `.nessus` files (see its README); they aren't packed.
