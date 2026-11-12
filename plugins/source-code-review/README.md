# Source Code Review

A Castor plugin (sandboxed, no network) that maps a code base for review. It adds three kinds of
asset and the relationships between them, so code sits in a project next to the hosts, URLs and
findings it belongs with:

| Asset type | Identified by | Fields |
| --- | --- | --- |
| **Repository** (`repository`) | name | URL, branch, commit, languages, files |
| **Source file** (`source_file`) | repository + path | language, lines, roles, review, packages it uses, SHA-256 |
| **Symbol** (`code_symbol`) | repository + file + name | kind (function, class, route, query…), line, signature, review |

Relationships: *in repository*, *imports*, *defines*, *calls*, *passes data to* (data flow, with what
passes), *tests*, *serves* (a symbol or file → a URL) and *deployed to* (a repository → a host, URL or
service). Castor's own *affects* also reaches files, symbols and repositories, so a finding can
point at the code it's in.

## Import source code

On a project's Assets page or a card: a repository as a `.zip`, `.tar.gz` or `.tar` (GitHub's
Download ZIP, `git archive`). The plugin reads it itself, lists the files Castor should know (code
and configuration, not pictures, docs or lock files), and works out what imports what:

- JavaScript, TypeScript, Svelte, Vue: `import`/`export … from`, `import()`, `require()`, relative
  paths with or without extensions, `index` files, `./x.js` meaning `x.ts`, `$lib/`, `@/`, `~/`
- Python: `import` and `from … import`, relative and absolute, packages found by their `__init__.py`
- Go: packages of the module in `go.mod` (each non-test file of the package)
- Java, Kotlin, Scala: imports by package path, wildcards, static imports
- C, C++: `#include "…"`
- C#: `using` namespaces, matched to the files that declare them
- PHP: `require`/`include` and `use` (PSR-4 style paths)
- Ruby: `require_relative`, `require` from `lib/`
- Rust: `mod x;` and `use crate::…`

What it can't find in the repository becomes the file's *packages it uses* (`flask`, `@scope/pkg`,
`github.com/go-chi/chi`), leaving out each language's standard library where it can tell. Test files
are linked to what they test when the names say so (`test_db.py` → `db.py`, `x.test.ts` → `x.ts`).
Files get roles to start a review from: *entry point*, *routes*, *auth* (sign-in, sessions,
secrets, crypto by path), *sql*, *test* and *config*. It's regular expressions, not compilers: it
finds the usual ways of importing and leaves out what it can't be sure of.

Importing again adds what's new and updates what changed; review states stay.
Settings: folders to leave out (`node_modules`, `vendor`, `dist`… by default) and the most files per
import (5000).

## The Code map

A tab on the Assets page: each repository as a folder tree, filtered by path, role and review
state. Choosing a file shows what it imports, what imports it, its symbols, data flow, tests and
the findings that affect it, with buttons to mark its review (not started, in progress, reviewed,
flagged), add a relationship, or add a symbol. **Add files** takes a list of paths. Everything is
an ordinary asset too: open one to describe it, link it to anything, or use it in a trigger.

## From an AI assistant (MCP)

The types are Castor's like any other, so Castor's MCP tools work with them: `add_asset`
(`{"type": "source_file", "fields": {"repo": "shop", "path": "app/db.py"}}`), `link_assets` (kind
`imports`, `calls`, `flows_to`…), `bulk_assets` for many at once, `list_asset_types` and
`list_link_kinds` for the fields and kinds, and `run_plugin_action` to import an archive.

## Working on it

```bash
deno task test    # code_test.ts, on samples/shop (a small app in Python, TypeScript, Svelte and Go)
python3 ../pack.py .
```
