"""Theme spec (docs/THEME_SPEC.md): a YAML file that restyles the web UI. The server checks it
and compiles it to CSS scoped to `html:root[data-skin='<id>']`, which the browser injects when
someone picks the theme. Built-ins come from themes/*.yaml, the rest from Settings → Themes.

Everything a theme sets lands inside its own scope: colour roles and type/shape variables in
one block per mode, and its extra `css` nested inside the scope (CSS nesting), so a theme can
never style the app when it isn't the chosen one. Values are checked so they can't close the
block, and the CSS can't import, fetch from other sites or run script."""

from __future__ import annotations

import re
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

# Material 3 colour roles (web lib/m3/tokens.css), Castor's categorical colours and the pinned
# notes' colours. A theme sets any of them; what it leaves out keeps Castor's value.
ROLES = (
    "primary", "on-primary", "primary-container", "on-primary-container",
    "secondary", "on-secondary", "secondary-container", "on-secondary-container",
    "tertiary", "on-tertiary", "tertiary-container", "on-tertiary-container",
    "error", "on-error", "error-container", "on-error-container",
    "background", "on-background", "surface", "on-surface", "surface-variant", "on-surface-variant",
    "surface-dim", "surface-bright", "surface-container-lowest", "surface-container-low",
    "surface-container", "surface-container-high", "surface-container-highest",
    "inverse-surface", "inverse-on-surface", "inverse-primary",
    "outline", "outline-variant", "shadow", "scrim", "surface-tint",
    "primary-fixed", "primary-fixed-dim", "on-primary-fixed", "on-primary-fixed-variant",
    "secondary-fixed", "secondary-fixed-dim", "on-secondary-fixed", "on-secondary-fixed-variant",
    "tertiary-fixed", "tertiary-fixed-dim", "on-tertiary-fixed", "on-tertiary-fixed-variant",
)
CUSTOM = tuple(f"{p}{c}{s}" for c in ("blue", "amber", "violet", "green")
               for p, s in (("", ""), ("on-", ""), ("", "-container"), ("on-", "-container")))
NOTES = tuple(f"note-{n}" for n in ("default", "red", "orange", "yellow", "green", "teal", "blue", "navy",
                                    "purple", "pink", "brown", "gray"))
# Castor's own: the hover of a filled primary (a theme's CSS uses var(--theme-primary-hover)).
EXTRA = ("primary-hover",)
COLOUR_KEYS = frozenset(ROLES + CUSTOM + NOTES + EXTRA)

# With `colour: accent`, the person's theme colour replaces these (theme.ts accent()): each
# compiles to var(--theme-accent…, <the theme's value>).
ACCENT_VARS = {
    "primary": "--theme-accent", "surface-tint": "--theme-accent", "primary-fixed": "--theme-accent",
    "primary-hover": "--theme-accent-hover", "on-primary-container": "--theme-accent-hover",
    "primary-container": "--theme-accent-container", "inverse-primary": "--theme-accent-inverse",
    "primary-fixed-dim": "--theme-accent-dim", "on-primary-fixed-variant": "--theme-accent-deep",
}

ID = r"^[a-z0-9][a-z0-9-]{1,39}$"
_COLOUR = re.compile(
    r"^(#[0-9a-fA-F]{3,8}|transparent|currentColor"
    r"|(rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color|color-mix)\([^;{}<>\\]*\)"
    r"|var\(--[a-zA-Z0-9-]+(,\s*[^;{}<>\\]*)?\))$")
_SAFE_VALUE = re.compile(r"^[^;{}<>\\]*$")  # one CSS value: can't end the declaration or the block
_FONT_URL = re.compile(r"^(https://[^\s\"'()<>;{}\\]+|/[A-Za-z0-9._/-]+)$")
_LENGTH = re.compile(r"^(0|\d+(\.\d+)?(px|rem|em|%))$")


def _value(v: str, what: str) -> str:
    v = str(v).strip()
    if not v or not _SAFE_VALUE.match(v):
        raise ValueError(f"{what}: one CSS value, without ; {{ }} < > or \\")
    return v


class FontFace(BaseModel):
    """A font file to load (@font-face): from this server (a /path) or https."""

    model_config = ConfigDict(extra="forbid")
    family: str
    src: str
    weight: str = "400"
    style: Literal["normal", "italic"] = "normal"
    stretch: str | None = None
    format: Literal["woff2", "woff", "truetype", "opentype"] = "woff2"

    @field_validator("family", "weight")
    @classmethod
    def _plain(cls, v: str) -> str:
        if not re.match(r"^[A-Za-z0-9 ._-]+$", v):
            raise ValueError("letters, digits, spaces, dots, dashes")
        return v

    @field_validator("stretch")
    @classmethod
    def _stretch(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^\d+(\.\d+)?%( \d+(\.\d+)?%)?$", v):
            raise ValueError("a percentage or a range, like 75% 112.5%")
        return v

    @field_validator("src")
    @classmethod
    def _src(cls, v: str) -> str:
        if not _FONT_URL.match(v):
            raise ValueError("an https:// URL or a path on this server (/fonts/…)")
        return v


class TypeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str | None = None  # font-family list for everything written
    mono: str | None = None  # for commands, code, logs
    icons: Literal["rounded", "sharp", "outlined"] = "rounded"
    icon_weight: int = Field(400, ge=100, le=700)
    icon_fill: bool | None = None  # None: filled where Castor fills (the active nav item)

    @field_validator("body", "mono")
    @classmethod
    def _family(cls, v: str | None) -> str | None:
        return None if v is None else _value(v, "font family")


CORNERS = ("extra-small", "small", "medium", "large", "extra-large")


class ShapeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Square every corner in the app (pills, avatars, dialogs…), except spinners and radios.
    square: bool = False
    corners: dict[str, str] = Field(default_factory=dict)  # extra-small … extra-large

    @field_validator("corners")
    @classmethod
    def _corners(cls, v: dict[str, str]) -> dict[str, str]:
        for k, val in v.items():
            if k not in CORNERS:
                raise ValueError(f"{k}: one of {', '.join(CORNERS)}")
            if not _LENGTH.match(str(val)):
                raise ValueError(f"{k}: a length, like 0, 6px or 0.5rem")
        return {k: str(val) for k, val in v.items()}


class SizeSpec(BaseModel):
    """How dense the interface is. `spacing` is the step every padding, gap and control height is
    a multiple of (Tailwind's --spacing; Castor's is 0.25rem); `text` scales every type style."""

    model_config = ConfigDict(extra="forbid")
    spacing: str | None = None
    text: float = Field(1.0, ge=0.75, le=1.5)

    @field_validator("spacing")
    @classmethod
    def _spacing(cls, v: str | None) -> str | None:
        if v is None:
            return None
        mt = re.match(r"^(\d+(?:\.\d+)?)(px|rem)$", str(v).strip())
        if not mt:
            raise ValueError("a length in px or rem, like 0.2rem or 3px")
        rem = float(mt[1]) / (16 if mt[2] == "px" else 1)
        if not 0.15 <= rem <= 0.4:
            raise ValueError("between 0.15rem and 0.4rem (Castor's is 0.25rem)")
        return str(v).strip()


class Colours(BaseModel):
    model_config = ConfigDict(extra="forbid")
    light: dict[str, str] = Field(default_factory=dict)
    dark: dict[str, str] = Field(default_factory=dict)

    @field_validator("light", "dark")
    @classmethod
    def _roles(cls, v: dict[str, str]) -> dict[str, str]:
        out = {}
        for k, val in (v or {}).items():
            if k not in COLOUR_KEYS:
                raise ValueError(f"{k}: not a colour role (see the theme spec for the list)")
            val = str(val).strip()
            if not _COLOUR.match(val):
                raise ValueError(f"{k}: a colour (#rrggbb, rgb(), oklch(), color-mix(), var(--…))")
            out[k] = val
        return out


class ThemeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=ID)
    name: str = Field(min_length=1, max_length=60)
    description: str = Field("", max_length=300)
    # Which of light and dark it has; with one, the light/dark switch is hidden.
    modes: Literal["both", "dark", "light"] = "both"
    # What a person's theme colour recolours: every role from it (palette, Material You), only
    # the accent (accent: primary and its family, see ACCENT_VARS), or nothing (fixed).
    colour: Literal["palette", "accent", "fixed"] = "accent"
    default_colour: str = Field("#4f5bd5", pattern=r"^#[0-9a-fA-F]{6}$")  # the picker's "Default"
    fonts: list[str] = Field(default_factory=list)  # stylesheets to load (https), like Google Fonts
    font_faces: list[FontFace] = Field(default_factory=list)
    type: TypeSpec = Field(default_factory=TypeSpec)
    shape: ShapeSpec = Field(default_factory=ShapeSpec)
    size: SizeSpec = Field(default_factory=SizeSpec)
    elevation: Literal["shadow", "hairline", "none"] = "shadow"
    colours: Colours = Field(default_factory=Colours)
    # For the picker's tile: background, surface, text, accent (taken from the colours if left out).
    preview: list[str] | None = None
    css: str = ""

    @field_validator("fonts")
    @classmethod
    def _fonts(cls, v: list[str]) -> list[str]:
        for url in v:
            if not re.match(r"^https://[^\s\"'<>\\{}()]+$", url):
                raise ValueError(f"{url}: an https:// stylesheet URL")
        return v

    @field_validator("preview")
    @classmethod
    def _preview(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and (len(v) != 4 or not all(re.match(r"^#[0-9a-fA-F]{6}$", c) for c in v)):
            raise ValueError("four #rrggbb colours: background, surface, text, accent")
        return v

    @field_validator("css")
    @classmethod
    def _css(cls, v: str) -> str:
        check_css(v)
        return v

    @model_validator(mode="after")
    def _modes(self) -> ThemeSpec:
        if self.modes == "dark" and self.colours.light:
            raise ValueError("colours.light: a dark-only theme has no light colours")
        if self.modes == "light" and self.colours.dark:
            raise ValueError("colours.dark: a light-only theme has no dark colours")
        return self

    def preview_colours(self) -> list[str]:
        if self.preview:
            return self.preview
        c = self.colours.dark if self.modes == "dark" else (self.colours.light or self.colours.dark)
        castor = ["#fbf8ff", "#efedf4", "#1b1b21", "#545a92"] if self.modes != "dark" else ["#131318", "#1f1f25", "#e4e1e9", "#bdc2ff"]

        def hex_or(key: str, fallback: str) -> str:
            val = c.get(key, "")
            return val if re.match(r"^#[0-9a-fA-F]{6}$", val) else fallback
        return [hex_or("surface", castor[0]), hex_or("surface-container", castor[1]),
                hex_or("on-surface", castor[2]), hex_or("primary", castor[3])]


# ---------- the extra CSS ----------

_BANNED = (
    (re.compile(r"</", re.IGNORECASE), "can't contain </"),
    (re.compile(r"@import|@charset|@namespace|@font-face", re.IGNORECASE), "no @import, @charset, @namespace or @font-face (use fonts and font_faces)"),
    (re.compile(r"expression\s*\(|javascript:|behavior\s*:|-moz-binding", re.IGNORECASE), "no script in CSS"),
)


def check_css(text: str) -> None:
    """Refuse CSS that could escape the theme's scope, load from elsewhere or run script.
    Braces must balance (outside strings and comments) so it can't close the scope block, and
    url() may point only at this server or a data: URI."""
    if len(text) > 100_000:
        raise ValueError("css: at most 100 000 characters")
    for pattern, why in _BANNED:
        if pattern.search(text):
            raise ValueError(f"css: {why}")
    depth, i, n = 0, 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise ValueError("css: a comment isn't closed")
            i = end + 2
            continue
        if c in "\"'":
            end = i + 1
            while end < n and text[end] != c:
                if text[end] in "\\\n":
                    raise ValueError("css: strings can't hold backslashes or line breaks")
                end += 1
            if end >= n:
                raise ValueError("css: a string isn't closed")
            i = end + 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0:
                raise ValueError("css: a } without its {")
        elif c == "\\":
            raise ValueError("css: no backslash escapes")
        i += 1
    if depth:
        raise ValueError("css: a { isn't closed")
    for m in re.finditer(r"url\(\s*(['\"]?)([^)'\"]*)\1\s*\)", text, re.IGNORECASE):
        target = m.group(2).strip()
        if not (target.startswith("/") and not target.startswith("//")) and not target.startswith("data:"):
            raise ValueError(f"css: url({target}) can only point at this server (/…) or be a data: URI")
    if re.search(r"url\(", text, re.IGNORECASE) and len(re.findall(r"url\(", text, re.IGNORECASE)) != len(re.findall(r"url\(\s*(['\"]?)([^)'\"]*)\1\s*\)", text, re.IGNORECASE)):
        raise ValueError("css: write url() as url(/path) or url('/path')")


# ---------- compiling ----------

ICON_FONTS = {
    "rounded": None,  # app.html loads it already
    "sharp": "https://fonts.googleapis.com/css2?family=Material+Symbols+Sharp:opsz,wght,FILL,GRAD@20..48,100..700,0..1,0&display=block",
    "outlined": "https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,0&display=block",
}
ICON_FAMILY = {"rounded": "Material Symbols Rounded", "sharp": "Material Symbols Sharp", "outlined": "Material Symbols Outlined"}


def _vars(spec: ThemeSpec, roles: dict[str, str]) -> list[str]:
    out = []
    for key, value in roles.items():
        if key in NOTES:
            name = f"--{key}"
        elif key in CUSTOM:
            name = f"--md-custom-color-{key}"
        elif key in EXTRA:
            name = f"--theme-{key}"
        else:
            name = f"--md-sys-color-{key}"
        if spec.colour == "accent" and key in ACCENT_VARS:
            value = f"var({ACCENT_VARS[key]}, {value})"
        out.append(f"{name}: {value};")
    if "primary-hover" not in roles and "primary" in roles:  # var(--theme-primary-hover) always works
        out.append("--theme-primary-hover: var(--theme-accent-hover, var(--md-sys-color-primary));"
                   if spec.colour == "accent" else "--theme-primary-hover: var(--md-sys-color-primary);")
    return out


def compile_css(spec: ThemeSpec) -> str:
    """The theme as CSS, every rule inside its scope (font faces aside, which CSS keeps at the top)."""
    scope = f"html:root[data-skin='{spec.id}']"
    lines: list[str] = [f"/* Castor theme: {spec.name} ({spec.id}), compiled from its spec. */"]
    for f in spec.font_faces:
        stretch = f" font-stretch: {f.stretch};" if f.stretch else ""
        lines.append(f"@font-face {{ font-family: '{f.family}'; src: url('{f.src}') format('{f.format}'); "
                     f"font-weight: {f.weight}; font-style: {f.style};{stretch} font-display: swap; }}")

    common: list[str] = []
    if spec.type.body:
        common.append(f"--md-sys-typescale-font: {spec.type.body};")
    if spec.type.mono:
        common.append(f"--md-sys-typescale-mono: {spec.type.mono};")
    for k, v in spec.shape.corners.items():
        common.append(f"--md-sys-shape-corner-{k}: {v};")
    if spec.size.spacing:
        common.append(f"--spacing: {spec.size.spacing};")
    if spec.size.text != 1:
        common.append(f"--md-sys-typescale-scale: {spec.size.text:g};")
    if spec.elevation != "shadow":
        rule = "0 0 0 1px var(--md-sys-color-outline-variant)" if spec.elevation == "hairline" else "none"
        lifted = ("0 0 0 1px color-mix(in oklab, var(--md-sys-color-outline-variant), var(--md-sys-color-on-surface) 28%)"
                  if spec.elevation == "hairline" else "none")
        common += [f"--md-sys-elevation-1: {rule};", f"--md-sys-elevation-2: {lifted};", f"--md-sys-elevation-3: {rule};",
                   f"--md-sys-elevation-4: {lifted};", f"--md-sys-elevation-5: {lifted};"]

    def block(selector: str, body: list[str]) -> None:
        if body:
            lines.append(selector + " {\n\t" + "\n\t".join(body) + "\n}")

    dark, light = spec.colours.dark, spec.colours.light
    if spec.modes == "dark":
        block(scope, ["color-scheme: dark;", *common, *_vars(spec, dark)])
    elif spec.modes == "light":
        block(scope, ["color-scheme: light;", *common, *_vars(spec, light)])
    else:
        block(scope, [*common])
        block(f"{scope}, {scope}[data-theme='light']", ["color-scheme: light;", *_vars(spec, light)])
        block(f"{scope}[data-theme='dark']", ["color-scheme: dark;", *_vars(spec, dark)])
        lines.append(f"@media (prefers-color-scheme: dark) {{\n{scope}:not([data-theme='light']) {{\n\tcolor-scheme: dark;\n\t"
                     + "\n\t".join(_vars(spec, dark)) + "\n}\n}")

    if spec.shape.square:
        lines.append(f"{scope} *, {scope} *::before, {scope} *::after {{ border-radius: 0 !important; }}")
        lines.append(f"{scope} .animate-spin, {scope} input[type='radio'] {{ border-radius: 9999px !important; }}")
    if spec.type.icons != "rounded" or spec.type.icon_weight != 400 or spec.type.icon_fill is not None:
        fill = "0" if spec.type.icon_fill is None else str(int(spec.type.icon_fill))
        settings = f"'FILL' {fill}, 'wght' {spec.type.icon_weight}, 'GRAD' 0, 'opsz' 24"
        lines.append(f"{scope} .material-symbols-rounded {{ font-family: '{ICON_FAMILY[spec.type.icons]}'; "
                     f"font-variation-settings: {settings}; }}")
        if spec.type.icon_fill is None:
            lines.append(f"{scope} .material-symbols-rounded.filled {{ font-variation-settings: 'FILL' 1, "
                         f"'wght' {spec.type.icon_weight}, 'GRAD' 0, 'opsz' 24; }}")
    if spec.css.strip():
        lines.append(f"{scope} {{\n{spec.css.rstrip()}\n}}")
    return "\n".join(lines) + "\n"


def stylesheets(spec: ThemeSpec) -> list[str]:
    """The stylesheets the browser loads with the theme: its fonts and its icon set."""
    icons = ICON_FONTS[spec.type.icons]
    return [*spec.fonts, *([icons] if icons and icons not in spec.fonts else [])]


def parse_theme_spec(text: str) -> ThemeSpec:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("a theme is a YAML mapping (id, name, colours…)")  # noqa: TRY004 (callers catch ValueError)
    try:
        return ThemeSpec.model_validate(data)
    except ValidationError as exc:
        raise ValueError("; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'theme'}: {e['msg'].removeprefix('Value error, ')}" for e in exc.errors()
        )) from exc
