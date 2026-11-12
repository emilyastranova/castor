/** What goes in the report, from a project's data: a pure function, so it's tested without Castor. */
import type { Block, Cell } from "./docx.ts";

export const SEVERITIES = ["critical", "high", "medium", "low", "info"] as const;
export type Severity = (typeof SEVERITIES)[number];
const LABEL: Record<Severity, string> = { critical: "Critical", high: "High", medium: "Medium", low: "Low", info: "Informational" };
// Cell shading per severity, with text that reads on it.
const SHADE: Record<Severity, { fill: string; color: string }> = {
  critical: { fill: "7A1C2E", color: "FFFFFF" },
  high: { fill: "C4372E", color: "FFFFFF" },
  medium: { fill: "E8A33B", color: "1B1B21" },
  low: { fill: "3E7CB1", color: "FFFFFF" },
  info: { fill: "D9D8E0", color: "1B1B21" },
};

export interface Finding {
  id: string;
  title: string;
  severity: Severity;
  cves: string[];
  tool: string;
  detail: string;
  /** What it affects: host or service labels. */
  affects: string[];
}

export interface Host {
  label: string;
  hostname: string;
  os: string;
  services: string[];
}

export interface ReportData {
  project: { name: string; codename: string; description: string; start: string | null; end: string | null };
  title: string;
  author: string;
  date: Date;
  findings: Finding[];
  hosts: Host[] | null;
  notes: { title: string; body: string }[];
  minSeverity: Severity;
}

export function severityOf(v: unknown): Severity {
  const s = String(v ?? "").toLowerCase();
  return (SEVERITIES as readonly string[]).includes(s) ? (s as Severity) : "info";
}

const rank = (s: Severity) => SEVERITIES.indexOf(s);
export const atLeast = (s: Severity, min: Severity) => rank(s) <= rank(min);

/** Markdown, as plain paragraphs: no markup characters left over in a document. */
export function plain(markdown: string): string[] {
  return markdown
    .replace(/```[\s\S]*?```/g, (m) => m.replace(/```\w*\n?/g, ""))
    .split(/\n{2,}/)
    .map((p) => p
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/^#{1,6}\s+/gm, "")
      .replace(/^\s*[-*+]\s+/gm, "• ")
      .replace(/(\*\*|__)(.+?)\1/g, "$2")
      .replace(/(^|\W)[*_](\S.*?)[*_](?=\W|$)/g, "$1$2")
      .replace(/`([^`]+)`/g, "$1")
      .trim())
    .filter(Boolean);
}

const fmtDate = (d: Date) => d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
const DETAIL_MAX = 3000;

export function buildReport(r: ReportData): Block[] {
  const findings = r.findings.filter((f) => atLeast(f.severity, r.minSeverity))
    .sort((a, b) => rank(a.severity) - rank(b.severity) || a.title.localeCompare(b.title));
  const counts = Object.fromEntries(SEVERITIES.map((s) => [s, findings.filter((f) => f.severity === s).length])) as Record<Severity, number>;
  const shown = SEVERITIES.filter((s) => atLeast(s, r.minSeverity));
  const out: Block[] = [];

  out.push({ kind: "title", text: r.title });
  out.push({ kind: "subtitle", text: r.project.name + (r.project.codename && r.project.codename !== r.project.name ? ` (${r.project.codename})` : "") });
  const period = r.project.start ? `${fmtDate(new Date(r.project.start))}${r.project.end ? ` to ${fmtDate(new Date(r.project.end))}` : ""}` : "";
  out.push({ kind: "paragraph", style: "Caption", runs: [[r.author && `Prepared by ${r.author}`, `Written ${fmtDate(r.date)}`, period && `Engagement ${period}`].filter(Boolean).join(" · ")] });
  out.push({ kind: "pagebreak" });

  out.push({ kind: "heading", level: 1, text: "Summary" });
  for (const p of plain(r.project.description)) out.push({ kind: "paragraph", runs: [p] });
  const total = findings.length;
  out.push({ kind: "paragraph", runs: [total
    ? `${total} finding${total === 1 ? "" : "s"}${r.minSeverity !== "info" ? ` rated ${LABEL[r.minSeverity].toLowerCase()} or above` : ""}` +
      (r.hosts ? ` across ${r.hosts.length} host${r.hosts.length === 1 ? "" : "s"}` : "") + "."
    : `No findings${r.minSeverity !== "info" ? ` rated ${LABEL[r.minSeverity].toLowerCase()} or above` : ""} were recorded.`] });
  out.push({ kind: "table", header: ["Severity", "Findings"], widths: [3, 1],
    rows: shown.map((s) => [{ runs: [LABEL[s]], ...SHADE[s], bold: true }, [String(counts[s])]]) });

  if (findings.length) {
    out.push({ kind: "heading", level: 2, text: "At a glance" });
    out.push({ kind: "table", header: ["#", "Finding", "Severity", "Affects"], widths: [0.5, 4, 1.3, 3],
      rows: findings.map((f, i): Cell[] => [[String(i + 1)], [f.title], { runs: [LABEL[f.severity]], ...SHADE[f.severity] },
        [affectsText(f.affects, 3)]]) });
  }

  out.push({ kind: "heading", level: 1, text: "Findings" });
  if (!findings.length) out.push({ kind: "paragraph", runs: ["None."] });
  findings.forEach((f, i) => {
    out.push({ kind: "heading", level: 2, text: `${i + 1}. ${f.title}` });
    const rows: Cell[][] = [
      [[{ text: "Severity", bold: true }], { runs: [LABEL[f.severity]], ...SHADE[f.severity] }],
      [[{ text: "Affects", bold: true }], [affectsText(f.affects, 25)]],
    ];
    if (f.cves.length) rows.push([[{ text: "CVEs", bold: true }], [f.cves.join(", ")]]);
    if (f.tool) rows.push([[{ text: "Found by", bold: true }], [f.tool]]);
    rows.push([[{ text: "Reference", bold: true }], [{ text: f.id, mono: true }]]);
    out.push({ kind: "table", header: null, widths: [1, 4], rows });
    const detail = f.detail.length > DETAIL_MAX ? f.detail.slice(0, DETAIL_MAX) + "…" : f.detail;
    for (const p of plain(detail)) out.push({ kind: "paragraph", runs: [p] });
  });

  if (r.hosts) {
    out.push({ kind: "heading", level: 1, text: "Hosts" });
    if (!r.hosts.length) out.push({ kind: "paragraph", runs: ["None recorded."] });
    else {
      out.push({ kind: "table", header: ["Host", "Name", "Operating system", "Services"], widths: [2, 3, 3, 3],
        rows: [...r.hosts].sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }))
          .map((h) => [[{ text: h.label, mono: true }], [h.hostname], [h.os], [h.services.join(", ")]]) });
    }
  }

  if (r.notes.length) {
    out.push({ kind: "heading", level: 1, text: "Notes" });
    for (const n of r.notes) {
      if (n.title) out.push({ kind: "heading", level: 3, text: n.title });
      for (const p of plain(n.body)) out.push({ kind: "paragraph", runs: [p] });
    }
  }
  return out;
}

function affectsText(list: string[], max: number): string {
  if (!list.length) return "—";
  return list.slice(0, max).join(", ") + (list.length > max ? ` and ${list.length - max} more` : "");
}
