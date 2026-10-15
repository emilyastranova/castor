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
tags: [recon, network]
image: docker.io/instrumentisto/nmap   # optional: run in this container image
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
    type: list
    parser: nmap.hosts            # parsers arrive in Phase 2b
    source: /work/ping-sweep.xml
```
