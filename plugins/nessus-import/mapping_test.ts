// deno test --allow-read=samples
import { type Counts, mapHost } from "./mapping.ts";
import { parseHost, readHosts, text } from "./nessus.ts";

function assert(ok: unknown, why: string): asserts ok {
  if (!ok) throw new Error(why);
}

async function hosts(file: string) {
  const f = await Deno.open(`samples/${file}`);
  const out = [];
  for await (const h of readHosts(f.readable)) out.push(h);
  return out;
}

const fresh = (): Counts => ({ hosts: 0, services: 0, findings: 0, bySeverity: [0, 0, 0, 0, 0] });

Deno.test("reads every host of the synthetic scan", async () => {
  const all = await hosts("cerno-demo-expanded.nessus");
  assert(all.length === 42, `42 hosts, got ${all.length}`);
  assert(all.reduce((n, h) => n + h.items.length, 0) === 125, "125 findings");
  const web = all.find((h) => h.name === "198.51.100.10")!;
  assert(web.tags["host-fqdn"] === "web01.demo.example", "host properties");
});

Deno.test("reads the real export's details", async () => {
  const [h] = await hosts("ruby-nessus-example-v3.nessus");
  assert(h.items.length === 155, `155 findings, got ${h.items.length}`);
  assert(h.items.some((i) => i.cves.length > 0), "CVEs");
  assert(h.items.some((i) => i.output && i.output.length > 0), "plugin output");
  const counts = fresh();
  const mapped = mapHost(h, false, counts);
  assert(counts.findings === 37, `low and above: 37, got ${counts.findings}`);
  assert(mapped.assets[0].type === "host" && mapped.assets[0].fields.ip === "10.0.0.1", "the host first");
  const refs = new Set(mapped.assets.map((a) => a.ref));
  assert(mapped.links.every((l) => refs.has(l.from) && refs.has(l.to)), "links point at assets in the same call");
  assert(mapped.links.filter((l) => l.kind === "affects").length === 37, "each finding affects something");
  const withInfo = fresh();
  mapHost(h, true, withInfo);
  assert(withInfo.findings === 155, "informational ones on request");
});

Deno.test("host findings set host fields", () => {
  const h = parseHost(`<ReportHost name="10.9.9.9"><HostProperties><tag name="host-ip">10.9.9.9</tag><tag name="netbios-name">FS01</tag></HostProperties>
    <ReportItem port="445" svc_name="cifs" protocol="tcp" severity="2" pluginID="57608" pluginName="SMB Signing not required" pluginFamily="Misc.">
    <synopsis>Signing is &lt;not&gt; required &amp; that&apos;s bad</synopsis><cve>cve-2020-1234</cve></ReportItem></ReportHost>`);
  const mapped = mapHost(h, false, fresh());
  const host = mapped.assets[0].fields;
  assert(host.smb_signing === false && host.netbios_name === "FS01", "smb_signing and the NetBIOS name");
  const vuln = mapped.assets.find((a) => a.type === "vulnerability")!;
  assert(vuln.fields.id === "CVE-2020-1234" && vuln.fields.detail === "Signing is <not> required & that's bad", "entities and the CVE id");
});

Deno.test("refuses what isn't a .nessus file", async () => {
  const bad = new Blob(["<?xml version='1.0'?><html></html>"]).stream();
  let failed = false;
  try {
    for await (const _ of readHosts(bad)) { /* nothing */ }
  } catch {
    failed = true;
  }
  assert(failed, "not NessusClientData_v2");
  assert(text("<![CDATA[a < b]]>") === "a < b", "CDATA");
});
