// deno test --allow-read=samples (no dependencies: the checks are below)
import { cleanPath, languageOf, mapRepo, type MappedFile, type RepoFile, rolesOf } from "./code.ts";
import { readArchive, readTar, repoName } from "./archive.ts";

function assert(ok: unknown, why = "expected true"): asserts ok {
  if (!ok) throw new Error(why);
}
function assertEquals(actual: unknown, expected: unknown, why = "") {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) throw new Error(`${why ? why + ": " : ""}${a} !== ${e}`);
}

async function sample(): Promise<RepoFile[]> {
  const root = new URL("./samples/shop/", import.meta.url);
  const out: RepoFile[] = [];
  async function walk(dir: URL, prefix: string) {
    for await (const e of Deno.readDir(dir)) {
      if (e.isDirectory) await walk(new URL(e.name + "/", dir), prefix + e.name + "/");
      else {
        const bytes = await Deno.readFile(new URL(e.name, dir));
        out.push({ path: prefix + e.name, size: bytes.length, text: bytes.includes(0) ? null : new TextDecoder().decode(bytes) });
      }
    }
  }
  await walk(root, "");
  return out.sort((a, b) => a.path.localeCompare(b.path));
}

const by = (files: MappedFile[], path: string) => files.find((f) => f.path === path)!;
const targets = (f: MappedFile) => f.imports.map((i) => i.to).sort();

Deno.test("maps the sample repository", async () => {
  const files = mapRepo(await sample());
  assert(!files.some((f) => f.path === "logo.png"), "pictures aren't source");
  const main = by(files, "app/main.py");
  assertEquals(main.language, "Python");
  assertEquals(targets(main), ["app/api/orders.py", "app/auth.py", "app/db/session.py"]);
  assertEquals(main.dependencies, ["flask"]);
  assertEquals(main.roles, ["entry point"]);
  const orders = by(files, "app/api/orders.py");
  assertEquals(targets(orders), ["app/db/session.py"]);
  assertEquals(orders.roles, ["routes", "sql"]);
  assertEquals(orders.imports.find((i) => i.to === "app/db/session.py")!.line, 3);
  assertEquals(by(files, "app/auth.py").roles, ["auth"]);
  assertEquals(by(files, "app/auth.py").dependencies, ["jwt"]);
  assertEquals(by(files, "tests/test_orders.py").roles, ["test"]);
  assertEquals(targets(by(files, "tests/test_orders.py")), ["app/api/orders.py"]);

  assertEquals(targets(by(files, "web/src/main.ts")), ["web/src/App.svelte", "web/src/lib/api.ts"], "$lib and .svelte");
  assertEquals(by(files, "web/src/main.ts").dependencies, ["svelte"]);
  assertEquals(targets(by(files, "web/src/App.svelte")), ["web/src/lib/format.ts"], "./x.js is x.ts");
  assertEquals(targets(by(files, "web/src/lib/api.ts")), ["web/src/lib/format.ts"], "export from");

  const server = by(files, "cmd/server/main.go");
  assertEquals(targets(server), ["internal/store/store.go"], "go.mod's module, test files left out");
  assertEquals(server.dependencies, ["github.com/go-chi/chi"]);
  assertEquals(server.roles, ["entry point", "routes"]);
  assertEquals(by(files, "go.mod").roles, ["config"]);
  assertEquals(by(files, "Dockerfile").roles, ["config"]);
  assertEquals(by(files, "requirements.txt").language, "Requirements");
});

const one = (files: Record<string, string>) =>
  mapRepo(Object.entries(files).map(([path, text]) => ({ path, text, size: text.length })));

Deno.test("the other languages", () => {
  const java = one({
    "src/main/java/com/shop/App.java": "package com.shop;\nimport com.shop.db.Repo;\nimport org.springframework.boot.SpringApplication;\nimport java.util.List;\npublic class App { public static void main(String[] a) {} }\n",
    "src/main/java/com/shop/db/Repo.java": "package com.shop.db;\nimport static com.shop.App.helper;\n",
  });
  assertEquals(targets(by(java, "src/main/java/com/shop/App.java")), ["src/main/java/com/shop/db/Repo.java"]);
  assertEquals(by(java, "src/main/java/com/shop/App.java").dependencies, ["org.springframework"]);
  assertEquals(targets(by(java, "src/main/java/com/shop/db/Repo.java")), ["src/main/java/com/shop/App.java"], "static import");

  const c = one({ "src/main.c": '#include <stdio.h>\n#include "util.h"\n#include "net/sock.h"\nint main() {}\n', "src/util.h": "", "include/net/sock.h": "" });
  assertEquals(targets(by(c, "src/main.c")), ["include/net/sock.h", "src/util.h"]);

  const cs = one({ "Api/Program.cs": "using Shop.Data;\nusing System.Text;\nusing Newtonsoft.Json;\n", "Data/Db.cs": "namespace Shop.Data;\nclass Db {}\n" });
  assertEquals(targets(by(cs, "Api/Program.cs")), ["Data/Db.cs"]);
  assertEquals(by(cs, "Api/Program.cs").dependencies, ["Newtonsoft.Json"]);

  const php = one({ "public/index.php": "<?php\nrequire_once __DIR__ . '/../src/boot.php';\nuse App\\Http\\Kernel;\nuse Symfony\\Component\\X;\n",
    "src/boot.php": "<?php\n", "src/Http/Kernel.php": "<?php\nRoute::get('/x', fn() => 1);\n" });
  assertEquals(targets(by(php, "public/index.php")), ["src/Http/Kernel.php", "src/boot.php"]);
  assertEquals(by(php, "src/Http/Kernel.php").roles, ["routes"]);

  const rb = one({ "lib/shop.rb": "require 'json'\nrequire 'rails'\nrequire_relative 'shop/order'\nrequire 'shop/cart'\n", "lib/shop/order.rb": "", "lib/shop/cart.rb": "" });
  assertEquals(targets(by(rb, "lib/shop.rb")), ["lib/shop/cart.rb", "lib/shop/order.rb"]);

  const rs = one({ "src/main.rs": "mod db;\nuse crate::db::conn;\nuse serde::Deserialize;\nuse std::io;\nfn main() {}\n", "src/db/mod.rs": "pub mod conn;\n", "src/db/conn.rs": "" });
  assertEquals(targets(by(rs, "src/main.rs")), ["src/db/conn.rs", "src/db/mod.rs"]);
  assertEquals(by(rs, "src/main.rs").dependencies, ["serde"]);
  assertEquals(targets(by(rs, "src/db/mod.rs")), ["src/db/conn.rs"]);

  const js = one({ "a.js": "const x = require('./b');\nimport('./c.mjs');\nimport fs from 'node:fs';\nimport { y } from '@scope/pkg/deep';\n", "b/index.js": "", "c.mjs": "" });
  assertEquals(targets(by(js, "a.js")), ["b/index.js", "c.mjs"]);
  assertEquals(by(js, "a.js").dependencies, ["@scope/pkg"]);
});

Deno.test("nothing climbs out of the repository", () => {
  const f = one({ "a/x.py": "from ... import secret\nfrom .... import y\n", "a/y.ts": "import '../../../etc/passwd';\n" });
  assertEquals(targets(by(f, "a/x.py")), []);
  assertEquals(targets(by(f, "a/y.ts")), []);
  assertEquals(cleanPath("../etc/passwd"), null);
  assertEquals(cleanPath(".\\src\\App.cs"), "src/App.cs");
  assertEquals(cleanPath("/srv/app/main.py"), "srv/app/main.py");
});

Deno.test("languages and roles", () => {
  assertEquals(languageOf("package-lock.json"), null);
  assertEquals(languageOf("dist/app.min.js"), null);
  assertEquals(languageOf("README.md"), null);
  assertEquals(languageOf(".env.production"), "Env");
  assertEquals(rolesOf("src/auth/login.ts", "TypeScript", null), ["auth"]);
  assertEquals(rolesOf("src/__tests__/auth.ts", "TypeScript", null), ["test"]);
  assertEquals(rolesOf("vite.config.ts", "TypeScript", "export default {}"), ["config"]);
});

function tar(files: Record<string, string>): Uint8Array {
  const enc = new TextEncoder();
  const blocks: Uint8Array[] = [];
  for (const [name, text] of Object.entries(files)) {
    const h = new Uint8Array(512);
    h.set(enc.encode(name), 0);
    h.set(enc.encode(text.length.toString(8).padStart(11, "0")), 124);
    h[156] = 48;
    h.set(enc.encode("ustar\x0000"), 257);
    blocks.push(h);
    const body = new Uint8Array(Math.ceil(text.length / 512) * 512);
    body.set(enc.encode(text));
    blocks.push(body);
  }
  blocks.push(new Uint8Array(1024));
  const out = new Uint8Array(blocks.reduce((n, b) => n + b.length, 0));
  let at = 0;
  for (const b of blocks) (out.set(b, at), at += b.length);
  return out;
}

Deno.test("reads tar and tar.gz, without the wrapping folder", async () => {
  const raw = tar({ "shop-1.2.0/app.py": "import os\n", "shop-1.2.0/lib/x.py": "" });
  assertEquals(readTar(raw).map((e) => e.path), ["shop-1.2.0/app.py", "shop-1.2.0/lib/x.py"]);
  const gz = new Uint8Array(await new Response(new Blob([raw as Uint8Array<ArrayBuffer>]).stream().pipeThrough(new CompressionStream("gzip"))).arrayBuffer());
  const { entries, folder } = await readArchive(gz, "shop.tar.gz");
  assertEquals(entries.map((e) => e.path), ["app.py", "lib/x.py"]);
  assertEquals(new TextDecoder().decode(await entries[0].read()), "import os\n");
  assertEquals(repoName(folder, "x.tar.gz"), "shop");
  assertEquals(repoName("castor-main", "castor-main.zip"), "castor");
  assertEquals(repoName(null, "api-3f9c2ab.zip"), "api");
});
