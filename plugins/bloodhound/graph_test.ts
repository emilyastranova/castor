// deno test --allow-read=samples (no dependencies: the checks are below)
import { addFile, emptyGraph, find, pack, Paths, unpack } from "./graph.ts";
import { mapGraph } from "./mapping.ts";
import { isZip, readZip } from "./zip.ts";

function assert(ok: unknown, why = "expected true"): asserts ok {
  if (!ok) throw new Error(why);
}
function assertEquals(actual: unknown, expected: unknown, why = "") {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) throw new Error(`${why ? why + ": " : ""}${a} !== ${e}`);
}

const D = "S-1-5-21-1111-2222-3333";

async function sample() {
  const bytes = await Deno.readFile(new URL("./samples/corp-sharphound.zip", import.meta.url));
  assert(isZip(bytes));
  const g = emptyGraph();
  for (const entry of readZip(bytes)) {
    const text = new TextDecoder().decode(await entry.read()).replace(/^﻿/, "");
    assert(addFile(g, JSON.parse(text)) > 0, entry.name);
  }
  return g;
}

Deno.test("reads a SharpHound zip into the graph", async () => {
  const g = await sample();
  assertEquals(g.nodes.get(`${D}-1103`)?.name, "ALICE@CORP.LOCAL");
  assert(g.edges.has(`${D}-1104|${D}-1110|MemberOf`), "bob is in IT Admins");
  assert(g.edges.has(`${D}-1110|${D}-1002|AdminTo`), "IT Admins are admins on SRV01 (v6 LocalGroups)");
  assert(g.edges.has(`${D}-1002|${D}-1105|HasSession`), "SRV01 has carol's session");
  assert(g.edges.has(`${D}-1107|${D}-1104|ForceChangePassword`), "an ACE");
  assert(g.edges.has(`${D}-1109|${D}|DCSync`), "GetChanges + GetChangesAll make DCSync");
  assert(g.edges.has(`${D}-1000|${D}|DCFor`), "a DC controls its domain");
});

Deno.test("shortest paths to Domain Admins", async () => {
  const paths = new Paths(await sample());
  const bob = paths.from(`${D}-1104`)!;
  assertEquals(bob.map((s) => s.rel ?? "").join(" "), "MemberOf AdminTo HasSession MemberOf ");
  assertEquals(bob.at(-1)!.name, "DOMAIN ADMINS@CORP.LOCAL");
  assertEquals(paths.dist.get(`${D}-1103`), 6, "alice: admin on WS01, which has bob's session…");
  assertEquals(paths.dist.get(`${D}-1107`), 5, "svc_sql resets bob's password");
  assertEquals(paths.dist.get(`${D}-1109`), 1, "backup_svc can DCSync");
  assertEquals(paths.from(`${D}-1106`), null, "dave has no way there");
});

Deno.test("packs and unpacks the same graph", async () => {
  const g = await sample();
  const back = unpack(pack(g));
  assertEquals(back.nodes.size, g.nodes.size);
  assertEquals([...back.edges].sort(), [...g.edges].sort());
});

Deno.test("finds a node by what Castor calls it", async () => {
  const g = await sample();
  assertEquals(find(g, { label: "corp\\bob" })?.sid, `${D}-1104`);
  assertEquals(find(g, { username: "bob" })?.sid, `${D}-1104`);
  assertEquals(find(g, { hostname: "srv01.corp.local" })?.sid, `${D}-1002`);
  assertEquals(find(g, { label: "ws01" })?.sid, `${D}-1001`);
  assertEquals(find(g, { label: "nobody" }), null);
});

Deno.test("maps to Castor's assets and relationships", async () => {
  const m = mapGraph(await sample());
  assertEquals(m.counts, { users: 7, hosts: 3, domains: 2 });
  const bob = m.assets.find((a) => a.ref === `user:${D}-1104`)!;
  assertEquals(bob.fields.username, "bob");
  assertEquals(bob.fields.domain, "corp");
  assertEquals([...(bob.fields.groups as string[])].sort(), ["domain users", "it admins"]);
  const svc = m.assets.find((a) => a.ref === `user:${D}-1107`)!;
  assertEquals(svc.fields.kerberoastable, true);
  const dc = m.assets.find((a) => a.ref === `computer:${D}-1000`)!;
  assertEquals(dc.fields.roles, ["dc"]);
  const kinds = (kind: string) => m.links.filter((l) => l.kind === kind).map((l) => `${l.from} ${l.to}`);
  assert(kinds("dc_of").includes(`computer:${D}-1000 domain:${D}`));
  assert(kinds("session_on").includes(`user:${D}-1105 computer:${D}-1002`));
  assert(kinds("admin_on").includes(`user:${D}-1104 computer:${D}-1002`), "through IT Admins");
  assert(kinds("admin_on").includes(`user:${D}-1103 computer:${D}-1001`), "alice directly");
  assert(kinds("trusts").includes(`domain:${D} domain:S-1-5-21-4444-5555-6666`));
  const carol = m.links.find((l) => l.kind === "in_domain" && l.from === `user:${D}-1105`)!;
  assertEquals(carol.fields, { role: "domain_admin" });
});
