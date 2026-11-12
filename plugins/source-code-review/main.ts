/**
 * Source Code Review: a code base as a project's assets. Repositories, source files and symbols
 * (functions, classes, routes…) are asset types this plugin adds; imports, definitions, calls, data
 * flow and tests are relationships between them, and Castor's own "affects" reaches them, so a
 * finding can point at the file or function it's in. An archive import maps files and imports; the
 * Code map tab adds the rest by hand. Sandboxed, no network.
 */
import { type BulkAsset, type BulkLink, type Context, definePlugin } from "@castor/plugin";
import { readArchive, repoName } from "./archive.ts";
import { cleanPath, languageOf, mapRepo, type RepoFile, rolesOf, subjectsOf } from "./code.ts";

const TEXT_MAX = 1_000_000; // bytes of a file read for imports; bigger ones are listed, not read
const ASSET_BATCH = 1000;
const LINK_BATCH = 2000;
const KINDS = ["in_repo", "imports", "defines", "calls", "flows_to", "tests", "serves", "deployed_to", "affects"];
export const REVIEW = ["not started", "in progress", "reviewed", "flagged"];
const SYMBOL_KINDS = ["function", "method", "class", "route", "query", "handler", "variable", "other"];

const plural = (n: number, one: string, many = one + "s") => `${n} ${n === 1 ? one : many}`;
const decoder = new TextDecoder("utf-8", { fatal: false });

async function sha256(bytes: Uint8Array): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", bytes as Uint8Array<ArrayBuffer>);
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function bulkAll(ctx: Context, project: string, assets: BulkAsset[], links: BulkLink[], task?: string,
  progress?: (text: string) => void) {
  const ids: Record<string, string> = {};
  let created = 0, skipped = 0, linked = 0;
  for (let i = 0; i < assets.length; i += ASSET_BATCH) {
    const r = await ctx.assets.bulk(project, { assets: assets.slice(i, i + ASSET_BATCH), task_id: task });
    Object.assign(ids, r.refs);
    created += r.created;
    skipped += r.skipped.length;
    for (const s of r.skipped.slice(0, 3)) ctx.log.warn(`skipped ${s.ref}: ${s.reason}`);
    progress?.(`${Math.min(i + ASSET_BATCH, assets.length)} of ${assets.length} assets`);
  }
  const resolved = links.map((l) => ({ ...l, from: ids[l.from] ?? l.from, to: ids[l.to] ?? l.to }));
  for (let i = 0; i < resolved.length; i += LINK_BATCH) {
    const r = await ctx.assets.bulk(project, { assets: [], links: resolved.slice(i, i + LINK_BATCH), task_id: task });
    linked += r.links;
    for (const s of r.skipped.slice(0, 3)) ctx.log.warn(`skipped ${s.ref}: ${s.reason}`);
    progress?.(`${Math.min(i + LINK_BATCH, resolved.length)} of ${resolved.length} relationships`);
  }
  return { ids, created, skipped, linked };
}

interface AssetRow {
  id: string;
  type: string;
  label: string;
  fields: Record<string, unknown>;
}
interface LinkRow {
  id: string;
  kind: string;
  from: { id: string; type: string; label: string };
  to: { id: string; type: string; label: string };
  fields: Record<string, unknown>;
  note: string;
}

async function allAssets(ctx: Context, project: string, type: string): Promise<AssetRow[]> {
  const out: AssetRow[] = [];
  for (let offset = 0; ; offset += 1000) {
    const page = await ctx.api.get<{ items: AssetRow[]; total: number }>(`/api/projects/${project}/assets`, { type, limit: 1000, offset });
    out.push(...page.items);
    if (out.length >= page.total || !page.items.length) return out;
  }
}

async function allLinks(ctx: Context, project: string): Promise<LinkRow[]> {
  const out: LinkRow[] = [];
  for (let offset = 0; ; offset += 5000) {
    const page = await ctx.api.get<{ items: LinkRow[]; total: number }>(`/api/projects/${project}/asset-links`,
      { kind: KINDS.join(","), limit: 5000, offset });
    out.push(...page.items);
    if (out.length >= page.total || !page.items.length) return out;
  }
}

const OURS = new Set(["repository", "source_file", "code_symbol"]);
const str = (v: unknown) => (v === null || v === undefined ? "" : String(v));
const list = (v: unknown) => (Array.isArray(v) ? v.map(String) : []);

definePlugin({
  activate(ctx) {
    ctx.actions.on("import", async (run) => {
      if (!run.file) throw new Error("Choose a repository archive: .zip, .tar.gz or .tar");
      if (!run.project) throw new Error("Run it on a project");
      const project = run.project.id;
      const skip = new Set(str(run.settings.skip ?? ctx.settings.skip).split(",").map((s) => s.trim().toLowerCase()).filter(Boolean));
      const maxFiles = Math.max(1, Number(run.settings.max_files ?? ctx.settings.max_files ?? 5000) || 5000);

      run.progress({ text: "Opening the archive" });
      const { entries, folder } = await readArchive(await run.file.bytes(), run.file.name);
      const repo = repoName(folder, run.file.name);
      const wanted = entries.filter((e) => !e.path.split("/").slice(0, -1).some((d) => skip.has(d.toLowerCase())) && languageOf(e.path));
      if (!wanted.length) throw new Error("No source files in that archive (or they're all in folders the plugin skips)");
      if (wanted.length > maxFiles) run.log.warn(`${wanted.length} files: mapping the first ${maxFiles} (the plugin's setting)`);
      const chosen = wanted.slice(0, maxFiles);

      const files: RepoFile[] = [];
      const hashes = new Map<string, string>();
      for (const [i, e] of chosen.entries()) {
        if (i % 200 === 0) run.progress({ done: i, total: chosen.length, text: `Reading ${e.path}` });
        const bytes = await e.read();
        hashes.set(e.path, await sha256(bytes));
        const head = bytes.subarray(0, 8000);
        const text = bytes.length <= TEXT_MAX && !head.includes(0) ? decoder.decode(bytes) : null;
        files.push({ path: e.path, text, size: bytes.length });
      }
      run.progress({ text: "Working out what imports what" });
      const mapped = mapRepo(files);

      const languages = new Map<string, number>();
      for (const f of mapped) languages.set(f.language, (languages.get(f.language) ?? 0) + 1);
      const byCount = [...languages].sort((a, b) => b[1] - a[1]);
      const assets: BulkAsset[] = [{ ref: "repo", type: "repository", fields: {
        name: repo, files: mapped.length, languages: byCount.slice(0, 8).map(([l]) => l) } }];
      const links: BulkLink[] = [];
      const paths = new Set(mapped.map((f) => f.path));
      let imports = 0, tests = 0;
      for (const f of mapped) {
        assets.push({ ref: `f:${f.path}`, type: "source_file", fields: {
          repo, path: f.path, language: f.language, lines: f.lines, roles: f.roles, dependencies: f.dependencies,
          sha256: hashes.get(f.path) } });
        links.push({ from: `f:${f.path}`, to: "repo", kind: "in_repo" });
        for (const imp of f.imports) {
          links.push({ from: `f:${f.path}`, to: `f:${imp.to}`, kind: "imports", fields: { line: imp.line } });
          imports++;
        }
        if (f.roles.includes("test")) {
          for (const s of subjectsOf(f.path, paths, f.imports.map((i) => i.to))) {
            links.push({ from: `f:${f.path}`, to: `f:${s}`, kind: "tests" });
            tests++;
          }
        }
      }
      const r = await bulkAll(ctx, project, assets, links, run.task?.id, (text) => run.progress({ text }));
      const count = (role: string) => mapped.filter((f) => f.roles.includes(role)).length;
      const deps = new Set(mapped.flatMap((f) => f.dependencies));
      const notable = [["entry point", "entry point"], ["routes", "file with routes", "files with routes"],
        ["auth", "file about sign-in or secrets", "files about sign-in or secrets"], ["sql", "file with SQL", "files with SQL"]]
        .map(([role, one, many]) => [count(role), one, many] as const).filter(([n]) => n)
        .map(([n, one, many]) => plural(n, one, many));
      return (`Mapped ${repo}: ${plural(mapped.length, "file")} (${byCount.slice(0, 4).map(([l, n]) => `${l} ${n}`).join(", ")}), ` +
        `${plural(imports, "import")} between them${tests ? ` and ${plural(tests, "test")} matched to what they test` : ""}. ` +
        (notable.length ? `${notable.join(", ")}. ` : "") +
        (deps.size ? `${plural(deps.size, "package")} from outside. ` : "") +
        (r.skipped ? `${r.skipped} couldn't be recorded (see the plugin's log).` : "")).trim();
    });

    // ---- the Code map tab ----

    ctx.ui.on("map", async (call) => {
      const project = call.project.id;
      const [repos, files, symbols, links] = await Promise.all([
        allAssets(ctx, project, "repository"), allAssets(ctx, project, "source_file"), allAssets(ctx, project, "code_symbol"),
        allLinks(ctx, project)]);
      return {
        can_write: call.user.can_write,
        review: REVIEW,
        symbol_kinds: SYMBOL_KINDS,
        repos: repos.map((a) => ({ id: a.id, name: str(a.fields.name), url: str(a.fields.url), branch: str(a.fields.branch),
          commit: str(a.fields.commit), languages: list(a.fields.languages) })),
        files: files.map((a) => ({ id: a.id, repo: str(a.fields.repo), path: str(a.fields.path), language: str(a.fields.language),
          lines: Number(a.fields.lines) || 0, roles: list(a.fields.roles), review: str(a.fields.review),
          dependencies: list(a.fields.dependencies) })),
        symbols: symbols.map((a) => ({ id: a.id, repo: str(a.fields.repo), file: str(a.fields.file), name: str(a.fields.name),
          kind: str(a.fields.kind), line: Number(a.fields.line) || null, review: str(a.fields.review) })),
        // Links between the plugin's assets, and from them to the rest (findings, URLs, hosts).
        links: links.filter((l) => OURS.has(l.from.type) || OURS.has(l.to.type))
          .map((l) => ({ id: l.id, kind: l.kind, from: l.from, to: l.to, fields: l.fields, note: l.note })),
      };
    }, { readOnly: true });

    ctx.ui.on<{ repo: string; paths: string[] }>("add_files", async (call) => {
      const repo = str(call.args.repo).trim().slice(0, 120);
      if (!repo) throw new Error("Which repository are they in?");
      const paths = [...new Set((call.args.paths ?? []).map(cleanPath).filter((p): p is string => !!p))];
      if (!paths.length) throw new Error("Give at least one path (one per line)");
      if (paths.length > 2000) throw new Error("At most 2000 paths at once");
      const assets: BulkAsset[] = [{ ref: "repo", type: "repository", fields: { name: repo } }];
      const links: BulkLink[] = [];
      for (const p of paths) {
        const language = languageOf(p) ?? "";
        assets.push({ ref: p, type: "source_file", fields: { repo, path: p, language, roles: rolesOf(p, language, null) } });
        links.push({ from: p, to: "repo", kind: "in_repo" });
      }
      const r = await bulkAll(ctx, call.project.id, assets, links);
      return { created: r.created, ids: paths.map((p) => r.ids[p]).filter(Boolean) };
    });

    ctx.ui.on<{ file: string; name: string; kind: string; line?: number; signature?: string }>("add_symbol", async (call) => {
      const file = await ctx.api.get<AssetRow>(`/api/assets/${call.args.file}`);
      if (file.type !== "source_file") throw new Error("Symbols go in a source file");
      const name = str(call.args.name).trim().slice(0, 200);
      if (!name) throw new Error("Give the symbol a name (a function, class, route…)");
      const kind = SYMBOL_KINDS.includes(call.args.kind) ? call.args.kind : "other";
      const line = Number(call.args.line) || undefined;
      const r = await bulkAll(ctx, call.project.id, [{ ref: "s", type: "code_symbol", fields: {
        repo: file.fields.repo, file: file.fields.path, name, kind, line, signature: str(call.args.signature).slice(0, 500) || undefined } }],
        [{ from: file.id, to: "s", kind: "defines", fields: line ? { line } : {} }]);
      if (!r.ids.s) throw new Error("Castor couldn't record it (see the plugin's log)");
      return { id: r.ids.s };
    });

    ctx.ui.on<{ from: string; to: string; kind: string; fields?: Record<string, unknown>; note?: string }>("link", async (call) => {
      const { from, to, kind } = call.args;
      if (!KINDS.includes(kind)) throw new Error(`Not a relationship this map draws: ${kind}`);
      const fields = Object.fromEntries(Object.entries(call.args.fields ?? {}).filter(([, v]) => v !== "" && v !== null && v !== undefined)
        .map(([k, v]) => [k, k === "line" ? Number(v) || undefined : str(v).slice(0, 300)]));
      const r = await ctx.assets.bulk(call.project.id, { assets: [], links: [{ from, to, kind, fields, note: str(call.args.note).slice(0, 2000) }] });
      if (r.skipped.length) throw new Error(r.skipped[0].reason);
      return { linked: r.links };
    });

    ctx.ui.on<{ id: string }>("unlink", async (call) => {
      await ctx.api.delete(`/api/asset-links/${call.args.id}`);
      return { ok: true };
    });

    ctx.ui.on<{ id: string; review: string }>("review", async (call) => {
      const review = REVIEW.includes(call.args.review) ? call.args.review : REVIEW[0];
      const a = await ctx.api.get<AssetRow>(`/api/assets/${call.args.id}`);
      if (a.type !== "source_file" && a.type !== "code_symbol") throw new Error("Only files and symbols have a review state");
      await ctx.api.patch(`/api/assets/${a.id}`, { fields: { review } });
      return { review };
    });
  },
});
