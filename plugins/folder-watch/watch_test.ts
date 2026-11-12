// deno test
import { actionFor, folderName, Settle, stamped } from "./watch.ts";

function assertEquals(a: unknown, e: unknown, why = "") {
  if (JSON.stringify(a) !== JSON.stringify(e)) throw new Error(`${why ? why + ": " : ""}${JSON.stringify(a)} !== ${JSON.stringify(e)}`);
}

const NESSUS = { plugin: { id: "nessus-import", name: "Nessus import" }, id: "import", label: "Import .nessus file", on: ["project", "card"],
  file: { accept: [".nessus", ".xml"], max_mb: 500 } };
const BH = { plugin: { id: "bloodhound", name: "BloodHound bridge" }, id: "import", label: "Import SharpHound data", on: ["project"],
  file: { accept: [".zip", ".json"], max_mb: 300 } };
const SELF = { plugin: { id: "folder-watch", name: "Watch folder" }, id: "x", label: "x", on: ["project"], file: { accept: [".txt"], max_mb: 1 } };

Deno.test("a file waits until its size stops changing", () => {
  const s = new Settle();
  assertEquals([s.ready("a", 10), s.ready("a", 20), s.ready("a", 20)], [false, false, true]);
});

Deno.test("each file goes to the action that takes its kind", () => {
  const all = [NESSUS, BH, SELF];
  assertEquals(actionFor("scan.NESSUS", all, "folder-watch")?.plugin.id, "nessus-import");
  assertEquals(actionFor("corp.zip", all, "folder-watch")?.plugin.id, "bloodhound");
  assertEquals(actionFor("notes.txt", all, "folder-watch"), null, "never itself");
  assertEquals(actionFor("photo.png", all, "folder-watch"), null);
});

Deno.test("moved-aside names don't collide", () => {
  assertEquals(stamped("a.zip", new Date("2026-11-11T10:00:00.5Z")), "2026-11-11T10-00-00-500Z-a.zip");
});

Deno.test("project folders stay inside the watched folder", () => {
  assertEquals([folderName("NW-EXT"), folderName("../etc"), folderName("a/b c")], ["NW-EXT", "_-etc", "a-b-c"]);
});
