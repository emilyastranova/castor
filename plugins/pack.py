#!/usr/bin/env python3
"""Pack a plugin folder into the .zip Castor installs (Settings → Plugins → Install plugin).

    python3 plugins/pack.py plugins/nessus-import            # → plugins/dist/nessus-import-1.0.0.zip

Leaves out what only matters while working on it: tests (*_test.ts), samples/, deno.json, dist/.
Standard library only."""

import re
import sys
import zipfile
from pathlib import Path

SKIP_DIRS = {"samples", "dist", "node_modules", ".git"}


def pack(folder: Path) -> Path:
    manifest = (folder / "castor-plugin.yaml").read_text()
    pid = re.search(r"^id:\s*(\S+)", manifest, re.M)
    version = re.search(r"^version:\s*(\S+)", manifest, re.M)
    if not pid or not version:
        sys.exit(f"{folder}/castor-plugin.yaml needs an id and a version")
    out = folder.parent / "dist" / f"{pid[1]}-{version[1]}.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            rel = path.relative_to(folder)
            if not path.is_file() or path.is_symlink() or rel.parts[0] in SKIP_DIRS:
                continue
            if path.name.endswith("_test.ts") or path.name in ("deno.json", "deno.lock"):
                continue
            zf.write(path, rel.as_posix())
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(pack(Path(sys.argv[1]).resolve()))
