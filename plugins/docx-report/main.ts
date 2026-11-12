/**
 * DOCX report: a project's findings, hosts and notes as a Word document, attached to a card.
 * Sandboxed, no network; it writes the .docx itself (docx.ts), no document library needed.
 */
import { definePlugin } from "@castor/plugin";
import { docx, DOCX_TYPE } from "./docx.ts";
import { buildReport, type Finding, type Host, severityOf, type Severity } from "./report.ts";

interface Asset { id: string; type: string; label: string; fields: Record<string, unknown> }
interface Link { kind: string; from: { id: string; type: string; label: string }; to: { id: string; type: string; label: string } }

definePlugin({
  activate(ctx) {
    ctx.actions.on("write", async (run) => {
      if (!run.project) throw new Error("Run it on a project");
      const pid = run.project.id;
      const s = run.settings;
      run.progress({ text: "Reading the project" });

      const all = async <T>(path: string, query: Record<string, unknown>, pageSize: number): Promise<T[]> => {
        const out: T[] = [];
        for (let offset = 0; ; offset += pageSize) {
          const page = await ctx.api.get<{ items: T[]; total: number }>(path, { ...query, limit: pageSize, offset });
          out.push(...page.items);
          if (!page.items.length || out.length >= page.total) return out;
        }
      };
      const project = await ctx.api.get<{ name: string; codename: string; description: string; start_date: string | null; end_date: string | null }>(`/api/projects/${pid}`);
      const vulns = await all<Asset>(`/api/projects/${pid}/assets`, { type: "vulnerability" }, 1000);
      const links = await all<Link>(`/api/projects/${pid}/asset-links`, { kind: "affects,on" }, 5000);
      const affects = new Map<string, string[]>();
      const servicesOn = new Map<string, string[]>();
      for (const l of links) {
        if (l.kind === "affects") (affects.get(l.from.id) ?? affects.set(l.from.id, []).get(l.from.id)!).push(l.to.label);
        if (l.kind === "on" && l.from.type === "service" && l.to.type === "host") {
          const name = l.from.label.startsWith(l.to.label) ? l.from.label.slice(l.to.label.length).replace(/^[:\s]+/, "") : l.from.label;
          (servicesOn.get(l.to.id) ?? servicesOn.set(l.to.id, []).get(l.to.id)!).push(name);
        }
      }
      const findings: Finding[] = vulns.map((v) => ({
        id: String(v.fields.id ?? v.label), title: String(v.fields.title || v.fields.id || v.label), severity: severityOf(v.fields.severity),
        cves: Array.isArray(v.fields.cves) ? v.fields.cves.map(String) : [], tool: String(v.fields.tool ?? ""),
        detail: String(v.fields.detail ?? ""),
        affects: affects.get(v.id) ?? (v.fields.target ? [String(v.fields.target)] : []),
      }));
      let hosts: Host[] | null = null;
      if (s.include_hosts !== false) {
        const rows = await all<Asset>(`/api/projects/${pid}/assets`, { type: "host" }, 1000);
        hosts = rows.map((h) => ({ label: h.label, hostname: String(h.fields.hostname ?? ""), os: String(h.fields.os ?? ""),
          services: (servicesOn.get(h.id) ?? []).sort((a, b) => a.localeCompare(b, undefined, { numeric: true })) }));
      }
      const notes = s.include_notes === false ? [] : await ctx.api.get<{ title: string; body: string }[]>(`/api/projects/${pid}/notes`);

      run.progress({ text: "Writing the document" });
      const date = new Date();
      const title = String(s.title || "Security assessment report");
      const author = String(s.author || run.user?.display_name || "");
      const blocks = buildReport({
        project: { name: project.name, codename: project.codename, description: project.description ?? "", start: project.start_date, end: project.end_date },
        title, author, date, findings, hosts, notes, minSeverity: severityOf(s.min_severity ?? "low") as Severity,
      });
      const bytes = await docx(blocks, { title: `${title}: ${project.name}`, author, when: date });
      const name = `${project.codename || "report"}-report-${date.toISOString().slice(0, 10)}.docx`.replace(/[^\w.-]+/g, "-");

      let card = run.task;
      if (!card) {
        const made = await ctx.api.post<{ id: string; title: string }>(`/api/projects/${pid}/tasks`, {
          title: `Report: ${date.toISOString().slice(0, 10)}`,
          description: `${title}, written by the DOCX report plugin. The document is in Attachments.`,
        });
        card = { id: made.id, title: made.title };
      }
      await ctx.api.upload(`/api/tasks/${card.id}/attachments`, { name, bytes, contentType: DOCX_TYPE });
      const counted = blocks.filter((b) => b.kind === "heading" && b.level === 2 && /^\d+\. /.test(b.text)).length;
      return `Wrote ${name} (${counted} finding${counted === 1 ? "" : "s"}) on “${card.title}”`;
    });
  },
});
