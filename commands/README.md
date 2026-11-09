# Command definitions

Reusable commands with typed arguments. Files here are loaded into the command library as
read-only built-ins when the server starts; you can also create your own in the UI
(Commands page) using the same format. Cards pick a command and fill in its arguments.
Later in Phase 2 these become playbook blocks, with `outputs` wired into the next block's
`args`.

Every argument value is inserted **shell-quoted**, so a value is always a single word: a
target like `10.0.0.0/24; id` is passed to the tool literally, never run. List arguments are
inserted as one word per item, or as one joined word when `separator` is set (e.g. `","`).
Each job runs in its own work directory. Write files there as `{{work}}/name`: it is `/work` in
a container and the job's own directory in a host shell or on an SSH host (it renders as
`"${CASTOR_WORK}"`, which every runtime sets), so never write a literal `/work`. Files listed as
output `source`s and anything under `{{work}}/artifacts/` are uploaded back to the card when the
job finishes. `work` can't be an argument name.

```yaml
id: nmap-ping-sweep          # unique, kebab-case
name: Nmap ping sweep
schema_version: 1
description: Discover live hosts in a range
tags: [network, discovery]
tool: nmap                             # optional: a tool from tools/ or the Tools page
# image: docker.io/instrumentisto/nmap # or a container image directly (not both)
container_flags:                     # optional: extra flags passed to docker/podman run
  - --privileged
  - --network=bridge
args:
  - name: targets
    type: string                 # string | number | boolean | list
    description: CIDR range or host list
    required: true
    example: 192.168.0.0/24
    regex: '^[0-9a-zA-Z./,:-]+$' # optional validation (full match)
    # separator: ","               # list args only: join items into one word
command: nmap -sn -oX {{work}}/ping-sweep.xml {{ targets }}   # placeholders are filled from args
outputs:                          # files collected from the work directory after the job
  - name: hosts
    parser: nmap.hosts            # turns the file into a typed value (see below)
    type: list<host>              # optional; defaults to the parser's type
    source: "{{work}}/ping-sweep.xml"  # path or glob in the work directory (quote it: YAML); omit for stdout
    options:                      # optional parser options
      include_down: false
```

When the job finishes, each output with a `parser` is parsed and shown under **Results** on
the card. A parse failure is shown there too and doesn't fail the job. An output with only a
`source` is just collected as a file.

| Parser | Type | Reads | Options |
| --- | --- | --- | --- |
| `nmap.hosts` | `list<host>` `{ip, hostnames, status}` | nmap `-oX` XML | `include_down` (default true) |
| `nmap.services` | `list<service>` `{host, port, proto, state, name, product, version}` | nmap `-oX` XML | `include_closed` (default false: open ports only) |
| `ffuf.results` | `list<url>` `{url, status, length, words, lines}` | ffuf `-of json` | `status`: list of codes to keep |
| `lines` | `list<string>` | any text, one value per non-empty line | `match` (regex filter), `ignore_case`, `unique` |
| `regex` | `list<string>` | any text | `pattern` (required), `group` (number or name, default 0), `ignore_case`, `unique` (default true) |
| `text` | `string` | any text | |

The **Parsers** page in the UI lists these with their options and lets you try each one on
built-in sample data or your own pasted output, with the result updating as you type. It
also writes the `outputs:` entry for you to paste here. Options are checked when a command
is saved, so a typo or an invalid pattern is reported then rather than after a run.

## Variable sets (credentials, keys, listeners, ...)

A command that needs a credential, key file, listener address or similar declares a
**slot** of a variable *kind* instead of an argument. The card (or playbook step) then picks
a *set* of that kind from the Variables page.

```yaml
vars:
  - name: creds              # lowercase; used as {{ creds.<field> }}
    kind: credential         # credential, ssh-key, listener, api-token, or your own kind
    description: Account to log in with
    required: true           # default; false lets the card leave it empty
command: nxc smb {{ targets }} -u {{ creds.username }} -p {{ creds.password }}
```

- `{{ slot.field }}` renders as `"${CASTOR_VAR_SLOT_FIELD}"`, an environment variable the
  agent sets when the job runs. The command line itself never contains a value, so secrets
  never appear in it, in the card, or in the job history.
- File fields (e.g. `ssh-key.private_key`) are written into the job directory with mode 0600
  and the variable holds the file's path, so `-i {{ key.private_key }}` works.
- Any characters in a value are safe, including quotes, `$`, backticks, `$(…)` and `*`: the
  shell parses the command line before it expands `"${CASTOR_VAR_…}"`, and never re-parses
  the expanded value. Don't switch to single quotes, though: `'${CASTOR_VAR_…}'` isn't
  expanded at all, so the tool would receive the variable's name instead of its value.
- **Don't put a placeholder inside quotes**: it already brings its own double quotes, so
  `"user {{ creds.username }}"` leaves the value unquoted. Inside a quoted string, use the
  variable directly: `"user $CASTOR_VAR_CREDS_USERNAME"`.
- Optional fields can be used conditionally with shell expansion, e.g.
  `${CASTOR_VAR_CREDS_DOMAIN:+-d "$CASTOR_VAR_CREDS_DOMAIN"}`.
- Secret values that the tool prints are masked as `••••` in the card's output.
- Variable files are owned by the agent's user. An image that runs as a different non-root
  user can't read a 0600 file; run it as root or adjust with `container_flags`.

Every set dropdown (on the card, in New card, and in the playbook Run dialog) also offers
**Enter manually…**: type the values for just this card or run, optionally saving them to
Variables. Unsaved manual values are encrypted like any set but stay with that card or run
and are deleted with it.

Saving a command checks that every slot's kind exists and every `{{ slot.field }}` names a
field of that kind. `netexec-smb-auth` and `netexec-shares` are real examples.

## Dropdowns from a variable set (`choices_from`)

An argument can take its dropdown options from a variable set instead of a fixed `choices`
list, so editing the set updates every command that uses it:

```yaml
args:
  - name: protocol
    required: true
    default: smb
    choices_from: { set: NetExec protocols, field: options }
command: nxc {{ protocol }} {{ targets }}
```

The field must be a text or list field (the built-in **Choice list** kind has one `options`
list). The options are read whenever the command is shown, rendered or validated; a value
outside them is rejected like any `choices`. Saving the command checks that the set and
field exist. A "NetExec protocols" set is created once on first start; edit or delete it
like any set. `netexec-auth` uses it.

Kind fields can also have `choices`: a text field becomes a dropdown and a list field a set
of toggles when filling in a set.

## Optional flags (`flag`) and toggles (`show_if`)

`flag:` turns an argument into a command-line option that appears only when it has a value:

```yaml
args:
  - name: use_dns
    type: boolean
    description: Specify DNS server
  - name: dns_server
    flag: --dns-server   # --dns-server '10.0.0.53' when filled, nothing when empty
    show_if: use_dns     # only shown (and used) while use_dns is on
    required: true       # required only while shown
  - name: kerberos
    type: boolean
    flag: -k             # -k when on, nothing when off
  - name: port
    type: number
    flag: --port=        # one word: --port='445'
command: nxc ldap {{ targets }} {{ dns_server }} {{ kerberos }} {{ port }}
```

- On a text, number or list argument the flag precedes the quoted value; an empty value
  renders nothing (so an optional field needs no toggle of its own).
- On a boolean the flag renders when on and nothing when off (without `flag`, a boolean
  renders the word `true` or `false`).
- `show_if` names a boolean argument of the same command (one level, no chains). The card
  form hides the argument while the toggle is off, and the server ignores its value then,
  so a value left over from before can't sneak into the command.

## Target lists

Any text or list argument can take a saved **target list** instead of typed values: on a
card (or in New card / the playbook Run dialog), click the list button at the end of the
field to pick a list, or to import a file or pasted text with a live preview of how it
parses (separator, duplicates, comments, and what each entry looks like: IP, CIDR, range,
domain/host, URL). Lists live on the Variables page ("New target list"), so "Lab network",
"Lab exclusion list" or a single "Test computer" are managed in one place.

A picked list is **linked**: the card stores `{"list": "<id>"}` and the entries are filled in
each time the card's command is rendered or the card runs, so editing the list updates
every card that uses it. A list argument gets the entries as separate values (nmap gets
one target per argument); a text argument gets them comma-separated. Each entry is still
checked against the argument's `regex`. The nmap commands take an `exclude` list for
exclusion lists.
