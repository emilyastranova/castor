/**
 * Nessus import: a .nessus scan becomes a project's hosts, services and vulnerabilities, each
 * vulnerability linked to what it affects. Runs sandboxed with no network; everything goes in
 * through Castor's bulk assets API as this plugin.
 */
import { type BulkAsset, type BulkLink, definePlugin } from "@castor/plugin";
import { type Counts, mapHost, SEVERITY } from "./mapping.ts";
import { readHosts } from "./nessus.ts";

const BATCH = 25; // hosts per call

definePlugin({
  activate(ctx) {
    ctx.actions.on("import", async (run) => {
      if (!run.file) throw new Error("Choose a .nessus file");
      if (!run.project) throw new Error("Run it on a project");
      const info = ctx.settings.info_findings === true;
      const counts: Counts = { hosts: 0, services: 0, findings: 0, bySeverity: [0, 0, 0, 0, 0] };
      let read = 0;
      let skipped = 0;
      let batch: { assets: BulkAsset[]; links: BulkLink[] } = { assets: [], links: [] };
      let inBatch = 0;
      const flush = async () => {
        if (!batch.assets.length) return;
        const r = await ctx.assets.bulk(run.project!.id, { ...batch, task_id: run.task?.id });
        skipped += r.skipped.length;
        for (const s of r.skipped.slice(0, 5)) run.log.warn(`skipped ${s.ref}: ${s.reason}`);
        batch = { assets: [], links: [] };
        inBatch = 0;
        run.progress({ done: read, total: run.file!.size, text: `${counts.hosts} hosts, ${counts.findings} findings so far` });
      };
      for await (const host of readHosts(run.file.stream(), (n) => (read += n))) {
        const mapped = mapHost(host, info, counts);
        batch.assets.push(...mapped.assets);
        batch.links.push(...mapped.links);
        if (++inBatch >= BATCH) await flush();
      }
      await flush();
      if (!counts.hosts) return "The file has no hosts in it";
      const worst = [4, 3, 2, 1].filter((s) => counts.bySeverity[s]).map((s) => `${counts.bySeverity[s]} ${SEVERITY[s]}`);
      return `Imported ${counts.hosts} host${counts.hosts === 1 ? "" : "s"}, ${counts.services} service${counts.services === 1 ? "" : "s"} and ` +
        `${counts.findings} finding${counts.findings === 1 ? "" : "s"}${worst.length ? ` (${worst.join(", ")})` : ""}` +
        (skipped ? `; ${skipped} couldn't be recorded (see the plugin's log)` : "");
    });
  },
});
