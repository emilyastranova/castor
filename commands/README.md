# Command definitions

Reusable commands with typed arguments. In milestone 1 these are reference material and a
library the UI can offer as card templates; in milestone 2 they become the blocks of
playbooks, with `outputs` wired into the next block's `args`.

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
    regex: '^[0-9a-zA-Z./,:-]+$' # optional validation
command: nmap -sn {{ targets }}   # Jinja-style placeholders are filled from args
outputs:                          # milestone 2: parsed results for the next block
  - name: hosts
    type: list
    parser: nmap.hosts
```
