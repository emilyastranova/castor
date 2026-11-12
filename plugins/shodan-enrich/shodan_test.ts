// deno test --allow-read=samples
import { isPublicIp, mapHost, severity, type ShodanHost } from "./shodan.ts";

function assert(ok: unknown, why: string): asserts ok {
  if (!ok) throw new Error(why);
}

Deno.test("maps a host lookup", async () => {
  const host = JSON.parse(await Deno.readTextFile("samples/host.json")) as ShodanHost;
  const { assets, links, found } = mapHost(host);
  assert(found.services === 3 && found.urls === 2 && found.vulns === 2, JSON.stringify(found));
  const byType = (t: string) => assets.filter((a) => a.type === t);
  assert(byType("domain").length === 2, "a domain per hostname");
  const https = byType("service").find((s) => s.fields.port === 443)!;
  assert(https.fields.tls === "TLSv1.2, TLSv1.3" && https.fields.cert_expires === "2027-01-01", "TLS and the certificate");
  assert(byType("url").some((u) => u.fields.url === "https://203.0.113.10/" && u.fields.webserver === "Apache/2.4.49 (Ubuntu)"), "a URL");
  const ssh = byType("vulnerability").find((v) => v.fields.id === "CVE-2023-38408")!;
  assert(ssh.fields.severity === "critical" && ssh.fields.target === "203.0.113.10:22", "severity and target");
  const refs = new Set(assets.map((a) => a.ref));
  assert(links.every((l) => refs.has(l.from) && refs.has(l.to)), "links point inside the call");
  assert(links.filter((l) => l.kind === "affects").length === 2, "each CVE affects the service it was seen on");
});

Deno.test("public addresses only", () => {
  for (const ip of ["10.0.0.1", "192.168.1.1", "172.20.0.1", "127.0.0.1", "169.254.1.1", "100.64.0.1", "224.0.0.1"]) assert(!isPublicIp(ip), ip);
  for (const ip of ["203.0.113.10", "8.8.8.8", "172.32.0.1"]) assert(isPublicIp(ip), ip);
  assert(severity(9.8) === "critical" && severity("5.0") === "medium" && severity(null) === "info", "severity");
});
