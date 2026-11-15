"""Castor's version: the installed package's (server/pyproject.toml, set by scripts/release.sh)."""

from importlib.metadata import PackageNotFoundError, version

try:
    VERSION = version("castor-server")
except PackageNotFoundError:  # running from a checkout that wasn't installed
    VERSION = "0.0.0"
