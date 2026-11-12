// deno test --allow-write=samples (writes samples/sample-report.docx to look at; not committed)
import { crc32, docx, documentXml } from "./docx.ts";
import { buildReport, plain, type ReportData } from "./report.ts";

function assert(ok: unknown, why = "expected true"): asserts ok {
  if (!ok) throw new Error(why);
}
function assertEquals(a: unknown, e: unknown, why = "") {
  if (JSON.stringify(a) !== JSON.stringify(e)) throw new Error(`${why ? why + ": " : ""}${JSON.stringify(a)} !== ${JSON.stringify(e)}`);
}

export const SAMPLE: ReportData = {
  project: { name: "Northwind external test", codename: "NW-EXT", description: "An **external** test of Northwind's internet-facing hosts.\n\nScope: `203.0.113.0/24`.",
    start: "2026-10-05T00:00:00Z", end: "2026-10-16T00:00:00Z" },
  title: "Security assessment report", author: "A. Tester", date: new Date("2026-10-20T09:00:00Z"),
  findings: [
    { id: "nessus-57608", title: "SMB signing not required", severity: "medium", cves: [], tool: "Nessus", detail: "Signing is not required on the remote SMB server.", affects: ["203.0.113.10:445/tcp"] },
    { id: "CVE-2024-6387", title: "OpenSSH regreSSHion <race> & \"quotes\"", severity: "critical", cves: ["CVE-2024-6387"], tool: "nuclei", detail: "A signal handler race.\u0007", affects: ["203.0.113.10:22/tcp", "203.0.113.11:22/tcp"] },
    { id: "info-1", title: "Service detection", severity: "info", cves: [], tool: "Nessus", detail: "", affects: [] },
  ],
  hosts: [{ label: "203.0.113.11", hostname: "vpn.northwind.example", os: "Linux", services: ["22/tcp", "443/tcp"] },
    { label: "203.0.113.10", hostname: "mail.northwind.example", os: "Windows Server 2019", services: ["22/tcp", "445/tcp"] }],
  notes: [{ title: "Out of scope", body: "- The **VPN** appliance itself\n- [Third parties](https://example.com)" }],
  minSeverity: "low",
};

Deno.test("findings by severity, the low ones left out", () => {
  const blocks = buildReport(SAMPLE);
  const headings = blocks.filter((b) => b.kind === "heading").map((b) => (b as { text: string }).text);
  assertEquals(headings, ["Summary", "At a glance", "Findings", "1. OpenSSH regreSSHion <race> & \"quotes\"", "2. SMB signing not required",
    "Hosts", "Notes", "Out of scope"]);
});

Deno.test("markdown comes out as plain text", () => {
  assertEquals(plain("# Hi\n\nSome **bold** and `code` and [a link](https://x).\n\n- one\n- two"), ["Hi", "Some bold and code and a link.", "• one\n• two"]);
});

Deno.test("the XML is escaped and has no control characters", () => {
  const xml = documentXml(buildReport(SAMPLE));
  assert(xml.includes("OpenSSH regreSSHion &lt;race&gt; &amp; &quot;quotes&quot;"));
  assert(!xml.includes("\u0007"));
});

Deno.test("a .docx with the parts Word needs", async () => {
  const bytes = await docx(buildReport(SAMPLE), { title: "Report", author: "A. Tester" });
  const text = new TextDecoder("latin1").decode(bytes);
  for (const part of ["[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml", "word/numbering.xml", "docProps/core.xml"]) {
    assert(text.includes(part), part);
  }
  assertEquals(crc32(new TextEncoder().encode("123456789")), 0xcbf43926, "CRC-32 check value");
  await Deno.writeFile(new URL("./samples/sample-report.docx", import.meta.url), bytes);
});
