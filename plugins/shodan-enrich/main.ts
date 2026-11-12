/**
 * Shodan enrichment: when a confirmed host with a public address turns up in a project (and, by
 * default, is in its scope), look it up on Shodan and record what Shodan knows: open ports as
 * services, web servers as URLs, names as domains, and known CVEs as vulnerabilities affecting
 * the service they were seen on. Also an action to look up one host, or every host, on demand.
 * Sandboxed; its only network is api.shodan.io.
 */
import { CastorError, type Context, definePlugin } from "@castor/plugin";
import { isPublicIp, mapHost, type ShodanHost } from "./shodan.ts";

const DAY = 24 * 3600 * 1000;

interface Lookup {
  projectId: string;
  ip: string;
  taskId?: string;
  /** Even if it was looked up recently. */
  force?: boolean;
}

async function lookup(ctx: Context, l: Lookup): Promise<string> {
  const key = String(ctx.settings.api_key ?? "");
  if (!key) throw new CastorError("Set the Shodan API key in the plugin's settings");
  const seenKey = `seen:${l.ip}`;
  const recheck = Number(ctx.settings.recheck_days ?? 7) * DAY;
  const seen = await ctx.storage.get<number>(seenKey, { project: l.projectId });
  if (!l.force && seen && Date.now() - seen < recheck) return `${l.ip}: looked up recently`;
  const base = String(ctx.settings.api_url || "https://api.shodan.io").replace(/\/$/, "");
  let r = await fetch(`${base}/shodan/host/${encodeURIComponent(l.ip)}?key=${encodeURIComponent(key)}`);
  if (r.status === 429) { // Shodan's one-a-second limit: wait and try once more
    await new Promise((ok) => setTimeout(ok, 1500));
    r = await fetch(`${base}/shodan/host/${encodeURIComponent(l.ip)}?key=${encodeURIComponent(key)}`);
  }
  if (r.status === 404) {
    await ctx.storage.set(seenKey, Date.now(), { project: l.projectId });
    return `${l.ip}: Shodan has nothing on it`;
  }
  if (r.status === 401 || r.status === 403) throw new CastorError("Shodan refused the API key (check it in the plugin's settings)");
  if (!r.ok) throw new CastorError(`Shodan answered HTTP ${r.status} for ${l.ip}`);
  const host = (await r.json()) as ShodanHost;
  const { assets, links, found } = mapHost(host);
  const res = await ctx.assets.bulk(l.projectId, { assets, links, task_id: l.taskId });
  for (const s of res.skipped.slice(0, 5)) ctx.log.warn(`${l.ip}: skipped ${s.ref}: ${s.reason}`);
  await ctx.storage.set(seenKey, Date.now(), { project: l.projectId });
  return `${l.ip}: ${found.services} service${found.services === 1 ? "" : "s"}, ${found.vulns} known CVE${found.vulns === 1 ? "" : "s"}`;
}

definePlugin({
  activate(ctx) {
    const inScopeOnly = ctx.settings.only_in_scope !== false;

    ctx.events.on<{ asset: { type: string; fields: Record<string, unknown>; in_scope: boolean } }>("asset.created", async (event) => {
      const { asset } = event.data;
      if (asset.type !== "host" || !isPublicIp(asset.fields.ip)) return;
      if (inScopeOnly && !asset.in_scope) return;
      ctx.log.info(await lookup(ctx, { projectId: event.project.id, ip: asset.fields.ip }));
    });

    ctx.actions.on("lookup", async (run) => {
      const asset = await ctx.api.get<{ fields: Record<string, unknown> }>(`/api/assets/${run.asset!.id}`);
      if (!isPublicIp(asset.fields.ip)) return `${run.asset!.label} has no public address for Shodan to know`;
      return await lookup(ctx, { projectId: run.project!.id, ip: asset.fields.ip, force: true });
    });

    ctx.actions.on("lookup-all", async (run) => {
      const list = await ctx.api.get<{ items: { fields: Record<string, unknown>; in_scope: boolean }[] }>(
        `/api/projects/${run.project!.id}/assets`, { type: "host", limit: 1000 });
      const hosts = list.items.filter((h) => isPublicIp(h.fields.ip) && (!inScopeOnly || h.in_scope));
      if (!hosts.length) return inScopeOnly ? "No in-scope host has a public address" : "No host has a public address";
      let done = 0;
      let found = 0;
      for (const h of hosts) {
        const said = await lookup(ctx, { projectId: run.project!.id, ip: String(h.fields.ip), taskId: run.task?.id });
        if (!said.includes("nothing") && !said.includes("recently")) found++;
        run.progress({ done: ++done, total: hosts.length, text: said });
        await new Promise((ok) => setTimeout(ok, 1000)); // Shodan allows one lookup a second
      }
      return `Looked up ${hosts.length} host${hosts.length === 1 ? "" : "s"}; Shodan knew ${found}`;
    });
  },
});
