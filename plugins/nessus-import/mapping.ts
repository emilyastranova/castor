/** How a .nessus host becomes Castor assets and relationships (pure: tested by mapping_test.ts). */
import type { BulkAsset, BulkLink } from "@castor/plugin";
import type { NessusHost } from "./nessus.ts";

export const SEVERITY = ["info", "low", "medium", "high", "critical"];
const IPV4 = /^\d{1,3}(\.\d{1,3}){3}$/;
const isIp = (v: string) => IPV4.test(v) || (v.includes(":") && /^[0-9a-fA-F:]+$/.test(v));
const firstLine = (v: string | undefined) => v?.split(/\r?\n/)[0].trim() || undefined;
const clip = (v: string | undefined, n: number) => (v && v.length > n ? v.slice(0, n) + "…" : v);

export interface Counts {
  hosts: number;
  services: number;
  findings: number;
  bySeverity: number[];
}

/** One host's assets and links (refs are unique within the call). */
export function mapHost(h: NessusHost, info: boolean, counts: Counts): { assets: BulkAsset[]; links: BulkLink[] } {
  const ip = h.tags["host-ip"] || (isIp(h.name) ? h.name : undefined);
  const hostname = h.tags["host-fqdn"] || h.tags["hostname"] || (!isIp(h.name) ? h.name : undefined);
  const target = ip ?? hostname;
  if (!target) return { assets: [], links: [] };
  const hostRef = `host:${target}`;
  const host: Record<string, unknown> = { ip, hostname: hostname?.toLowerCase(), status: "up" };
  if (h.tags["operating-system"]) host.os = firstLine(h.tags["operating-system"]);
  if (h.tags["mac-address"]) host.mac = firstLine(h.tags["mac-address"]);
  if (h.tags["netbios-name"]) host.netbios_name = h.tags["netbios-name"];
  const assets: BulkAsset[] = [];
  const links: BulkLink[] = [];
  const services = new Set<string>();
  for (const item of h.items) {
    // Findings that say something about the host itself.
    if (item.pluginId === "57608" || /SMB Signing not required/i.test(item.pluginName)) host.smb_signing = false;
    if (item.pluginId === "96982" || /SMB.*Version 1 .*Enabled|SMBv1/i.test(item.pluginName)) host.smbv1 = true;

    let serviceRef: string | null = null;
    if (item.port > 0) {
      serviceRef = `service:${target}:${item.port}/${item.protocol}`;
      if (!services.has(serviceRef)) {
        services.add(serviceRef);
        assets.push({ ref: serviceRef, type: "service", fields: { host: target, port: item.port, proto: item.protocol, name: item.service || undefined, state: "open" } });
        links.push({ from: serviceRef, to: hostRef, kind: "on" });
      }
    }
    if (item.severity === 0 && !info) continue;
    const ref = `vuln:${target}:${item.port}:${item.pluginId}`;
    assets.push({
      ref,
      type: "vulnerability",
      fields: {
        id: item.cves.length === 1 ? item.cves[0] : `NESSUS-${item.pluginId}`,
        title: item.pluginName,
        severity: SEVERITY[item.severity],
        target: item.port > 0 ? `${target}:${item.port}` : target,
        cves: item.cves,
        tool: "Nessus",
        detail: clip(item.synopsis ?? item.description, 2000),
        nessus_plugin: item.pluginId,
        plugin_family: item.pluginFamily || undefined,
        solution: clip(item.solution, 2000),
        cvss3: item.cvss3,
        cvss: item.cvss,
        output: clip(item.output, 4000),
      },
    });
    links.push({ from: ref, to: serviceRef ?? hostRef, kind: "affects" });
    counts.findings++;
    counts.bySeverity[item.severity]++;
  }
  assets.unshift({ ref: hostRef, type: "host", fields: host });
  counts.hosts++;
  counts.services += services.size;
  return { assets, links };
}
