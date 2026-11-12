"""A theme's contrast check (the theme editor's warnings): every text colour role against the
surfaces it's written on, in each of the theme's modes, against WCAG's 4.5:1 for normal text.
Roles a theme leaves out are Castor's own (CASTOR, from web lib/m3/tokens.css; a test keeps them
in step). Colours it can't work out (color-mix(), oklch(), var() to something else) are skipped."""

from __future__ import annotations

import re

from .theme_spec import ThemeSpec

MINIMUM = 4.5
# Text role → the surfaces it's written on.
PAIRS: dict[str, tuple[str, ...]] = {
    "on-surface": ("surface", "surface-container-lowest", "surface-container-low", "surface-container",
                   "surface-container-high", "surface-container-highest"),
    "on-surface-variant": ("surface", "surface-container-low", "surface-container", "surface-container-high"),
    "on-background": ("background",),
    "primary": ("surface", "surface-container"),
    "error": ("surface",),
    "on-primary": ("primary",),
    "on-primary-container": ("primary-container",),
    "on-secondary": ("secondary",),
    "on-secondary-container": ("secondary-container",),
    "on-tertiary": ("tertiary",),
    "on-tertiary-container": ("tertiary-container",),
    "on-error": ("error",),
    "on-error-container": ("error-container",),
    "inverse-on-surface": ("inverse-surface",),
}
CASTOR: dict[str, dict[str, str]] = {
    "light": {
    "primary": "#545a92", "on-primary": "#ffffff", "primary-container": "#e0e0ff", "on-primary-container": "#3d4279",
    "secondary": "#5c5d72", "on-secondary": "#ffffff", "secondary-container": "#e1e0f9",
    "on-secondary-container": "#444559", "tertiary": "#78536b", "on-tertiary": "#ffffff",
    "tertiary-container": "#ffd7ef", "on-tertiary-container": "#5e3c53", "error": "#ba1a1a", "on-error": "#ffffff",
    "error-container": "#ffdad6", "on-error-container": "#93000a", "surface": "#fbf8ff", "on-surface": "#1b1b21",
    "surface-variant": "#e3e1ec", "on-surface-variant": "#46464f", "surface-dim": "#dbd9e0",
    "surface-bright": "#fbf8ff", "surface-container-lowest": "#ffffff", "surface-container-low": "#f5f2fa",
    "surface-container": "#efedf4", "surface-container-high": "#eae7ef", "surface-container-highest": "#e4e1e9",
    "inverse-surface": "#303036", "inverse-on-surface": "#f2eff7", "inverse-primary": "#bdc2ff",
    "outline": "#777680", "outline-variant": "#c7c5d0", "shadow": "#000000", "scrim": "#000000",
    "surface-tint": "#545a92", "primary-fixed": "#e0e0ff", "primary-fixed-dim": "#bdc2ff",
    "on-primary-fixed": "#0f144b", "on-primary-fixed-variant": "#3d4279", "secondary-fixed": "#e1e0f9",
    "secondary-fixed-dim": "#c4c4dd", "on-secondary-fixed": "#191a2c", "on-secondary-fixed-variant": "#444559",
    "tertiary-fixed": "#ffd7ef", "tertiary-fixed-dim": "#e7b9d5", "on-tertiary-fixed": "#2e1126",
    "on-tertiary-fixed-variant": "#5e3c53", "background": "#fbf8ff", "on-background": "#1b1b21",
    },
    "dark": {
    "primary": "#bdc2ff", "on-primary": "#262b61", "primary-container": "#3d4279", "on-primary-container": "#e0e0ff",
    "secondary": "#c4c4dd", "on-secondary": "#2d2f42", "secondary-container": "#444559",
    "on-secondary-container": "#e1e0f9", "tertiary": "#e7b9d5", "on-tertiary": "#45263c",
    "tertiary-container": "#5e3c53", "on-tertiary-container": "#ffd7ef", "error": "#ffb4ab", "on-error": "#690005",
    "error-container": "#93000a", "on-error-container": "#ffdad6", "surface": "#131318", "on-surface": "#e4e1e9",
    "surface-variant": "#46464f", "on-surface-variant": "#c7c5d0", "surface-dim": "#131318",
    "surface-bright": "#39393f", "surface-container-lowest": "#0d0e13", "surface-container-low": "#1b1b21",
    "surface-container": "#1f1f25", "surface-container-high": "#2a292f", "surface-container-highest": "#34343a",
    "inverse-surface": "#e4e1e9", "inverse-on-surface": "#303036", "inverse-primary": "#545a92",
    "outline": "#91909a", "outline-variant": "#46464f", "shadow": "#000000", "scrim": "#000000",
    "surface-tint": "#bdc2ff", "primary-fixed": "#e0e0ff", "primary-fixed-dim": "#bdc2ff",
    "on-primary-fixed": "#0f144b", "on-primary-fixed-variant": "#3d4279", "secondary-fixed": "#e1e0f9",
    "secondary-fixed-dim": "#c4c4dd", "on-secondary-fixed": "#191a2c", "on-secondary-fixed-variant": "#444559",
    "tertiary-fixed": "#ffd7ef", "tertiary-fixed-dim": "#e7b9d5", "on-tertiary-fixed": "#2e1126",
    "on-tertiary-fixed-variant": "#5e3c53", "background": "#131318", "on-background": "#e4e1e9",
    },
}


def _rgb(value: str, roles: dict[str, str], depth: int = 0) -> tuple[float, float, float] | None:
    v = value.strip()
    mt = re.fullmatch(r"var\(--md-sys-color-([a-z-]+)(?:,\s*(.+))?\)", v)
    if mt and depth < 4:  # another role (or the fallback of an accent variable)
        inner = roles.get(mt[1])
        return _rgb(inner, roles, depth + 1) if inner else (_rgb(mt[2], roles, depth + 1) if mt[2] else None)
    mt = re.fullmatch(r"var\(--[a-z0-9-]+,\s*(.+)\)", v)
    if mt and depth < 4:
        return _rgb(mt[1], roles, depth + 1)
    mt = re.fullmatch(r"#([0-9a-fA-F]{3,8})", v)
    if mt:
        h = mt[1]
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h[:3])
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    mt = re.fullmatch(r"rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:\s*[,/]\s*[\d.]+%?)?\s*\)", v)
    if mt:
        return tuple(min(255.0, float(x)) / 255 for x in mt.groups())  # type: ignore[return-value]
    return None


def _luminance(rgb: tuple[float, float, float]) -> float:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def check(spec: ThemeSpec) -> list[dict[str, object]]:
    """Each text role that falls below 4.5:1 on a surface it's written on: {mode, text, surface,
    ratio, message}, the worst first."""
    modes = ["light", "dark"] if spec.modes == "both" else [spec.modes]
    out: list[dict[str, object]] = []
    for mode in modes:
        mine = spec.colours.light if mode == "light" else spec.colours.dark
        roles = {**CASTOR[mode], **mine}
        for text, grounds in PAIRS.items():
            for ground in grounds:
                if text not in mine and ground not in mine:
                    continue  # both Castor's own: not this theme's doing
                fg, bg = _rgb(roles[text], roles), _rgb(roles[ground], roles)
                if fg is None or bg is None:
                    continue
                r = ratio(fg, bg)
                if r < MINIMUM:
                    out.append({"mode": mode, "text": text, "surface": ground, "ratio": round(r, 2),
                                "message": f"{mode}: {text} on {ground} is {r:.1f}:1 (needs {MINIMUM:g}:1)"})
    return sorted(out, key=lambda w: w["ratio"])
