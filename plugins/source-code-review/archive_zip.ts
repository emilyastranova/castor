/** Just enough of the .zip format to read a repository archive: stored and deflated entries,
 *  no encryption, no zip64. Uses the runtime's DecompressionStream, so no dependencies. */

export interface ZipEntry {
  name: string;
  size: number;
  read(): Promise<Uint8Array>;
}

const EOCD = 0x06054b50;
const CENTRAL = 0x02014b50;
const LOCAL = 0x04034b50;

export function isZip(bytes: Uint8Array): boolean {
  return bytes.length >= 4 && bytes[0] === 0x50 && bytes[1] === 0x4b && bytes[2] === 0x03 && bytes[3] === 0x04;
}

export function readZip(bytes: Uint8Array): ZipEntry[] {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  // The end-of-central-directory record is in the last 64 KB (it can have a comment).
  let end = -1;
  for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 22 - 0xffff); i--) {
    if (view.getUint32(i, true) === EOCD) {
      end = i;
      break;
    }
  }
  if (end < 0) throw new Error("That isn't a .zip file (or it's cut short)");
  const count = view.getUint16(end + 10, true);
  let at = view.getUint32(end + 16, true);
  if (count === 0xffff || at === 0xffffffff) throw new Error("This .zip is in the zip64 format, which the plugin can't read yet; make a .tar.gz instead (git archive)");
  const entries: ZipEntry[] = [];
  const decoder = new TextDecoder();
  for (let n = 0; n < count; n++) {
    if (view.getUint32(at, true) !== CENTRAL) throw new Error("The .zip's directory is damaged");
    const flags = view.getUint16(at + 8, true);
    const method = view.getUint16(at + 10, true);
    const compressed = view.getUint32(at + 20, true);
    const size = view.getUint32(at + 24, true);
    const nameLen = view.getUint16(at + 28, true);
    const extraLen = view.getUint16(at + 30, true);
    const commentLen = view.getUint16(at + 32, true);
    const local = view.getUint32(at + 42, true);
    const name = decoder.decode(bytes.subarray(at + 46, at + 46 + nameLen));
    at += 46 + nameLen + extraLen + commentLen;
    if (name.endsWith("/")) continue;
    if (flags & 1) throw new Error(`${name} is encrypted: collect without a zip password (or unzip it first)`);
    if (method !== 0 && method !== 8) throw new Error(`${name} is compressed in a way the plugin can't read (method ${method})`);
    entries.push({
      name,
      size,
      async read() {
        if (view.getUint32(local, true) !== LOCAL) throw new Error(`${name}: damaged entry`);
        const start = local + 30 + view.getUint16(local + 26, true) + view.getUint16(local + 28, true);
        const data = bytes.subarray(start, start + compressed);
        if (method === 0) return data;
        const stream = new Blob([data as Uint8Array<ArrayBuffer>]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
        return new Uint8Array(await new Response(stream).arrayBuffer());
      },
    });
  }
  return entries;
}
