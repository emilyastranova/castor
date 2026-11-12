# Watch folder

Castor's example of an **unrestricted** plugin: it reads and moves files on the Castor server,
which a sandboxed plugin can't.

- It watches a folder on the server (`/data/inbox` by default) and makes a folder in it for each
  project it's enabled in, named by the project's code name.
- A file dropped in a project's folder becomes a card in that project ("Dropped: scan.nessus") with
  the file attached, once it has stopped growing. If another plugin has an action that takes that
  kind of file (Nessus import's `.nessus`, the BloodHound bridge's `.zip`), it runs on it.
- The file then moves to `.imported/` in the same folder (`.skipped/` if it's over 20 MB or
  something failed; the plugin's log says why).

Because it's unrestricted, Castor runs it with every permission (any file, program and network
the server can reach), and it can only be installed with restricted mode off (Settings → Plugins)
and its id typed to confirm. It still acts in Castor only as its own account, with
`projects:read` and `projects:write`.

In Docker, the folder is inside the server container; put it under the `/data` volume (the
default) or mount one, and copy files in with `docker compose cp scan.nessus server:/data/inbox/ACME-26-001/`.

```bash
python3 plugins/pack.py plugins/folder-watch     # → plugins/dist/folder-watch-1.0.0.zip
cd plugins/folder-watch && deno task test
```
