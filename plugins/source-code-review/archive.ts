/** A repository archive's files: .zip (archive_zip.ts), .tar and .tar.gz (here). Paths come out
 *  relative to the repository, without the one folder GitHub and `git archive --prefix` wrap them in. */
import { isZip, readZip } from "./archive_zip.ts";

export interface Entry {
  path: string;
  size: number;
  read(): Promise<Uint8Array>;
}

const isGzip = (b: Uint8Array) => b.length > 2 && b[0] === 0x1f && b[1] === 0x8b;
const isTar = (b: Uint8Array) => b.length >= 512 && new TextDecoder().decode(b.subarray(257, 262)) === "ustar";

async function gunzip(bytes: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([bytes as Uint8Array<ArrayBuffer>]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

function text(b: Uint8Array): string {
  const end = b.indexOf(0);
  return new TextDecoder().decode(end >= 0 ? b.subarray(0, end) : b);
}

/** A tar's regular files (ustar, with GNU long names and pax paths). */
export function readTar(bytes: Uint8Array): Entry[] {
  const out: Entry[] = [];
  let at = 0, longName: string | null = null, paxPath: string | null = null;
  while (at + 512 <= bytes.length) {
    const h = bytes.subarray(at, at + 512);
    if (h.every((x) => x === 0)) break;
    const size = parseInt(text(h.subarray(124, 136)).trim() || "0", 8);
    if (!Number.isFinite(size) || size < 0) throw new Error("The archive is damaged");
    const type = String.fromCharCode(h[156] || 48);
    const body = at + 512;
    const data = bytes.subarray(body, body + size);
    if (type === "L") longName = text(data);
    else if (type === "x") {
      for (const line of new TextDecoder().decode(data).split("\n")) {
        const m = /^\d+ path=(.*)$/.exec(line);
        if (m) paxPath = m[1];
      }
    } else if (type === "0" || type === "7") {
      const prefix = text(h.subarray(345, 500));
      const name = paxPath ?? longName ?? (prefix ? `${prefix}/${text(h.subarray(0, 100))}` : text(h.subarray(0, 100)));
      out.push({ path: name, size, read: () => Promise.resolve(data) });
      longName = paxPath = null;
    } else if (type !== "g") longName = paxPath = null;
    at = body + Math.ceil(size / 512) * 512;
  }
  return out;
}

/** The archive's files, and the name of the folder they were in (a repository's name, usually). */
export async function readArchive(bytes: Uint8Array, fileName: string): Promise<{ entries: Entry[]; folder: string | null }> {
  let entries: Entry[];
  if (isZip(bytes)) {
    entries = readZip(bytes).map((e) => ({ path: e.name, size: e.size, read: e.read }));
  } else {
    const raw = isGzip(bytes) ? await gunzip(bytes) : bytes;
    if (!isTar(raw)) throw new Error(`${fileName} isn't a .zip, .tar or .tar.gz`);
    entries = readTar(raw);
  }
  entries = entries
    .map((e) => ({ ...e, path: e.path.replace(/\\/g, "/").replace(/^(\.\/)+/, "") }))
    .filter((e) => e.path && !e.path.endsWith("/") && !e.path.split("/").some((s) => s === ".." || s === ""));
  const tops = new Set(entries.map((e) => (e.path.includes("/") ? e.path.slice(0, e.path.indexOf("/")) : "")));
  let folder: string | null = null;
  if (tops.size === 1 && !tops.has("")) {
    folder = [...tops][0];
    entries = entries.map((e) => ({ ...e, path: e.path.slice(folder!.length + 1) }));
  }
  return { entries, folder };
}

/** A repository's name from its folder (shop-main) or file name (shop-1.2.0.tar.gz): "shop". */
export function repoName(folder: string | null, fileName: string): string {
  const base = folder ?? fileName.replace(/\.(zip|tgz|tar(\.gz)?)$/i, "");
  return base.replace(/-(main|master|trunk|develop|head)$/i, "").replace(/-v?\d+(\.\d+)*([-.][\w.]+)?$/, "").replace(/-[0-9a-f]{7,40}$/, "") || base;
}
