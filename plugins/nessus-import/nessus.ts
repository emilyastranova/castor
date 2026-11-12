/**
 * Reading .nessus files (Nessus v2 XML, `NessusClientData_v2`), one ReportHost at a time so a
 * large scan never has to be in memory whole. A small reader for this one format: no DTDs, no
 * entity definitions (only the five predefined ones and numeric references), CDATA kept as text.
 */

export interface NessusItem {
  port: number;
  protocol: string;
  service: string;
  /** 0 info, 1 low, 2 medium, 3 high, 4 critical. */
  severity: number;
  pluginId: string;
  pluginName: string;
  pluginFamily: string;
  synopsis?: string;
  description?: string;
  solution?: string;
  riskFactor?: string;
  cvss3?: number;
  cvss?: number;
  cves: string[];
  output?: string;
}

export interface NessusHost {
  /** The ReportHost's name: an address or a name, as the scan was given it. */
  name: string;
  tags: Record<string, string>;
  items: NessusItem[];
}

const ENTITIES: Record<string, string> = { lt: "<", gt: ">", amp: "&", quot: '"', apos: "'" };

/** Text content: CDATA as is, entities decoded (never expanded beyond the predefined ones). */
export function text(raw: string): string {
  return raw
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>|&(#x[0-9a-fA-F]+|#\d+|lt|gt|amp|quot|apos);/g, (_m, cdata: string | undefined, ent: string | undefined) => {
      if (cdata !== undefined) return cdata;
      if (ent!.startsWith("#x")) return String.fromCodePoint(parseInt(ent!.slice(2), 16));
      if (ent!.startsWith("#")) return String.fromCodePoint(parseInt(ent!.slice(1), 10));
      return ENTITIES[ent!];
    })
    .trim();
}

function attrs(raw: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of raw.matchAll(/([A-Za-z_][\w:.-]*)\s*=\s*"([^"]*)"/g)) out[m[1]] = text(m[2]);
  return out;
}

function child(body: string, name: string): string | undefined {
  const m = body.match(new RegExp(`<${name}>([\\s\\S]*?)</${name}>`));
  return m ? text(m[1]) : undefined;
}

function num(v: string | undefined): number | undefined {
  const n = v === undefined ? NaN : Number(v);
  return Number.isFinite(n) ? n : undefined;
}

/** One `<ReportHost …>…</ReportHost>` element. */
export function parseHost(xml: string): NessusHost {
  const open = xml.match(/<ReportHost\b([^>]*)>/);
  const name = open ? (attrs(open[1]).name ?? "") : "";
  const tags: Record<string, string> = {};
  const props = xml.match(/<HostProperties>([\s\S]*?)<\/HostProperties>/);
  if (props) for (const m of props[1].matchAll(/<tag\s+name="([^"]+)"\s*>([\s\S]*?)<\/tag>/g)) tags[text(m[1])] = text(m[2]);
  const items: NessusItem[] = [];
  for (const m of xml.matchAll(/<ReportItem\b([^>]*?)(?:\/>|>([\s\S]*?)<\/ReportItem>)/g)) {
    const a = attrs(m[1]);
    const body = m[2] ?? "";
    items.push({
      port: Number(a.port ?? 0) || 0,
      protocol: (a.protocol || "tcp").toLowerCase(),
      service: a.svc_name ?? "",
      severity: Math.max(0, Math.min(4, Number(a.severity ?? 0) || 0)),
      pluginId: a.pluginID ?? "",
      pluginName: a.pluginName ?? "",
      pluginFamily: a.pluginFamily ?? "",
      synopsis: child(body, "synopsis"),
      description: child(body, "description"),
      solution: child(body, "solution"),
      riskFactor: child(body, "risk_factor"),
      cvss3: num(child(body, "cvss3_base_score")),
      cvss: num(child(body, "cvss_base_score")),
      cves: [...body.matchAll(/<cve>([\s\S]*?)<\/cve>/g)].map((c) => text(c[1]).toUpperCase()),
      output: child(body, "plugin_output"),
    });
  }
  return { name, tags, items };
}

/** Every ReportHost in a .nessus stream, as it's read. Throws on something that isn't one. */
export async function* readHosts(stream: ReadableStream<Uint8Array>, onBytes?: (n: number) => void): AsyncGenerator<NessusHost> {
  const decoder = new TextDecoder();
  let buffer = "";
  let checked = false;
  const reader = stream.getReader();
  while (true) {
    const { value, done } = await reader.read();
    if (value) {
      onBytes?.(value.length);
      buffer += decoder.decode(value, { stream: true });
    }
    if (done) buffer += decoder.decode();
    if (!checked && (buffer.length > 4096 || done)) {
      if (!/<NessusClientData_v2[\s>]/.test(buffer.slice(0, 64 * 1024))) {
        throw new Error("This isn't a .nessus v2 file (no NessusClientData_v2): export the scan as .nessus from Nessus");
      }
      if (/<!ENTITY/i.test(buffer.slice(0, 64 * 1024))) throw new Error("The file defines XML entities, which a .nessus export never does; refusing it");
      checked = true;
    }
    while (true) {
      const start = buffer.indexOf("<ReportHost");
      if (start < 0) {
        buffer = buffer.slice(-16); // a tag split across chunks
        break;
      }
      const end = buffer.indexOf("</ReportHost>", start);
      if (end < 0) {
        buffer = buffer.slice(start);
        break;
      }
      yield parseHost(buffer.slice(start, end + "</ReportHost>".length));
      buffer = buffer.slice(end + "</ReportHost>".length);
    }
    if (done) return;
  }
}
