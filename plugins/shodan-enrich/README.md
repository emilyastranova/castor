# Shodan enrichment

Castor's second plugin, and the example of events and network access.

- **When a confirmed host with a public address is new to a project** (in its scope, unless that's
  turned off), it looks the host up on Shodan and records what Shodan knows: open ports as
  services (with product, version, TLS and certificate), web servers as URLs, its names as
  domains that resolve to it, and known CVEs as vulnerabilities affecting the service they were
  seen on. A host looked up in the last week (a setting) isn't looked up again.
- **Look up on Shodan** on a host asset: now, whatever it did before.
- **Look up every host on Shodan** on the Assets page: one a second, Shodan's limit.

**Runs sandboxed**; its only network is `api.shodan.io`. It asks Castor for `projects:read`,
`assets:read` and `assets:write`, and to be told about `asset.created`. It needs a Shodan API key
(a secret setting, kept in Castor's vault).

```bash
python3 plugins/pack.py plugins/shodan-enrich       # → plugins/dist/shodan-enrich-1.0.0.zip
cd plugins/shodan-enrich && deno task test          # the mapping, on samples/host.json
```

`samples/host.json` is a made-up lookup in Shodan's format, on a documentation address.
