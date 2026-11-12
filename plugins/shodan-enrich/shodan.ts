/** How a Shodan host lookup (GET /shodan/host/{ip}) becomes Castor assets and relationships. Pure:
 *  tested by shodan_test.ts on samples/host.json. */
import type { BulkAsset, BulkLink } from "@castor/plugin";

export interface ShodanBanner {
  port: number;
  transport?: string;
  product?: string;
  version?: string;
  _shodan?: { module?: string };
  http?: { title?: string | null; server?: string | null; status?: number | null };
  ssl?: { versions?: string[]; cert?: { subject?: { CN?: string }; expires?: string } };
  vulns?: Record<string, { cvss?: number | string | null; summary?: string; verified?: boolean }>;
}

export interface ShodanHost {
  ip_str: string;
  hostnames?: string[];
  os?: string | null;
  org?: string | null;
  isp?: string | null;
  asn?: string | null;
  country_code?: string | null;
  city?: string | null;
  vulns?: string[];
  last_update?: string;
  data?: ShodanBanner[];
}

export interface Found {
  services: number;
  urls: number;
  vulns: number;
}

export function severity(cvss: number | string | null | undefined): string {
  const n = Number(cvss);
  if (!Number.isFinite(n) || n <= 0) return "info";
  return n >= 9 ? "critical" : n >= 7 ? "high" : n >= 4 ? "medium" : "low";
}

/** "20270101000000Z" → "2027-01-01". */
function certDate(v: string | undefined): string | undefined {
  const m = v?.match(/^(\d{4})(\d{2})(\d{2})/);
  return m ? `${m[1]}-${m[2]}-${m[3]}` : undefined;
}

const clip = (v: string | undefined, n: number) => (v && v.length > n ? v.slice(0, n) + "…" : v);

export function mapHost(r: ShodanHost): { assets: BulkAsset[]; links: BulkLink[]; found: Found } {
  const ip = r.ip_str;
  const hostRef = `host:${ip}`;
  const assets: BulkAsset[] = [{
    ref: hostRef,
    type: "host",
    fields: {
      ip, os: r.os || undefined, status: "up", org: r.org || undefined, isp: r.isp || undefined, asn: r.asn || undefined,
      country: r.country_code || undefined, city: r.city || undefined, shodan_last_update: r.last_update?.slice(0, 10),
    },
  }];
  const links: BulkLink[] = [];
  const found: Found = { services: 0, urls: 0, vulns: 0 };
  for (const name of r.hostnames ?? []) {
    const ref = `domain:${name}`;
    assets.push({ ref, type: "domain", fields: { name, ips: [ip] } });
    links.push({ from: ref, to: hostRef, kind: "resolves_to" });
  }
  const covered = new Set<string>();
  for (const b of r.data ?? []) {
    const proto = (b.transport || "tcp").toLowerCase();
    const svcRef = `service:${ip}:${b.port}/${proto}`;
    const tls = (b.ssl?.versions ?? []).filter((v) => !v.startsWith("-"));
    assets.push({
      ref: svcRef,
      type: "service",
      fields: {
        host: ip, port: b.port, proto, name: b._shodan?.module || undefined, product: b.product || undefined, version: b.version || undefined,
        state: "open", tls: tls.length ? tls.join(", ") : undefined, cert_subject: b.ssl?.cert?.subject?.CN || undefined,
        cert_expires: certDate(b.ssl?.cert?.expires),
      },
    });
    links.push({ from: svcRef, to: hostRef, kind: "on" });
    found.services++;
    if (b.http) {
      const scheme = b.ssl || b._shodan?.module === "https" ? "https" : "http";
      const url = `${scheme}://${ip}${(scheme === "https" && b.port === 443) || (scheme === "http" && b.port === 80) ? "" : `:${b.port}`}/`;
      const urlRef = `url:${url}`;
      assets.push({ ref: urlRef, type: "url", fields: { url, status: b.http.status ?? undefined, title: b.http.title ?? undefined, webserver: b.http.server ?? undefined } });
      links.push({ from: urlRef, to: hostRef, kind: "on" });
      found.urls++;
    }
    for (const [cve, v] of Object.entries(b.vulns ?? {})) {
      const id = cve.toUpperCase();
      covered.add(id);
      const ref = `vuln:${id}:${b.port}`;
      assets.push({
        ref,
        type: "vulnerability",
        fields: { id, target: `${ip}:${b.port}`, title: id, severity: severity(v.cvss), cves: [id], tool: "Shodan", detail: clip(v.summary, 2000),
                  cvss: Number(v.cvss) || undefined, verified: v.verified ?? undefined },
      });
      links.push({ from: ref, to: svcRef, kind: "affects" });
      found.vulns++;
    }
  }
  // CVEs Shodan lists for the host without saying which service.
  for (const cve of r.vulns ?? []) {
    const id = cve.toUpperCase();
    if (covered.has(id)) continue;
    const ref = `vuln:${id}`;
    assets.push({ ref, type: "vulnerability", fields: { id, target: ip, title: id, cves: [id], tool: "Shodan" } });
    links.push({ from: ref, to: hostRef, kind: "affects" });
    found.vulns++;
  }
  return { assets, links, found };
}

const PRIVATE = [/^10\./, /^127\./, /^169\.254\./, /^192\.168\./, /^172\.(1[6-9]|2\d|3[01])\./, /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./, /^0\./, /^2(2[4-9]|[3-5]\d)\./];

/** An address Shodan could have seen: public IPv4 (not private, loopback, link-local, CGNAT or multicast). */
export function isPublicIp(ip: unknown): ip is string {
  return typeof ip === "string" && /^\d{1,3}(\.\d{1,3}){3}$/.test(ip) && !PRIVATE.some((r) => r.test(ip));
}
