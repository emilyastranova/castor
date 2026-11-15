#!/bin/sh
# Make a release: set the version everywhere, commit and tag it. Pushing the tag starts the
# Release workflow (.github/workflows/release.yml): tests, images on Docker Hub, a GitHub Release.
#   scripts/release.sh 1.0.0          # then: git push origin HEAD v1.0.0
#   scripts/release.sh 1.0.1 --push   # push them too
set -eu
version=${1:-}
case "$version" in
  [0-9]*.[0-9]*.[0-9]*) ;;
  *) echo "usage: scripts/release.sh X.Y.Z [--push]   (a version like 1.0.0, 1.2.3 or 2.0.0-rc.1)" >&2; exit 2 ;;
esac
cd "$(dirname "$0")/.."
if [ -n "$(git status --porcelain)" ]; then
  echo "The working tree has uncommitted changes; commit or stash them first." >&2; exit 1
fi
if git rev-parse -q --verify "refs/tags/v$version" >/dev/null; then
  echo "Tag v$version already exists." >&2; exit 1
fi
python3 - "$version" <<'PY'
import json, re, sys
v = sys.argv[1]
for path in ("server/pyproject.toml", "agent/pyproject.toml"):
    text = open(path).read()
    new, n = re.subn(r'(?m)^version = "[^"]*"', f'version = "{v}"', text, count=1)
    assert n == 1, f"no version line in {path}"
    open(path, "w").write(new)
for path in ("web/package.json", "web/package-lock.json"):
    data = json.load(open(path))
    data["version"] = v
    if "packages" in data and "" in data["packages"]:
        data["packages"][""]["version"] = v
    open(path, "w").write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
PY
git add server/pyproject.toml agent/pyproject.toml web/package.json web/package-lock.json
git commit -q -m "Release $version"
git tag -a "v$version" -m "Castor $version"
echo "Tagged v$version."
if [ "${2:-}" = "--push" ]; then
  git push origin HEAD "v$version"
else
  echo "Push it to release:  git push origin HEAD v$version"
fi
