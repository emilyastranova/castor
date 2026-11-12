/**
 * The Active Directory graph from a SharpHound collection (BloodHound CE's JSON, versions 5 and 6):
 * users, computers, groups and domains by SID, and the edges that let one take over another.
 * Kept compact so it fits the plugin's storage, and searched backwards from the domains' most
 * powerful groups to find everyone's shortest path to Domain Admins.
 */

export type Kind = "user" | "computer" | "group" | "domain";
const KINDS: Kind[] = ["user", "computer", "group", "domain"];

/** What an edge means, read from → to. */
export const RELS = [
  "MemberOf", // from is a member of the group to (has its rights)
  "AdminTo", // local administrator on the computer
  "HasSession", // the computer has the user's session (take the computer, get the user)
  "GenericAll",
  "GenericWrite",
  "WriteDacl",
  "WriteOwner",
  "Owns",
  "ForceChangePassword",
  "AddMember",
  "AddSelf",
  "AllExtendedRights",
  "DCSync",
  "DCFor", // a domain controller: take it, have the domain
  "CanRDP",
  "ReadLAPSPassword",
  "AllowedToDelegate",
] as const;
export type Rel = (typeof RELS)[number];
const REL_INDEX = new Map<string, number>(RELS.map((r, i) => [r, i]));
// ACE rights that are edges as they are; GetChanges + GetChangesAll on a domain make DCSync.
const ACE_EDGES = new Set<string>(["GenericAll", "GenericWrite", "WriteDacl", "WriteOwner", "Owns", "ForceChangePassword", "AddMember",
  "AddSelf", "AllExtendedRights", "ReadLAPSPassword"]);

export const FLAG = { enabled: 1, admincount: 2, kerberoastable: 4, asreproastable: 8, dc: 16, highvalue: 32 } as const;

export interface Node {
  sid: string;
  kind: Kind;
  /** BloodHound's name: ALICE@CORP.LOCAL, WS01.CORP.LOCAL, DOMAIN ADMINS@CORP.LOCAL, CORP.LOCAL. */
  name: string;
  flags: number;
  /** A few properties for the assets: os, description, SPNs, the domain's NetBIOS name… */
  props: Record<string, unknown>;
}

export interface Graph {
  nodes: Map<string, Node>;
  /** Edges by "from|to|rel", so a second import doesn't double them. */
  edges: Set<string>;
}

export const emptyGraph = (): Graph => ({ nodes: new Map(), edges: new Set() });

// ---------- reading SharpHound ----------

type Obj = Record<string, unknown>;
const str = (v: unknown) => (typeof v === "string" ? v : "");
const arr = (v: unknown): Obj[] => (Array.isArray(v) ? (v.filter((x) => x && typeof x === "object") as Obj[]) : []);
const results = (v: unknown): Obj[] => (Array.isArray(v) ? arr(v) : arr((v as Obj | null)?.Results));
const kindOf = (t: unknown): Kind | null => {
  const k = str(t).toLowerCase();
  return (KINDS as string[]).includes(k) ? (k as Kind) : null;
};

/** What kind of SharpHound file this is (meta.type), or null if it isn't one. */
export function fileType(doc: unknown): Kind | "other" | null {
  const meta = (doc as Obj | null)?.meta as Obj | undefined;
  if (!meta || !Array.isArray((doc as Obj).data)) return null;
  const t = str(meta.type).toLowerCase().replace(/s$/, "");
  return (KINDS as string[]).includes(t) ? (t as Kind) : "other";
}

function edge(g: Graph, from: string, to: string, rel: Rel) {
  if (from && to && from !== to) g.edges.add(`${from}|${to}|${rel}`);
}

/** A node we only know by reference so far (a member, an ACE's principal): its name comes later. */
function ref(g: Graph, sid: string, kind: Kind | null) {
  if (!sid || g.nodes.has(sid) || !kind) return;
  g.nodes.set(sid, { sid, kind, name: sid, flags: 0, props: {} });
}

function put(g: Graph, sid: string, kind: Kind, name: string, flags: number, props: Obj) {
  const had = g.nodes.get(sid);
  g.nodes.set(sid, { sid, kind, name: name || had?.name || sid, flags: flags | (had?.flags ?? 0), props: { ...had?.props, ...props } });
}

/** Add one SharpHound file's objects to the graph. Returns how many it had. */
export function addFile(g: Graph, doc: unknown): number {
  const type = fileType(doc);
  if (!type || type === "other") return 0;
  const data = arr((doc as Obj).data);
  for (const o of data) {
    const sid = str(o.ObjectIdentifier);
    if (!sid) continue;
    const p = (o.Properties ?? {}) as Obj;
    const name = str(p.name);
    const flags = (p.enabled === true ? FLAG.enabled : 0) | (p.admincount === true ? FLAG.admincount : 0) |
      (p.hasspn === true ? FLAG.kerberoastable : 0) | (p.dontreqpreauth === true ? FLAG.asreproastable : 0) |
      (p.highvalue === true || p.isTierZero === true ? FLAG.highvalue : 0) | (p.isdc === true ? FLAG.dc : 0);
    const props: Obj = {};
    for (const k of ["domain", "domainsid", "samaccountname", "description", "operatingsystem", "functionallevel", "netbios",
      "serviceprincipalnames", "unconstraineddelegation"]) {
      if (p[k] !== undefined && p[k] !== null && p[k] !== "") props[k] = p[k];
    }
    put(g, sid, type, name, flags, props);

    // Who has rights over it.
    const changes = new Set<string>();
    const changesAll = new Set<string>();
    for (const ace of arr(o.Aces)) {
      const who = str(ace.PrincipalSID);
      const right = str(ace.RightName);
      ref(g, who, kindOf(ace.PrincipalType));
      if (ACE_EDGES.has(right)) edge(g, who, sid, right as Rel);
      else if (right === "GetChanges") changes.add(who);
      else if (right === "GetChangesAll") changesAll.add(who);
      else if (right === "DCSync") edge(g, who, sid, "DCSync");
    }
    if (type === "domain") for (const who of changes) if (changesAll.has(who)) edge(g, who, sid, "DCSync");

    if (type === "group") {
      for (const m of arr(o.Members)) {
        const msid = str(m.ObjectIdentifier ?? m.MemberId);
        ref(g, msid, kindOf(m.ObjectType ?? m.MemberType));
        edge(g, msid, sid, "MemberOf");
      }
    }
    if (type === "computer") {
      // v5: LocalAdmins / RemoteDesktopUsers; v6: LocalGroups (by the group's RID).
      for (const a of results(o.LocalAdmins)) {
        ref(g, str(a.ObjectIdentifier), kindOf(a.ObjectType));
        edge(g, str(a.ObjectIdentifier), sid, "AdminTo");
      }
      for (const a of results(o.RemoteDesktopUsers)) {
        ref(g, str(a.ObjectIdentifier), kindOf(a.ObjectType));
        edge(g, str(a.ObjectIdentifier), sid, "CanRDP");
      }
      for (const lg of arr(o.LocalGroups)) {
        const rid = str(lg.ObjectIdentifier).split("-").pop();
        const rel: Rel | null = rid === "544" ? "AdminTo" : rid === "555" ? "CanRDP" : null;
        if (!rel) continue;
        for (const a of results(lg.Results)) {
          ref(g, str(a.ObjectIdentifier), kindOf(a.ObjectType));
          edge(g, str(a.ObjectIdentifier), sid, rel);
        }
      }
      for (const key of ["Sessions", "PrivilegedSessions", "RegistrySessions"]) {
        for (const s of results(o[key])) {
          const user = str(s.UserSID);
          ref(g, user, "user");
          edge(g, str(s.ComputerSID) || sid, user, "HasSession");
        }
      }
      for (const d of arr(o.AllowedToDelegate)) {
        ref(g, str(d.ObjectIdentifier), kindOf(d.ObjectType));
        edge(g, sid, str(d.ObjectIdentifier), "AllowedToDelegate");
      }
      const domainSid = str(p.domainsid);
      if (p.isdc === true && domainSid) {
        ref(g, domainSid, "domain");
        edge(g, sid, domainSid, "DCFor");
      }
    }
    if (type === "user") {
      for (const d of arr(o.AllowedToDelegate)) {
        ref(g, str(d.ObjectIdentifier), kindOf(d.ObjectType));
        edge(g, sid, str(d.ObjectIdentifier), "AllowedToDelegate");
      }
    }
    if (type === "domain") {
      const trusts = arr(o.Trusts);
      if (trusts.length) props.trusts = trusts.map((t) => ({
        sid: str(t.TargetDomainSid), name: str(t.TargetDomainName), transitive: t.IsTransitive === true,
        direction: trustDirection(t.TrustDirection), type: str(t.TrustType),
      }));
      put(g, sid, type, name, flags, props);
    }
  }
  // The groups whose members run a domain: Domain Admins (-512), Enterprise Admins (-519),
  // Administrators (-544) and Domain Controllers (-516) are high value whatever the file says.
  for (const n of g.nodes.values()) if (n.kind === "group" && TIER_ZERO.test(n.sid)) n.flags |= FLAG.highvalue;
  return data.length;
}

const TIER_ZERO = /-(512|516|519|544)$/;
export const isDomainAdmins = (n: Node) => n.kind === "group" && /-512$/.test(n.sid);

function trustDirection(v: unknown): string {
  if (typeof v === "number") return ["disabled", "inbound", "outbound", "bidirectional"][v] ?? String(v);
  return str(v).toLowerCase();
}

// ---------- compact form (storage) ----------

interface Packed {
  v: 1;
  n: [string, number, string, number, Obj][];
  e: number[];
}

export function pack(g: Graph): string {
  const index = new Map<string, number>();
  const n: Packed["n"] = [];
  for (const node of g.nodes.values()) {
    index.set(node.sid, n.length);
    n.push([node.sid, KINDS.indexOf(node.kind), node.name, node.flags, node.props]);
  }
  const e: number[] = [];
  for (const key of g.edges) {
    const [from, to, rel] = key.split("|");
    const a = index.get(from), b = index.get(to), r = REL_INDEX.get(rel);
    if (a !== undefined && b !== undefined && r !== undefined) e.push(a, b, r);
  }
  return JSON.stringify({ v: 1, n, e } satisfies Packed);
}

export function unpack(text: string): Graph {
  const p = JSON.parse(text) as Packed;
  const g = emptyGraph();
  for (const [sid, k, name, flags, props] of p.n) g.nodes.set(sid, { sid, kind: KINDS[k], name, flags, props });
  for (let i = 0; i < p.e.length; i += 3) g.edges.add(`${p.n[p.e[i]][0]}|${p.n[p.e[i + 1]][0]}|${RELS[p.e[i + 2]]}`);
  return g;
}

// ---------- paths ----------

export interface Step {
  sid: string;
  name: string;
  kind: Kind;
  /** The edge from this node to the next one (none on the last). */
  rel?: Rel;
}

/** Everyone's shortest way to a Domain Admins group (or to a domain itself: DCSync, a DC). */
export class Paths {
  /** sid → [next sid, rel] towards the nearest target, and how many steps it is. */
  private next = new Map<string, [string, Rel] | null>();
  readonly dist = new Map<string, number>();

  constructor(readonly g: Graph) {
    const into = new Map<string, [string, Rel][]>();
    for (const key of g.edges) {
      const [from, to, rel] = key.split("|");
      // CanRDP isn't control; delegation needs more than the graph shows. They're context, not steps.
      if (rel === "CanRDP" || rel === "AllowedToDelegate") continue;
      (into.get(to) ?? into.set(to, []).get(to)!).push([from, rel as Rel]);
    }
    const queue: string[] = [];
    for (const n of g.nodes.values()) {
      if (isDomainAdmins(n) || n.kind === "domain") {
        this.dist.set(n.sid, 0);
        this.next.set(n.sid, null);
        queue.push(n.sid);
      }
    }
    for (let i = 0; i < queue.length; i++) {
      const at = queue[i];
      const d = this.dist.get(at)!;
      for (const [from, rel] of into.get(at) ?? []) {
        if (this.dist.has(from)) continue;
        this.dist.set(from, d + 1);
        this.next.set(from, [at, rel]);
        queue.push(from);
      }
    }
  }

  /** The shortest path from a node, or null if it has none. */
  from(sid: string): Step[] | null {
    if (!this.dist.has(sid)) return null;
    const steps: Step[] = [];
    let at: string | undefined = sid;
    while (at !== undefined && steps.length < 64) {
      const node = this.g.nodes.get(at);
      const hop = this.next.get(at);
      steps.push({ sid: at, name: node?.name ?? at, kind: node?.kind ?? "group", rel: hop?.[1] });
      at = hop?.[0];
    }
    return steps;
  }
}

/** Find a node by what Castor calls it: an asset's SID, "corp\\alice", "alice", a hostname. */
export function find(g: Graph, q: { sid?: string; username?: string; domain?: string; hostname?: string; label?: string }): Node | null {
  if (q.sid && g.nodes.has(q.sid)) return g.nodes.get(q.sid)!;
  const host = (q.hostname ?? "").toLowerCase();
  let user = (q.username ?? "").toLowerCase();
  let domain = (q.domain ?? "").toLowerCase();
  const label = (q.label ?? "").toLowerCase();
  if (!user && !host && label) {
    if (label.includes("\\")) [domain, user] = label.split("\\", 2);
    else user = label;
  }
  let fallback: Node | null = null;
  for (const n of g.nodes.values()) {
    const name = n.name.toLowerCase();
    if (n.kind === "computer" && (host || label)) {
      const h = host || label;
      if (h.includes(".") ? name === h : name.split(".")[0] === h) return n;
    }
    if (n.kind === "user" && user) {
      const [account, realm = ""] = name.split("@");
      if (account !== user) continue;
      if (!domain || realm === domain || realm.split(".")[0] === domain || String(n.props.netbios ?? "").toLowerCase() === domain) return n;
      fallback ??= n;
    }
  }
  return fallback;
}
