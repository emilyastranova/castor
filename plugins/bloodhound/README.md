# BloodHound bridge

Castor's third plugin, and the example of panels: pages of a plugin's own, shown in Castor.

- **Import SharpHound data** on a project's Assets page: a SharpHound collection (BloodHound CE,
  the `.zip` or one of its `.json` files). Users, computers and domains become assets; sessions,
  admin rights (directly or through a group), domain membership, domain controllers and trusts
  become relationships. Groups and ACLs stay in the plugin's graph, kept with the project.
  Importing again adds to what's there.
- **Attack paths**, a tab on the Assets page: the Domain Admins, and everyone with a way to them,
  shortest first, each row opening to its path (membership, admin rights, sessions, ACL edges such
  as `ForceChangePassword` or `GenericAll`, DCSync).
- **Path to Domain Admins**, a panel on users and hosts.
- **Forget the BloodHound graph** drops the graph (the assets and relationships stay).

**Runs sandboxed**, with no network. It asks Castor for `projects:read` and `assets:write`. Its
pages run in sandboxed frames with no network either; they ask the plugin's own handlers for the
paths.

It reads users, computers, groups and domains files (SharpHound's JSON versions 5 and 6). Zips
must be unencrypted and under 4 GB (no zip64).

```bash
python3 plugins/pack.py plugins/bloodhound       # → plugins/dist/bloodhound-1.0.0.zip
cd plugins/bloodhound && deno task test          # the reader, paths and mapping, on the sample
python3 samples/make_sample.py                   # rewrite the sample
```

`samples/corp-sharphound.zip` is a made-up domain (CORP.LOCAL, seven users, three computers, a
trust), written by `samples/make_sample.py`.
