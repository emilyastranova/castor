/**
 * BloodHound bridge: a SharpHound collection becomes a project's users, computers and domains,
 * with sessions, admin rights, membership and trusts as relationships; the whole graph (groups and
 * ACLs too) stays in the plugin's storage for its panels, which show everyone's shortest path to
 * Domain Admins. Sandboxed, no network.
 */
import { type Context, definePlugin } from "@castor/plugin";
import { addFile, emptyGraph, FLAG, type Graph, isDomainAdmins, pack, Paths, unpack, find } from "./graph.ts";
import { mapGraph, membersOf } from "./mapping.ts";
import { isZip, readZip } from "./zip.ts";

const CHUNK = 400_000; // characters per storage value (a value can be 1 MB of JSON)
const ASSET_BATCH = 500;
const LINK_BATCH = 2000;

// The graph per project, read from storage once and kept while the plugin runs.
const cache = new Map<string, { graph: Graph; paths: Paths; meta: Meta }>();

interface Meta {
  chunks: number;
  imported_at: string;
  files: string[];
  nodes: number;
  edges: number;
}

async function load(ctx: Context, project: string) {
  const hit = cache.get(project);
  if (hit) return hit;
  const meta = await ctx.storage.get<Meta>("graph/meta", { project });
  if (!meta) return null;
  let text = "";
  for (let i = 0; i < meta.chunks; i++) text += (await ctx.storage.get<string>(`graph/${i}`, { project })) ?? "";
  const graph = unpack(text);
  const entry = { graph, paths: new Paths(graph), meta };
  cache.set(project, entry);
  return entry;
}

async function save(ctx: Context, project: string, graph: Graph, files: string[]) {
  const text = pack(graph);
  const old = await ctx.storage.get<Meta>("graph/meta", { project });
  const chunks = Math.ceil(text.length / CHUNK);
  for (let i = 0; i < chunks; i++) await ctx.storage.set(`graph/${i}`, text.slice(i * CHUNK, (i + 1) * CHUNK), { project });
  for (let i = chunks; i < (old?.chunks ?? 0); i++) await ctx.storage.delete(`graph/${i}`, { project });
  const meta: Meta = { chunks, imported_at: new Date().toISOString(), files: [...new Set([...(old?.files ?? []), ...files])].slice(-50),
    nodes: graph.nodes.size, edges: graph.edges.size };
  await ctx.storage.set("graph/meta", { ...meta }, { project });
  cache.set(project, { graph, paths: new Paths(graph), meta });
}

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

definePlugin({
  activate(ctx) {
    ctx.actions.on("import", async (run) => {
      if (!run.file) throw new Error("Choose a SharpHound .zip (or one of its .json files)");
      if (!run.project) throw new Error("Run it on a project");
      const project = run.project.id;
      const bytes = await run.file.bytes();
      const docs: { name: string; read: () => Promise<Uint8Array> }[] = isZip(bytes)
        ? readZip(bytes).filter((e) => e.name.toLowerCase().endsWith(".json"))
        : [{ name: run.file.name, read: () => Promise.resolve(bytes) }];
      if (!docs.length) throw new Error("There are no .json files in that .zip");

      const graph = (await load(ctx, project))?.graph ?? emptyGraph();
      const read: string[] = [];
      let objects = 0;
      for (const [i, doc] of docs.entries()) {
        run.progress({ done: i, total: docs.length + 2, text: `Reading ${doc.name}` });
        let parsed: unknown;
        try {
          parsed = JSON.parse(new TextDecoder().decode(await doc.read()).replace(/^﻿/, ""));
        } catch {
          run.log.warn(`${doc.name}: not JSON, skipped`);
          continue;
        }
        const n = addFile(graph, parsed);
        if (n) {
          objects += n;
          read.push(doc.name);
        } else {
          run.log.info(`${doc.name}: not a SharpHound users/computers/groups/domains file, skipped`);
        }
      }
      if (!read.length) throw new Error("No SharpHound data in that file: it wants users, computers, groups or domains .json (BloodHound CE format)");
      run.progress({ done: docs.length, total: docs.length + 2, text: "Saving the graph" });
      await save(ctx, project, graph, read);

      const mapped = mapGraph(graph);
      const ids: Record<string, string> = {};
      let skipped = 0;
      for (let i = 0; i < mapped.assets.length; i += ASSET_BATCH) {
        const r = await ctx.assets.bulk(project, { assets: mapped.assets.slice(i, i + ASSET_BATCH), task_id: run.task?.id });
        Object.assign(ids, r.refs);
        skipped += r.skipped.length;
        for (const s of r.skipped.slice(0, 3)) run.log.warn(`skipped ${s.ref}: ${s.reason}`);
        run.progress({ done: docs.length + 1, total: docs.length + 2, text: `${Math.min(i + ASSET_BATCH, mapped.assets.length)} of ${mapped.assets.length} assets` });
      }
      const links = mapped.links.filter((l) => ids[l.from] && ids[l.to]).map((l) => ({ ...l, from: ids[l.from], to: ids[l.to] }));
      let linked = 0;
      for (let i = 0; i < links.length; i += LINK_BATCH) {
        const r = await ctx.assets.bulk(project, { assets: [], links: links.slice(i, i + LINK_BATCH), task_id: run.task?.id });
        linked += r.links;
      }
      const paths = cache.get(project)!.paths;
      const reach = [...graph.nodes.values()].filter((n) => n.kind === "user" && (n.flags & FLAG.enabled) && (paths.dist.get(n.sid) ?? 0) > 0).length;
      return `Read ${plural(objects, "object")} from ${plural(read.length, "file")}: ${plural(mapped.counts.users, "user")}, ` +
        `${plural(mapped.counts.hosts, "computer")}, ${plural(mapped.counts.domains, "domain")} and ${plural(linked, "relationship")}. ` +
        `${plural(reach, "enabled user")} ${reach === 1 ? "has" : "have"} a path to Domain Admins.` +
        (skipped ? ` ${skipped} couldn't be recorded (see the plugin's log).` : "");
    });

    ctx.actions.on("forget", async (run) => {
      if (!run.project) throw new Error("Run it on a project");
      const project = run.project.id;
      const keys = await ctx.storage.list({ prefix: "graph/", project });
      for (const k of keys) await ctx.storage.delete(k, { project });
      cache.delete(project);
      return keys.length ? "Forgot the imported graph (the assets stay)" : "There was no graph to forget";
    });

    // ---- the panels ----

    /** The project page: what's imported, who the domain admins are, the shortest paths. */
    ctx.ui.on("summary", async (call) => {
      const entry = await load(ctx, call.project.id);
      if (!entry) return { imported: false };
      const { graph, paths, meta } = entry;
      const nodes = [...graph.nodes.values()].filter((n) => n.name !== n.sid);
      const count = (k: string) => nodes.filter((n) => n.kind === k).length;
      const admins = nodes.filter(isDomainAdmins).map((g) => ({ group: g.name, members: [...membersNames(graph, g.sid)] }));
      const isAdmin = new Set(nodes.filter(isDomainAdmins).flatMap((g) => [...membersOf(graph, g.sid)]));
      const reach = nodes
        .filter((n) => (n.kind === "user" || n.kind === "computer") && (paths.dist.get(n.sid) ?? 0) > 0)
        .map((n) => ({ sid: n.sid, name: n.name, kind: n.kind, hops: paths.dist.get(n.sid)!, enabled: !!(n.flags & FLAG.enabled),
          kerberoastable: !!(n.flags & FLAG.kerberoastable), asreproastable: !!(n.flags & FLAG.asreproastable),
          domain_admin: isAdmin.has(n.sid) }))
        .sort((a, b) => a.hops - b.hops || a.name.localeCompare(b.name));
      return {
        imported: true, at: meta.imported_at, files: meta.files,
        counts: { users: count("user"), computers: count("computer"), groups: count("group"), domains: count("domain"), edges: graph.edges.size },
        domains: nodes.filter((n) => n.kind === "domain").map((d) => d.name),
        admins, reach: reach.slice(0, 500), more: Math.max(0, reach.length - 500),
      };
    }, { readOnly: true });

    /** One node's shortest path, by its SID or by the asset the panel is on. */
    ctx.ui.on<{ sid?: string }>("path", async (call) => {
      const entry = await load(ctx, call.project.id);
      if (!entry) return { imported: false };
      let node = call.args?.sid ? entry.graph.nodes.get(call.args.sid) ?? null : null;
      if (!node && call.asset) {
        const asset = await ctx.api.get<{ fields: Record<string, string> }>(`/api/assets/${call.asset.id}`);
        node = find(entry.graph, { sid: asset.fields.sid, username: asset.fields.username, domain: asset.fields.domain,
          hostname: asset.fields.hostname, label: call.asset.label });
      }
      if (!node) return { imported: true, found: false };
      return { imported: true, found: true, node: { sid: node.sid, name: node.name, kind: node.kind }, path: entry.paths.from(node.sid) };
    }, { readOnly: true });

    /** Castor's asset for a node, so the panel can open it. */
    ctx.ui.on<{ sid: string }>("asset", async (call) => {
      const entry = await load(ctx, call.project.id);
      const node = entry?.graph.nodes.get(call.args?.sid ?? "");
      if (!node) return null;
      const where = node.kind === "computer" ? `hostname:${node.name.toLowerCase()}` : `sid:${node.sid}`;
      const type = node.kind === "computer" ? "host" : node.kind;
      if (type === "group") return null;
      const r = await ctx.api.get<{ items: { id: string }[] }>(`/api/projects/${call.project.id}/assets`, { type, where, limit: 1 });
      return r.items[0]?.id ?? null;
    }, { readOnly: true });
  },
});

function membersNames(g: Graph, groupSid: string): string[] {
  const out: string[] = [];
  for (const key of g.edges) {
    const [from, to, rel] = key.split("|");
    if (rel === "MemberOf" && to === groupSid) out.push(g.nodes.get(from)?.name ?? from);
  }
  return out.sort();
}

