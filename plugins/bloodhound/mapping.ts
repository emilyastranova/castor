/** The graph as Castor assets: users, computers (hosts) and domains, with the relationships
 *  Castor knows (joined_to, dc_of, in_domain, session_on, admin_on, trusts). Groups stay in the
 *  plugin's graph: they're how paths go, not things in the inventory. */
import type { BulkAsset, BulkLink } from "@castor/plugin";
import { FLAG, type Graph, isDomainAdmins, type Node } from "./graph.ts";

/** Expanding a group's admin rights to its members stops here, per computer. */
export const ADMIN_FANOUT = 200;

export interface Mapped {
  assets: BulkAsset[];
  /** Links between `ref`s of the assets above. */
  links: BulkLink[];
  counts: { users: number; hosts: number; domains: number };
}

const lower = (s: unknown) => String(s ?? "").toLowerCase();

export function netbiosOf(g: Graph, domainName: string): string {
  for (const n of g.nodes.values()) {
    if (n.kind === "domain" && lower(n.name) === lower(domainName) && n.props.netbios) return lower(n.props.netbios);
  }
  return lower(domainName).split(".")[0];
}

export const refOf = (n: Node) => `${n.kind}:${n.sid}`;

/** Users who are in a group, directly or through other groups. */
export function membersOf(g: Graph, groupSid: string, limit = Infinity): Set<string> {
  const into = new Map<string, string[]>();
  for (const key of g.edges) {
    const [from, to, rel] = key.split("|");
    if (rel === "MemberOf") (into.get(to) ?? into.set(to, []).get(to)!).push(from);
  }
  const users = new Set<string>();
  const seen = new Set([groupSid]);
  const queue = [groupSid];
  for (let i = 0; i < queue.length && users.size < limit; i++) {
    for (const m of into.get(queue[i]) ?? []) {
      if (seen.has(m)) continue;
      seen.add(m);
      const node = g.nodes.get(m);
      if (node?.kind === "user") users.add(m);
      else if (node?.kind === "group") queue.push(m);
    }
  }
  return users;
}

export function mapGraph(g: Graph): Mapped {
  const assets: BulkAsset[] = [];
  const links: BulkLink[] = [];
  const counts = { users: 0, hosts: 0, domains: 0 };
  const known = (n: Node | undefined): n is Node => !!n && n.name !== n.sid; // seen in a file, not just referenced
  const domainBySid = new Map<string, Node>();
  for (const n of g.nodes.values()) if (n.kind === "domain" && known(n)) domainBySid.set(n.sid, n);
  const domainNamed = (name: unknown) => [...domainBySid.values()].find((d) => lower(d.name) === lower(name));

  const admins = new Set<string>();
  for (const n of g.nodes.values()) if (isDomainAdmins(n)) for (const u of membersOf(g, n.sid)) admins.add(u);

  // Direct group names, for the user's "groups" field.
  const groupsOf = new Map<string, string[]>();
  for (const key of g.edges) {
    const [from, to, rel] = key.split("|");
    const group = g.nodes.get(to);
    if (rel === "MemberOf" && known(group)) (groupsOf.get(from) ?? groupsOf.set(from, []).get(from)!).push(group.name.split("@")[0].toLowerCase());
  }

  for (const n of g.nodes.values()) {
    if (!known(n)) continue;
    if (n.kind === "domain") {
      counts.domains++;
      assets.push({ ref: refOf(n), type: "domain", fields: {
        name: lower(n.name), sid: n.sid, netbios_name: n.props.netbios ? String(n.props.netbios).toUpperCase() : undefined,
        functional_level: n.props.functionallevel,
      } });
    } else if (n.kind === "user") {
      const [account, realm = String(n.props.domain ?? "")] = n.name.split("@");
      const username = lower(n.props.samaccountname || account);
      if (!username || username.endsWith("$")) continue;
      counts.users++;
      const spns = Array.isArray(n.props.serviceprincipalnames) ? n.props.serviceprincipalnames as string[] : [];
      assets.push({ ref: refOf(n), type: "user", fields: {
        username, domain: realm ? netbiosOf(g, realm) : undefined, sid: n.sid,
        enabled: !!(n.flags & FLAG.enabled), admin_count: !!(n.flags & FLAG.admincount),
        kerberoastable: !!(n.flags & FLAG.kerberoastable), asreproastable: !!(n.flags & FLAG.asreproastable),
        spns: spns.length ? spns : undefined, groups: groupsOf.get(n.sid), ad_description: n.props.description || undefined,
      } });
      const domain = domainNamed(realm);
      if (domain) links.push({ from: refOf(n), to: refOf(domain), kind: "in_domain", fields: admins.has(n.sid) ? { role: "domain_admin" } : undefined });
    } else if (n.kind === "computer") {
      counts.hosts++;
      const isDc = !!(n.flags & FLAG.dc);
      assets.push({ ref: refOf(n), type: "host", fields: {
        hostname: lower(n.name), domain: n.props.domain ? lower(n.props.domain) : undefined, os: n.props.operatingsystem || undefined,
        netbios_name: n.props.samaccountname ? String(n.props.samaccountname).replace(/\$$/, "").toUpperCase() : undefined,
        roles: isDc ? ["dc"] : undefined,
      } });
      const domain = domainNamed(n.props.domain) ?? domainBySid.get(String(n.props.domainsid ?? ""));
      if (domain) links.push({ from: refOf(n), to: refOf(domain), kind: isDc ? "dc_of" : "joined_to" });
    }
  }

  const userAsset = (sid: string) => {
    const n = g.nodes.get(sid);
    return known(n) && n.kind === "user" && !lower(n.props.samaccountname || n.name).split("@")[0].endsWith("$") ? n : null;
  };
  const adminSeen = new Set<string>();
  for (const key of g.edges) {
    const [from, to, rel] = key.split("|");
    const a = g.nodes.get(from);
    const b = g.nodes.get(to);
    if (rel === "HasSession" && known(a) && a.kind === "computer" && userAsset(to)) {
      links.push({ from: refOf(b!), to: refOf(a), kind: "session_on" });
    } else if (rel === "AdminTo" && known(b) && b.kind === "computer") {
      const who = userAsset(from) ? [from] : a?.kind === "group" ? [...membersOf(g, from, ADMIN_FANOUT)] : [];
      for (const u of who) {
        const user = userAsset(u);
        if (!user || adminSeen.has(`${u}|${to}`)) continue;
        adminSeen.add(`${u}|${to}`);
        links.push({ from: refOf(user), to: refOf(b), kind: "admin_on", note: u === from ? undefined : `Through ${a!.name}` });
      }
    }
  }
  for (const d of domainBySid.values()) {
    for (const t of (d.props.trusts as { sid: string; name: string; transitive: boolean; direction: string; type: string }[] | undefined) ?? []) {
      const other = domainBySid.get(t.sid) ?? domainNamed(t.name);
      if (!other) continue;
      // An outbound or two-way trust: this domain trusts the other (its users can sign in here).
      if (t.direction === "outbound" || t.direction === "bidirectional") {
        links.push({ from: refOf(d), to: refOf(other), kind: "trusts", fields: { type: lower(t.type) || undefined, direction: t.direction, transitive: t.transitive } });
      }
    }
  }
  return { assets: assets.map(clean), links: links.map((l) => (l.fields ? { ...l, fields: strip(l.fields) } : l)), counts };
}

function strip(o: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined));
}

const clean = (a: BulkAsset): BulkAsset => ({ ...a, fields: strip(a.fields) });
