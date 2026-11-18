# Network routes

A **route** is the way to a network an agent can't reach on its own: a chain of hops, each an SSH
jump, a SOCKS5 or SOCKS4(a) proxy, or an HTTP proxy that allows `CONNECT`. A card, a playbook run
(or one of its steps), a project (its default) or an agent (everything it runs) picks one. Castor
brings the chain up on the agent, checks it, and sends the job's traffic through it the way that
tool takes best. Nothing about a route is written into a card's command, and its passwords and
keys stay in the vault.

## The pieces

| Where | What |
| --- | --- |
| `server/castor_server/routing.py` | The rules that need no database: hop validation (`HopIn`, `RouteIn`), a tool's `route:` block (`RouteCaps`), `plan` (the ways to try, best first, or why it can't), `raw_reason` (nmap SYN scans, ping sweeps, UDP), `unfake`. |
| `server/castor_server/network_routes.py` | Routes in the database: secrets into the vault (`store_hops`), what people see (`route_out`, no secrets), which route a job takes (`chosen_for`, `rows_for`), the payload agents get (`payload`), host keys trusted on first use (`learn_host_key`), tests (`test`), the scope check (`outside_scope`), routes from an SSH host. Live state per agent in `STATES`. |
| `server/castor_server/api/routes.py` | `/api/routes` (list, create, get, patch, delete, usage, test, accept a host key, from an SSH host, plan). |
| `agent/castor_agent/route_chain.py` | The chain itself, stdlib only: proxy handshakes, SSH hops (OpenSSH `-D`), the local proxy (`Endpoint`), and the route box's program (`Box`): transparent redirection, stand-in DNS, the kill switch. |
| `agent/castor_agent/routes.py` | `RouteManager` (start, share, test and stop routes on an agent), `apply` (set a job up for the way the server planned). |
| `tools/castor-route.yaml` | The route box's image: Alpine with Python, OpenSSH and iptables, built once per agent. |
| `web/src/routes/routes/`, `lib/components/routes/` | The Routes page (`RouteChain`, `HopEditor`), `RouteChoice` (cards, runs, projects). |

## Which route a job takes

`network_routes.chosen_for(task)`: the card's `tasks.route` (a route id, or `"none"` for direct),
else its playbook run's `playbook_runs.route`, else the project's `projects.settings.route`. A
playbook step's `route:` (a route's name or id) is put on its card when the run starts. An agent's
own `agents.route` (admins; not SSH hosts or plugin machines) goes **in front**: its hops first,
then the card's, so "this agent only gets out through the jumpbox" and "this card goes on into the
DMZ" compose.

None of these is a foreign key. A route that's deleted, not for the project, or has a hop whose host
key changed stops the job with the reason (`RouteError` in the dispatcher). A job never runs
unrouted because its route went away.

## Which agents can use it

A proxy is often only reachable from some agents (the one on the client's VPN, the one in that
segment). `routes.agents` says which: `{ids: [agent ids], tags: [agent tags]}`, an agent qualifies
when it's listed or has **any** of the tags; null (the default) is every agent.
`network_routes.allows(route, agent_id, tags)` is the rule (web `lib/routes.ts` `routeAllows`; keep
them the same), applied wherever an agent is chosen for a route:

* the dispatcher: a job only goes to an agent every route it takes allows (the card's, run's or
  project's route, and the agent's own); an HTTP action's sending agent too (`_to_server`);
* fan-outs: `{tags, all: true}` cards (`services.broadcast_targets`) and per-agent playbook runs
  start only on allowed agents;
* `POST /api/routes/{id}/test` picks an allowed agent, and refuses one that isn't (409);
* an agent's own `agents.route` must allow it (422).

A card pinned to an agent the route doesn't allow waits (queued) until that changes; the card
dialog says so under Agent, and its Agent list, like Run Playbook's, offers only allowed agents.
Ids are kept as given: an id with no agent here (forgotten, or the route came from another Castor)
matches nothing rather than opening the route up, and only new unknown ids are refused. Renaming or
merging agent tags (`services.rename_tags`) renames them in routes too.

## How a job is sent through it

A tool says what it can take in a `route:` block. A command can carry one too, overriding its tool's
key by key:

```yaml
route:
  proxy: "-proxy {url}"     # native: its own flag; {url} is the route's local end, {hostport} without the scheme
  scheme: http              # what the flag and env speak: socks5h (default), socks5, socks4a or http
  env: true                 # honours ALL_PROXY / HTTPS_PROXY / HTTP_PROXY
  hook: true                # dynamically linked: proxychains (LD_PRELOAD) works
  flags: "-sT -Pn"          # added wherever it's routed
  only: [native, hooked]    # the only ways that give true answers
  unroutable: "why"         # it can't go through a route at all
```

The command line says where the flags go with `{{ route }}`. It renders as `${CASTOR_ROUTE_ARGS}`,
unquoted, which the agent fills in (empty when the card isn't routed):
`nmap {{ route }} -sV -p {{ ports }} …`. A typed shell command with no tool is treated as
`env: true, hook: true`.

`routing.plan` lists the ways to try, best first, and the agent uses the first that works for the run:

| In a container | On the agent's host |
| --- | --- |
| native, env, hooked, transparent | native, hooked, env |

* **native**: `CASTOR_ROUTE_ARGS` gets the tool's flag with the local end's URL.
* **env**: `ALL_PROXY`, `HTTPS_PROXY`, `HTTP_PROXY` (and lower case) point at it.
* **hooked**: the command runs under proxychains-ng (strict chain, `proxy_dns` unless the route
  resolves names locally). In a container, the agent builds the tool's image with proxychains added
  once (`castor-tools/hooked-<hash>:1`: apk, apt or dnf). On a host, proxychains must be installed.
* **transparent**: the job's container is in the route box's network, where every TCP connection
  goes into the route whatever the tool does.

`CASTOR_ROUTE_URL` is always set (socks5h) for scripts that want it.

Some things can't go through a SOCKS or SSH chain: UDP, ICMP and raw packets. `raw_reason` refuses
nmap's SYN, UDP, ACK… scans, its ping sweeps and OS detection, and nmap without `-sT -Pn`; and ping,
fping, traceroute, masscan, hping3 and the like. A command can say `route: {unroutable: …}`.
Transparent mode makes every port look open to a port scanner (the box accepts each connection before
the far end answers), so nmap says `only: [native, hooked]`. DNS tools say `only: [native]`: in the
box their lookups would get stand-in addresses.

A strict route (`strict`, "Only through the route", the default) refuses host jobs. Nothing can hold
a process on the agent's host to the route, so a command must run in a container to use one.

## On the agent

`RouteManager.acquire(route, holder)` starts the route the first time a job (or test, or HTTP
request) needs it, keyed by `route.key`: a hash of the hops with their secrets and the options, so an
edit means a new route. It's shared by every job on it and stopped `IDLE_SECONDS` (90) after the last
one ends. A route that's broken at an SSH hop is kept and retried (`Chain._supervise`, backing off);
a job on a strict route that's broken fails with the hop and the reason.

**With a container runtime** the chain runs in a **route box**: `castor-route`, started as
`<runtime> run --rm -i --cap-add NET_ADMIN -v <route dir>:/castor-route:Z … python3
/castor-route/route_chain.py box config.json`. It talks JSON lines over stdin and stdout
(`configured`, `hop`, `host_key`, `ready`, `tested`, `names`, `error`). The config (with the secrets)
is deleted as soon as the box says `configured`. As root, the box:

1. binds `endpoint.sock` in the route directory (0666 inside the agent's own 0700 directory), the
   way host jobs, tests and HTTP requests reach it;
2. sets up its network namespace with iptables. In `nat OUTPUT`: UDP and TCP port 53 go to its DNS
   on 5353, and every other TCP connection not to 127/8 goes to its transparent port 12345, except
   the chain's own (uid 39999). With `strict`, `filter OUTPUT` drops everything but loopback,
   whatever was redirected (bound for 127/8 now), established traffic and uid 39999; IPv6 is
   dropped too;
3. drops to uid 39999 and runs the chain: SSH hops, a mixed SOCKS5/SOCKS4/HTTP proxy on
   127.0.0.1:1080, the transparent listener (`SO_ORIGINAL_DST`), and DNS that answers A lookups
   with stand-in addresses from 198.18.0.0/15 (`FakeDns`). Those map back to the name when the
   connection comes, so the far end resolves it. AAAA gets no answer.

Container jobs on the route run with `--network container:<box>` (a `--network` the card or command
asks for gives way). When one ends, the agent asks the box for its stand-ins (`names`) and sends them
in `job.finished` as `route_names`. The server keeps them in `jobs.route_info.names` and
`outputs.parse_job` puts the names back into parsed results (`routing.unfake`), so assets say
`intranet.lab`, not `198.18.0.1`.

**Without one** the chain runs in the agent's own process (`LocalRoute`), for host jobs only. Its
proxy is on 127.0.0.1 behind a random password. The SSH hops' `-D` ports are on loopback without one,
so on a shared machine, prefer an agent with a container runtime.

Host jobs on a box route use a password-protected proxy on 127.0.0.1 that passes connections to
`endpoint.sock`.

### SSH hops

An SSH hop is OpenSSH: `ssh -F /dev/null -N -D 127.0.0.1:<port>` with
`ExitOnForwardFailure`, keepalives, `IdentitiesOnly`, no agent and no forwarding. A hop after the
first is reached through the ones before it by `ProxyCommand=python3 route_chain.py pipe` to a
"stage" SOCKS server for that prefix of the chain (password-protected). Keys are written 0600 for the
process; passwords go through `SSH_ASKPASS` (`SSH_ASKPASS_REQUIRE=force`) from the process's
environment, never a command line. Host keys use `HostKeyAlias=castor-hop-<id>` and a known_hosts
of their own: pinned (`StrictHostKeyChecking=yes`) or, the first time, `accept-new`. The key seen is
reported (`route.host_key`) and kept. A mismatch stops the hop. The agent reads the key it shows now
with a connection that offers no credentials (`PreferredAuthentications=none`), and the server holds
it as `host_key_pending` until someone accepts it (`POST
/api/routes/{id}/hops/{hop}/accept-host-key`), notifying the route's maker and admins.

## The wire

Server → agent

| type | fields |
| --- | --- |
| `job.run` | `job.route`: `{id, ids[], name, label, key, hops[{id, kind, host, port, username, password, private_key, host_key}], dns, strict, plan[{method, scheme, args, flags}], box? (a tool way for castor-route)}` |
| `route.test` | `request_id, route (as above, no plan), target? [host, port]` |
| `http.request` | `route?` (an HTTP action through a route: the agent sends it only through the route's proxy) |

Agent → server

| type | fields |
| --- | --- |
| `route.status` | `route_id, key, state (starting\|up\|broken\|error\|down), mode (box\|local), jobs, broken?{index, error}, error?` |
| `route.hop` | `route_id, key, hop_id, index, state (up\|down\|error), error?, ms?` |
| `route.host_key` | `route_id, hop_id, index, key, changed` |
| `route.tested` | `request_id, hops[{index, ok (null: not reached), ms?, error?}], target?{ok, ms?, error?, hop?}, error?` |
| `job.route` | `job_id, method` (how the run went through it) |
| `job.finished` | `route_names?{address: name}` |

Capabilities: `routes: {box, ssh, proxychains}`. An agent without it gets no routed jobs.

Events: `route.updated` (a route as `route_out`), `route.deleted`, `route.live` (a route's state on
one agent), to the route's projects (every socket when it's for every project).

## The same rules as everything else

* Passwords and keys: vault-encrypted per hop, write-only in the API, masked in job output (route
  passwords join the job's redactions), in exports only under a passphrase (`transfer.route_out`).
* Every route change is in the activity log (`route.created`, `route.updated`, `route.deleted`,
  `route.tested`, `route.host_key_trusted`, `route.host_key_changed`, `route.host_key_accepted`).
* A card that goes through a route to targets outside the project's scope waits for approval
  (`services.enqueue_job`, `network_routes.outside_scope`), like a run that asks first.
* What a routed job finds is recorded as seen from the route: its `segment`, else its name
  (`assets.segment_of`), so Assets → Reachability compares routes like vantage points.
* HTTP actions with a route go through an agent that can bring it up (the runner adds `route` to
  each `http.request`). MCP actions don't take routes yet.
