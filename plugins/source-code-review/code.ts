/** What a repository is made of: each file's language, what it imports (resolved to other files of
 *  the repository where it can be, as packages where it can't) and the roles worth a reviewer's
 *  attention. Pure functions over paths and text, so they're tested without Castor. Regular
 *  expressions, not parsers: they find the usual ways each language imports, and say nothing
 *  rather than guess. */

export interface RepoFile {
  path: string;
  /** Its text, or null when it's binary or too big to read. */
  text: string | null;
  size: number;
}

export interface Import {
  to: string;
  line: number;
}

export interface MappedFile {
  path: string;
  language: string;
  lines: number;
  roles: string[];
  imports: Import[];
  dependencies: string[];
}

const LANGUAGES: Record<string, string> = {
  js: "JavaScript", mjs: "JavaScript", cjs: "JavaScript", jsx: "JavaScript",
  ts: "TypeScript", tsx: "TypeScript", mts: "TypeScript", cts: "TypeScript",
  svelte: "Svelte", vue: "Vue", py: "Python", go: "Go", java: "Java", kt: "Kotlin", kts: "Kotlin", scala: "Scala",
  c: "C", h: "C", cc: "C++", cpp: "C++", cxx: "C++", hpp: "C++", hh: "C++", hxx: "C++", cs: "C#", php: "PHP",
  rb: "Ruby", rs: "Rust", swift: "Swift", sh: "Shell", bash: "Shell", ps1: "PowerShell", sql: "SQL",
  html: "HTML", htm: "HTML", css: "CSS", scss: "CSS", yml: "YAML", yaml: "YAML", json: "JSON", toml: "TOML",
  xml: "XML", ini: "INI", tf: "Terraform", gradle: "Gradle", properties: "Properties",
};
const NAMED: Record<string, string> = {
  dockerfile: "Dockerfile", makefile: "Makefile", gemfile: "Ruby", rakefile: "Ruby", jenkinsfile: "Groovy", ".env": "Env",
  "go.mod": "Go module", "requirements.txt": "Requirements",
};
const CONFIG_LANGUAGES = new Set(["Go module", "Requirements", "YAML", "JSON", "TOML", "XML", "INI", "Terraform", "Dockerfile", "Makefile", "Gradle", "Properties", "Env"]);

export const basename = (p: string) => p.slice(p.lastIndexOf("/") + 1);
export const dirname = (p: string) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "");
const extOf = (p: string) => {
  const b = basename(p);
  return b.includes(".") ? b.slice(b.lastIndexOf(".") + 1).toLowerCase() : "";
};

/** The file's language, or null for what a code review doesn't map (pictures, docs, lock files). */
export function languageOf(path: string): string | null {
  const b = basename(path).toLowerCase();
  if (NAMED[b]) return NAMED[b];
  if (b.startsWith(".env")) return "Env";
  if (b.startsWith("dockerfile")) return "Dockerfile";
  if (/(^|[.-])lock(\.json)?$|\.min\.(js|css)$|\.map$/.test(b)) return null;
  return LANGUAGES[extOf(path)] ?? null;
}

/** A path as the repository writes it: forward slashes, nothing above it. */
export function cleanPath(p: string): string | null {
  const parts = String(p).trim().replace(/\\/g, "/").replace(/^[a-z]:\//i, "").split("/").filter((s) => s && s !== ".");
  if (!parts.length || parts.includes("..") || parts.join("/").length > 380) return null;
  return parts.join("/");
}

/** Joins and normalises a path ("a/b/../c" → "a/c"); null when it climbs out of the repository. */
export function join(...parts: string[]): string | null {
  const out: string[] = [];
  for (const seg of parts.join("/").split("/")) {
    if (!seg || seg === ".") continue;
    if (seg === "..") {
      if (!out.length) return null;
      out.pop();
    } else out.push(seg);
  }
  return out.join("/");
}

const lineAt = (text: string, index: number) => {
  let n = 1;
  for (let i = 0; i < index; i++) if (text.charCodeAt(i) === 10) n++;
  return n;
};

/** Every match of `re` (global) as [specifier, line]. */
function matches(text: string, re: RegExp, group = 1): [string, number][] {
  const out: [string, number][] = [];
  // Lines are counted incrementally: one pass, not one per match.
  let line = 1, at = 0;
  for (const m of text.matchAll(re)) {
    const spec = m[group];
    if (!spec) continue;
    for (; at < m.index!; at++) if (text.charCodeAt(at) === 10) line++;
    out.push([spec, line]);
  }
  return out;
}

const PY_STDLIB = new Set(("__future__ abc argparse array ast asyncio base64 binascii bisect builtins bz2 calendar collections " +
  "concurrent configparser contextlib copy csv ctypes dataclasses datetime decimal difflib email enum errno fnmatch fractions " +
  "functools gc getpass glob gzip hashlib heapq hmac html http importlib inspect io ipaddress itertools json keyword logging " +
  "lzma math mimetypes multiprocessing operator os pathlib pickle platform pprint queue random re secrets select shlex shutil " +
  "signal socket sqlite3 ssl stat statistics string struct subprocess sys tarfile tempfile textwrap threading time timeit " +
  "tkinter traceback types typing unicodedata unittest urllib uuid warnings weakref xml zipfile zlib zoneinfo").split(" "));
const RUST_LOCAL = new Set(["crate", "self", "super", "std", "core", "alloc"]);

/** Knows every path in the repository, and turns what a file imports into paths. */
export class Resolver {
  readonly paths: Set<string>;
  private byName = new Map<string, string[]>();
  private pyModules = new Map<string, string[]>();
  private goModules: [string, string][] = []; // [module path, its folder]
  private csNamespaces = new Map<string, string[]>();
  private dirs = new Map<string, string[]>();

  constructor(files: RepoFile[]) {
    this.paths = new Set(files.map((f) => f.path));
    for (const f of files) {
      const b = basename(f.path);
      (this.byName.get(b) ?? this.byName.set(b, []).get(b)!).push(f.path);
      const d = dirname(f.path);
      (this.dirs.get(d) ?? this.dirs.set(d, []).get(d)!).push(f.path);
    }
    for (const f of files) {
      if (f.path.endsWith(".py")) {
        const name = this.pyName(f.path);
        (this.pyModules.get(name) ?? this.pyModules.set(name, []).get(name)!).push(f.path);
      } else if (basename(f.path) === "go.mod" && f.text) {
        const mod = /^\s*module\s+(\S+)/m.exec(f.text)?.[1];
        if (mod) this.goModules.push([mod, dirname(f.path)]);
      } else if (f.path.endsWith(".cs") && f.text) {
        for (const [ns] of matches(f.text, /^\s*namespace\s+([\w.]+)/gm)) {
          (this.csNamespaces.get(ns) ?? this.csNamespaces.set(ns, []).get(ns)!).push(f.path);
        }
      }
    }
    this.goModules.sort((a, b) => b[0].length - a[0].length);
  }

  private has(p: string | null): p is string {
    return p !== null && this.paths.has(p);
  }

  /** A Python file's module name: up through the folders that are packages (have __init__.py). */
  private pyName(path: string): string {
    const stem = basename(path).replace(/\.py$/, "");
    const parts = stem === "__init__" ? [] : [stem];
    let dir = dirname(path);
    while (dir && this.paths.has(`${dir}/__init__.py`)) {
      parts.unshift(basename(dir));
      dir = dirname(dir);
    }
    return parts.join(".");
  }

  /** The files whose path ends with `suffix` (a/b/C.java), shortest first; only whole names match. */
  private endingWith(suffix: string): string[] {
    const hits = (this.byName.get(basename(suffix)) ?? []).filter((p) => p === suffix || p.endsWith("/" + suffix));
    return hits.sort((a, b) => a.length - b.length);
  }

  private inDir(dir: string, test: (p: string) => boolean, max = 30): string[] {
    return (this.dirs.get(dir) ?? []).filter(test).slice(0, max);
  }

  private firstOf(candidates: (string | null)[]): string[] {
    const hit = candidates.find((c) => this.has(c));
    return hit ? [hit] : [];
  }

  /** What `spec`, imported by `from`, is: files of the repository, or a package (`dependency`). */
  resolve(from: string, language: string, spec: string): { files: string[]; dependency?: string } {
    const dir = dirname(from);
    switch (language) {
      case "JavaScript": case "TypeScript": case "Svelte": case "Vue": {
        let base: string | null = null;
        if (spec.startsWith(".")) base = join(dir, spec);
        else if (spec.startsWith("/")) base = join(spec);
        else if (spec.startsWith("$lib/")) base = this.aliasRoot("src/lib", from) + spec.slice(4);
        else if (/^[@~]\//.test(spec)) base = this.aliasRoot("src", from) + spec.slice(1);
        if (base === null) {
          const dep = spec.startsWith("@") ? spec.split("/").slice(0, 2).join("/") : spec.split("/")[0];
          return { files: [], dependency: dep.startsWith("node:") ? undefined : dep };
        }
        const b = join(base);
        if (b === null) return { files: [] };
        const exts = ["ts", "tsx", "js", "jsx", "mjs", "cjs", "mts", "svelte", "vue", "json"];
        const noJs = b.replace(/\.(m|c)?js$/, "");
        return { files: this.firstOf([b, ...exts.map((e) => `${b}.${e}`), ...exts.map((e) => `${noJs}.${e}`),
          ...exts.map((e) => `${b}/index.${e}`)]) };
      }
      case "Python": {
        // `from pkg import name`: pkg.name if that's a module, otherwise pkg itself.
        const from = /^from:([^:]*):(\w+)$/.exec(spec);
        if (from) {
          const sub = this.python(dir, `${from[1]}${from[1].endsWith(".") ? "" : "."}${from[2]}`);
          return sub.files.length ? sub : this.python(dir, from[1]);
        }
        return this.python(dir, spec);
      }
      case "Go": {
        for (const [mod, root] of this.goModules) {
          if (spec === mod || spec.startsWith(mod + "/")) {
            const d = join(root, spec.slice(mod.length)) ?? "";
            return { files: this.inDir(d, (p) => p.endsWith(".go") && !p.endsWith("_test.go")) };
          }
        }
        return { files: [], dependency: spec.split("/")[0].includes(".") ? spec.split("/").slice(0, 3).join("/") : undefined };
      }
      case "Java": case "Kotlin": case "Scala": {
        const s = spec.replace(/^static\s+/, "");
        const parts = s.split(".");
        if (parts.at(-1) === "*") {
          const folder = parts.slice(0, -1).join("/");
          const any = this.endingWithDir(folder);
          if (any !== null) return { files: this.inDir(any, (p) => /\.(java|kt|scala)$/.test(p)) };
        } else {
          // a.b.C, or a.b.C.member (a static import): the longest that's a file.
          for (let n = parts.length; n >= 2; n--) {
            const base = parts.slice(0, n).join("/");
            for (const ext of ["java", "kt", "scala"]) {
              const hits = this.endingWith(`${base}.${ext}`);
              if (hits.length) return { files: [hits[0]] };
            }
          }
        }
        if (/^(java|javax|kotlin|scala|jdk|sun)\./.test(s)) return { files: [] };
        return { files: [], dependency: parts.slice(0, Math.min(2, parts.length - 1)).join(".") };
      }
      case "C": case "C++": {
        const near = join(dir, spec);
        if (this.has(near)) return { files: [near] };
        const hits = this.endingWith(spec);
        return { files: hits.slice(0, 1) };
      }
      case "C#": {
        const hits = this.csNamespaces.get(spec) ?? [];
        if (hits.length) return { files: hits.filter((p) => p !== from).slice(0, 30) };
        return { files: [], dependency: /^(System|Microsoft)(\.|$)/.test(spec) ? undefined : spec.split(".").slice(0, 2).join(".") };
      }
      case "PHP": {
        if (spec.includes("\\")) { // use A\B\C
          const parts = spec.replace(/^\\/, "").split("\\");
          for (let i = 0; i < parts.length - 1; i++) {
            const hits = this.endingWith(parts.slice(i).join("/") + ".php");
            if (hits.length) return { files: [hits[0]] };
          }
          return { files: [], dependency: parts.slice(0, 2).join("\\") };
        }
        return { files: this.firstOf([join(dir, spec), join(spec)]) };
      }
      case "Ruby": {
        if (spec.startsWith("relative:")) {
          const p = join(dir, spec.slice(9));
          return { files: p === null ? [] : this.firstOf([p.endsWith(".rb") ? p : `${p}.rb`]) };
        }
        const hits = this.endingWith(`${spec}.rb`).filter((p) => p.startsWith("lib/") || p.includes("/lib/") || !p.includes("/"));
        return hits.length ? { files: [hits[0]] } : { files: [], dependency: spec.split("/")[0] };
      }
      case "Rust": {
        if (spec.startsWith("mod:")) {
          const name = spec.slice(4);
          const b = basename(from);
          const here = ["main.rs", "lib.rs", "mod.rs"].includes(b) ? dir : join(dir, b.replace(/\.rs$/, ""));
          return { files: here === null ? [] : this.firstOf([join(here, `${name}.rs`), join(here, name, "mod.rs")]) };
        }
        const parts = spec.split("::");
        if (parts[0] === "crate") {
          const src = this.crateRoot(from);
          for (let n = parts.length - 1; n >= 1; n--) {
            const p = join(src, ...parts.slice(1, n + 1));
            const hit = this.firstOf([p && `${p}.rs`, p && join(p, "mod.rs")]);
            if (hit.length) return { files: hit };
          }
          return { files: [] };
        }
        return { files: [], dependency: RUST_LOCAL.has(parts[0]) ? undefined : parts[0] };
      }
    }
    return { files: [] };
  }

  private python(dir: string, spec: string): { files: string[]; dependency?: string } {
    const dots = /^\.*/.exec(spec)![0].length;
    const rest = spec.slice(dots);
    if (dots) {
      let base: string | null = dir;
      for (let i = 1; i < dots && base !== null; i++) base = base ? dirname(base) : null;
      if (base === null) return { files: [] };
      const p = rest ? join(base, rest.replaceAll(".", "/")) : base;
      return { files: p === null ? [] : this.firstOf([`${p}.py`, join(p, "__init__.py")]) };
    }
    // A script next to it (python runs with its folder on the path), then the packages.
    const local = this.firstOf([join(dir, rest.replaceAll(".", "/") + ".py")]);
    if (local.length) return { files: local };
    const hits = this.pyModules.get(rest);
    if (hits?.length) return { files: [[...hits].sort((a, b) => a.length - b.length)[0]] };
    if (rest.includes(".")) return this.python(dir, rest.slice(0, rest.lastIndexOf(".")));
    return { files: [], dependency: PY_STDLIB.has(rest) ? undefined : rest };
  }

  /** The folder a dotted package names (a/b), wherever it sits; null if no file is in it. */
  private endingWithDir(folder: string): string | null {
    for (const d of this.dirs.keys()) if (d === folder || d.endsWith("/" + folder)) return d;
    return null;
  }

  /** Where `src` (or `src/lib`) is for this file: the nearest one above it, for monorepos. */
  private aliasRoot(root: string, from: string): string {
    const parts = from.split("/");
    for (let i = parts.length - 1; i >= 0; i--) {
      const base = parts.slice(0, i).join("/");
      const candidate = base ? `${base}/${root}` : root;
      if (this.dirs.has(candidate) || [...this.dirs.keys()].some((d) => d.startsWith(candidate + "/"))) return candidate;
    }
    return root;
  }

  private crateRoot(from: string): string {
    const i = from.lastIndexOf("/src/");
    return i >= 0 ? from.slice(0, i + 4) : from.startsWith("src/") ? "src" : dirname(from);
  }
}

/** What a file says it imports, as each language writes it (unresolved). */
export function importsOf(language: string, text: string): [string, number][] {
  switch (language) {
    case "JavaScript": case "TypeScript": case "Svelte": case "Vue":
      return [
        ...matches(text, /(?:^|[;\s])(?:import|export)\s+(?:type\s+)?[\w*${}\s,]*?\s*from\s*['"]([^'"\n]+)['"]/g),
        ...matches(text, /(?:^|[;\s])import\s*['"]([^'"\n]+)['"]/g),
        ...matches(text, /\b(?:require|import)\s*\(\s*['"]([^'"\n]+)['"]\s*\)/g),
      ];
    case "Python": {
      const out: [string, number][] = [];
      for (const [names, line] of matches(text, /^[ \t]*import[ \t]+([\w., \t]+)/gm)) {
        for (const n of names.split(",")) {
          const mod = n.trim().split(/\s+as\s+/)[0].trim();
          if (mod) out.push([mod, line]);
        }
      }
      for (const m of text.matchAll(/^[ \t]*from[ \t]+(\.*[\w.]*)[ \t]+import[ \t]+(?:\(([^)]*)\)|([^\n#;]+))/gm)) {
        const line = lineAt(text, m.index!);
        const names = (m[2] ?? m[3]).split(",").map((n) => n.trim().split(/\s+as\s+/)[0].trim()).filter((n) => /^\w+$/.test(n));
        for (const n of names.length ? names : ["*"]) out.push([n === "*" ? m[1] : `from:${m[1]}:${n}`, line]);
      }
      return out;
    }
    case "Go": {
      const out: [string, number][] = matches(text, /^\s*import\s+(?:[\w.]+\s+)?"([^"]+)"/gm);
      for (const m of text.matchAll(/^\s*import\s*\(([\s\S]*?)\)/gm)) {
        const start = lineAt(text, m.index!);
        m[1].split("\n").forEach((l, i) => {
          const spec = /"([^"]+)"/.exec(l)?.[1];
          if (spec) out.push([spec, start + i]);
        });
      }
      return out;
    }
    case "Java": case "Kotlin": case "Scala":
      return matches(text, /^\s*import\s+((?:static\s+)?[\w.]+(?:\.\*)?)\s*;?/gm);
    case "C": case "C++":
      return matches(text, /^\s*#\s*include\s*"([^"\n]+)"/gm);
    case "C#":
      return matches(text, /^\s*(?:global\s+)?using\s+(?:static\s+)?([A-Z][\w.]*)\s*;/gm);
    case "PHP":
      return [
        ...matches(text, /\b(?:require|include)(?:_once)?\s*\(?\s*(?:__DIR__\s*\.\s*)?['"]\/?([^'"\n]+\.php)['"]/g),
        ...matches(text, /^\s*use\s+([\w\\]+)(?:\s+as\s+\w+)?\s*;/gm),
      ];
    case "Ruby":
      return [
        ...matches(text, /^\s*require_relative\s*\(?\s*['"]([^'"\n]+)['"]/gm).map(([s, l]): [string, number] => [`relative:${s}`, l]),
        ...matches(text, /^\s*require\s*\(?\s*['"]([^'"\n]+)['"]/gm),
      ];
    case "Rust":
      return [
        ...matches(text, /^\s*(?:pub(?:\([\w ]+\))?\s+)?mod\s+(\w+)\s*;/gm).map(([s, l]): [string, number] => [`mod:${s}`, l]),
        ...matches(text, /^\s*(?:pub\s+)?use\s+((?:crate|[a-z_]\w*)(?:::\w+)*)/gm),
        ...matches(text, /^\s*extern\s+crate\s+(\w+)/gm),
      ];
  }
  return [];
}

const ENTRY_NAMES = new Set(["main.py", "__main__.py", "app.py", "manage.py", "wsgi.py", "asgi.py", "main.go", "main.rs",
  "server.js", "server.ts", "app.js", "app.ts", "main.ts", "main.js", "program.cs", "index.php", "main.c", "main.cpp", "application.java"]);
const CONFIG_NAMES = /^(package\.json|tsconfig(\..+)?\.json|requirements.*\.txt|pyproject\.toml|setup\.(py|cfg)|go\.mod|pom\.xml|build\.gradle(\.kts)?|cargo\.toml|composer\.json|gemfile|settings\.py|config\.\w+|.*\.config\.(js|ts|mjs|cjs)|web\.config|appsettings.*\.json|\.env.*|docker-compose.*\.ya?ml|compose\.ya?ml|vite\.config\.\w+)$/i;
const ROUTES = /@(?:app|router|bp|blueprint|api)\.(?:route|get|post|put|patch|delete)\s*\(|\b(?:app|router)\.(?:get|post|put|patch|delete|all|use)\s*\(\s*['"`]\/|@(?:Get|Post|Put|Patch|Delete|Request)Mapping\b|\bRoute::(?:get|post|put|patch|delete|any)\s*\(|\bhttp\.HandleFunc\s*\(|\[Http(?:Get|Post|Put|Patch|Delete)\b|\bpath\s*\(\s*['"][^'"]*['"]\s*,\s*\w/;
const SQL = /\b(?:SELECT\s+[\s\S]{1,200}?\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b|\.(?:execute|executemany|raw|query)\s*\(/i;
const AUTH_PATH = /(?:^|[/_.-])(?:auth\w*|login|logout|password|passwd|jwt|oauth\w*|saml|sso|permissions?|acl|rbac|crypto\w*|secrets?)(?:[/_.-]|$)/i;

/** What a reviewer should know a file is for: entry point, routes, auth, sql, test, config. */
export function rolesOf(path: string, language: string, text: string | null): string[] {
  const roles = new Set<string>();
  const lower = path.toLowerCase();
  const b = basename(lower);
  const test = /(^|\/)(tests?|__tests__|spec|specs)\//.test(lower) || /\.(test|spec)\.\w+$/.test(b) || /^test_.*\.py$/.test(b) ||
    /_test\.(go|py)$/.test(b) || /(tests?|spec)\.(java|kt|cs|php|rb)$/i.test(basename(path)) || /_spec\.rb$/.test(b);
  if (test) roles.add("test");
  if (CONFIG_LANGUAGES.has(language) || CONFIG_NAMES.test(b)) roles.add("config");
  if (!test && text !== null) {
    if (ENTRY_NAMES.has(b) || (language === "Go" && /^package\s+main\b/m.test(text)) ||
      (language === "Python" && /^if\s+__name__\s*==\s*['"]__main__['"]/m.test(text)) ||
      (/^(Java|Kotlin|Scala)$/.test(language) && /\bstatic\s+void\s+main\s*\(|^fun\s+main\s*\(/m.test(text))) roles.add("entry point");
    if (!roles.has("config") && ROUTES.test(text)) roles.add("routes");
    if (!roles.has("config") && language !== "SQL" && SQL.test(text)) roles.add("sql");
  }
  if (AUTH_PATH.test(lower) && !test) roles.add("auth");
  return [...roles].sort();
}

/** Every file of the repository Castor should know, mapped. */
export function mapRepo(files: RepoFile[]): MappedFile[] {
  const r = new Resolver(files);
  const out: MappedFile[] = [];
  for (const f of files) {
    const language = languageOf(f.path);
    if (!language) continue;
    const imports = new Map<string, number>();
    const deps = new Set<string>();
    if (f.text !== null) {
      for (const [spec, line] of importsOf(language, f.text)) {
        const { files: to, dependency } = r.resolve(f.path, language, spec);
        for (const t of to) if (t !== f.path && !imports.has(t)) imports.set(t, line);
        if (!to.length && dependency) deps.add(dependency);
      }
    }
    // Python's `from pkg import name` tries pkg.name as a module, then pkg: keep only real packages.
    const dependencies = [...deps].filter((d) => d && !/^\.|^\d/.test(d)).sort().slice(0, 60);
    out.push({
      path: f.path, language, roles: rolesOf(f.path, language, f.text),
      lines: f.text === null ? 0 : f.text.length ? f.text.split("\n").length - (f.text.endsWith("\n") ? 1 : 0) : 0,
      imports: [...imports].map(([to, line]) => ({ to, line })).sort((a, b) => a.line - b.line),
      dependencies,
    });
  }
  return out;
}

/** The test file's subject, by name: tests/test_db.py → db.py, src/x.test.ts → src/x.ts, a_test.go → a.go. */
export function subjectsOf(test: string, paths: Set<string>, imports: string[]): string[] {
  const b = basename(test);
  const stem = b.replace(/^test_/, "").replace(/(_test|\.test|\.spec|_spec|Tests?)(?=\.\w+$)/, "");
  if (stem === b) return [];
  const sameDir = join(dirname(test), stem);
  if (sameDir && sameDir !== test && paths.has(sameDir)) return [sameDir];
  // Otherwise what it imports that has that name.
  return imports.filter((p) => basename(p) === stem).slice(0, 3);
}
