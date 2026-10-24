# Command definitions

Reusable commands with typed arguments. Files here are loaded into the command library as
read-only built-ins when the server starts; you can also create your own in the UI
(Commands page) using the same format. Cards pick a command and fill in its arguments.
Later in Phase 2 these become playbook blocks, with `outputs` wired into the next block's
`args`.

Every argument value is inserted **shell-quoted**, so a value is always a single word: a
target like `10.0.0.0/24; id` is passed to the tool literally, never run. List arguments are
inserted as one word per item, or as one joined word when `separator` is set (e.g. `","`).
Each job runs in its own `/work` directory; files listed as output `source`s and anything
under `/work/artifacts/` are uploaded back to the card when the job finishes.

```yaml
id: nmap-ping-sweep          # unique, kebab-case
name: Nmap ping sweep
schema_version: 1
description: Discover live hosts in a range
tags: [network, discovery]
image: docker.io/instrumentisto/nmap   # optional: run in this container image
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
command: nmap -sn {{ targets }}   # Jinja-style placeholders are filled from args
outputs:                          # files collected from /work after the job
  - name: hosts
    parser: nmap.hosts            # turns the file into a typed value (see below)
    type: list<host>              # optional; defaults to the parser's type
    source: /work/ping-sweep.xml  # path or glob under /work; omit to parse stdout
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
