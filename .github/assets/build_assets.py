"""Make the README images in .github/assets/.

The banner and diagrams use the app's own look: colour roles from web/src/lib/m3/tokens.css,
Roboto Flex and Roboto Mono, Material Symbols, each in a dark and a light version. The footer is
the AstraLabs lockup from the brand repository. All text is converted to outlines, because
GitHub does not load fonts in SVG images.

Usage:

    python3 .github/assets/build_assets.py [path/to/astralabs_design]

The script needs fontTools (pip install fonttools). The default path of the
brand repository is ../astralabs_design next to this repository.
"""

import re
import sys
import urllib.request
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BRAND = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "astralabs_design"
FONT = BRAND / "brand" / "fonts" / "MartianMono[wdth,wght].ttf"
LOCKUP = BRAND / "brand" / "astralabs-lockup.svg"

VOID = "#0A0909"  # the AstraLabs brand background, for the footer


def num(value):
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


class Face:
    """One instance of a variable font (Martian Mono unless another is given)."""

    def __init__(self, weight, width, font_path=FONT, **axes):
        font = TTFont(font_path)
        present = {a.axisTag for a in font["fvar"].axes}
        limits = {k: v for k, v in {"wght": weight, "wdth": width, **axes}.items() if k in present}
        font = instancer.instantiateVariableFont(font, limits)
        self.glyphs = font.getGlyphSet()
        self.cmap = font.getBestCmap()
        self.hmtx = font["hmtx"]
        self.upm = font["head"].unitsPerEm

    def advance(self, char, size):
        return self.hmtx[self.cmap[ord(char)]][0] * size / self.upm

    def glyph(self, char, size, x, baseline):
        """Returns the SVG path data of one glyph with its origin at x."""
        scale = size / self.upm
        pen = SVGPathPen(self.glyphs, ntos=num)
        name = self.cmap[ord(char)]
        self.glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x, baseline)))
        return pen.getCommands()

    def width(self, text, size, tracking):
        """Returns the width of a text, without the tracking after the last
        character."""
        step = tracking * size
        return sum(self.advance(c, size) + step for c in text) - step

    def text(self, text, size, tracking, x, baseline, anchor="start"):
        """Returns the path data of a line of text."""
        width = self.width(text, size, tracking)
        if anchor == "middle":
            x -= width / 2
        elif anchor == "end":
            x -= width
        parts = []
        for char in text:
            if char != " ":
                parts.append(self.glyph(char, size, x, baseline))
            x += self.advance(char, size) + tracking * size
        return " ".join(parts)


# ---------- the Castor banner: the app's own look (Material You tokens, Roboto Flex) ----------
# It follows the UI rather than the AstraLabs terminal style: the README is where people first
# see Castor, so it should look like the thing they'll use. The footer keeps the AstraLabs mark.

CACHE = HERE / ".cache"  # downloaded once, not committed (see .gitignore)
ROBOTO_URL = ("https://github.com/google/fonts/raw/main/ofl/robotoflex/"
              "RobotoFlex%5BGRAD%2CXOPQ%2CXTRA%2CYOPQ%2CYTAS%2CYTDE%2CYTFI%2CYTLC%2CYTUC%2Copsz%2Cslnt%2Cwdth%2Cwght%5D.ttf")
RADAR_URL = "https://fonts.gstatic.com/s/i/short-term/release/materialsymbolsrounded/radar/fill1/48px.svg"
MONO_URL = "https://github.com/google/fonts/raw/main/ofl/robotomono/RobotoMono%5Bwght%5D.ttf"
SYMBOLS = "https://fonts.gstatic.com/s/i/short-term/release/materialsymbolsrounded"
TOKENS = ROOT / "web" / "src" / "lib" / "m3" / "tokens.css"


def cached(name, url):
    """A file from the web (Roboto Flex, OFL; a Material Symbol, Apache 2.0), fetched once."""
    path = CACHE / name
    if not path.exists():
        CACHE.mkdir(exist_ok=True)
        with urllib.request.urlopen(url) as resp:
            path.write_bytes(resp.read())
    return path


def theme_tokens(theme):
    """The app's colour roles for one theme, read from the generated tokens.css."""
    css = TOKENS.read_text()
    selector = ":root, :root[data-theme='light'] {" if theme == "light" else ":root[data-theme='dark'] {"
    block = css[css.index(selector):]
    block = block[: block.index("}")]
    return dict(re.findall(r"--md-sys-color-([a-z-]+):\s*(#[0-9a-fA-F]{6})", block))


def rounded(x, y, w, h, r, fill, extra=""):
    return f'<rect x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}" rx="{num(r)}" fill="{fill}"{extra}/>'


def castor_banner(theme):
    c = theme_tokens(theme)
    roboto = cached("RobotoFlex.ttf", ROBOTO_URL)
    title = Face(560, 100, roboto, opsz=96)
    body = Face(420, 100, roboto, opsz=24)
    small = Face(560, 100, roboto, opsz=14)
    radar = re.search(r' d="([^"]+)"', cached("radar.svg", RADAR_URL).read_text()).group(1)
    width, height = 1280, 360
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
        f'height="{height}" role="img" aria-label="Castor: the Kanban board that runs the work">',
        f'<rect width="{width}" height="{height}" fill="{c["surface"]}"/>',
        # The app's elevation 1 for board cards (only visible on the light surfaces).
        '<defs><filter id="e1" x="-10%" y="-10%" width="120%" height="140%">'
        '<feDropShadow dx="0" dy="1" stdDeviation="1.5" flood-color="#000" flood-opacity=".14"/></filter></defs>',
    ]

    # The app mark (the rail's: radar on primary-container), the name and what it is.
    x0, top = 88, 92
    out.append(rounded(x0, top, 76, 76, 22, c["primary-container"]))
    out.append(f'<g transform="translate({x0 + 14} {top + 14}) scale({48 / 960}) translate(0 960)">'
               f'<path fill="{c["on-primary-container"]}" d="{radar}"/></g>')
    out.append(f'<path fill="{c["on-surface"]}" d="{title.text("Castor", 84, -0.01, x0 + 100, top + 66)}"/>')
    out.append(f'<path fill="{c["on-surface-variant"]}" d="{body.text("The Kanban board that runs the work", 26, 0, x0, top + 128)}"/>')

    # A few of the things it does, as the app's assist chips (the first one selected).
    x, y = x0, top + 158
    for i, text in enumerate(["Board", "Playbooks", "Agents", "Assets", "API + MCP"]):
        w = small.width(text, 15, 0.01) + 32
        if i == 0:
            out.append(rounded(x, y, w, 36, 10, c["secondary-container"]))
            fill = c["on-secondary-container"]
        else:
            out.append(rounded(x + 0.5, y + 0.5, w - 1, 35, 10, "none", f' stroke="{c["outline-variant"]}"'))
            fill = c["on-surface-variant"]
        out.append(f'<path fill="{fill}" d="{small.text(text, 15, 0.01, x + 16, y + 23)}"/>')
        x += w + 10

    # A small board: three columns of cards with the app's status colours.
    status = {
        "running": c["tertiary-container"], "done": c["primary-container"], "failed": c["error-container"],
        "queued": c["surface-container-highest"],
    }
    columns = [
        ("To Do", [(0.78, None), (0.6, "queued")]),
        ("In Progress", [(0.7, "running"), (0.84, "run"), (0.55, "running")]),
        ("Review", [(0.66, "done"), (0.8, "failed"), (0.5, "done")]),
    ]
    bx, by, col_w, gap = 712, 44, 152, 14
    for ci, (name, cards) in enumerate(columns):
        cx = bx + ci * (col_w + gap)
        out.append(rounded(cx, by, col_w, height - by * 2, 18, c["surface-container-low"]))
        out.append(f'<path fill="{c["on-surface"]}" d="{small.text(name, 14, 0.01, cx + 16, by + 30)}"/>')
        count = str(len(cards))
        out.append(rounded(cx + col_w - 36, by + 16, 22, 20, 10, c["surface-container-highest"]))
        out.append(f'<path fill="{c["on-surface-variant"]}" d="{small.text(count, 12, 0, cx + col_w - 25, by + 30, "middle")}"/>')
        y = by + 50
        for line, state in cards:
            h = 74 if state == "run" else 62
            card = (c["surface-container-lowest"], ' filter="url(#e1)"') if theme == "light" else (c["surface-container-high"], "")
            out.append(rounded(cx + 10, y, col_w - 20, h, 12, card[0], card[1]))
            out.append(rounded(cx + 22, y + 14, (col_w - 44) * line, 8, 4, c["on-surface"], ' fill-opacity=".72"'))
            out.append(rounded(cx + 22, y + 28, (col_w - 44) * line * 0.6, 6, 3, c["on-surface-variant"], ' fill-opacity=".4"'))
            if state == "run":  # a playbook run card: progress so far
                out.append(rounded(cx + 22, y + 46, col_w - 44, 6, 3, c["surface-container-highest"]))
                out.append(rounded(cx + 22, y + 46, (col_w - 44) * 0.62, 6, 3, c["primary"]))
            elif state:
                out.append(rounded(cx + 22, y + 42, 44, 10, 5, status[state]))
            y += h + 10

    out.append(f'<path fill="{c["on-surface-variant"]}" fill-opacity=".8" d="{small.text("Self-hosted · open source · MIT", 13, 0.02, x0, height - 34)}"/>')
    out.append("</svg>\n")
    return "\n".join(out)


def icon(name, x, y, size, fill, style="default"):
    """A Material Symbols Rounded icon (Apache 2.0) with its top left corner at x, y."""
    path = cached(f"{name}-{style}.svg", f"{SYMBOLS}/{name}/{style}/48px.svg")
    d = re.search(r' d="([^"]+)"', path.read_text()).group(1)
    return (f'<g transform="translate({num(x)} {num(y)}) scale({size / 960:.5f}) translate(0 960)">'
            f'<path fill="{fill}" d="{d}"/></g>')


class Look:
    """The app's colours and type for one theme, shared by the README diagrams."""

    def __init__(self, theme):
        self.theme = theme
        self.c = theme_tokens(theme)
        roboto = cached("RobotoFlex.ttf", ROBOTO_URL)
        self.title = Face(560, 100, roboto, opsz=24)
        self.body = Face(420, 100, roboto, opsz=14)
        self.mono = Face(450, 100, cached("RobotoMono.ttf", MONO_URL))

    def text(self, face, text, size, x, baseline, fill, anchor="start", tracking=0.0):
        return f'<path fill="{self.c[fill]}" d="{face.text(text, size, tracking, x, baseline, anchor)}"/>'

    def card(self, x, y, w, h, r):
        """A card on a container: raised with elevation 1 in light, a lighter tone in dark."""
        if self.theme == "light":
            return rounded(x, y, w, h, r, self.c["surface-container-lowest"], ' filter="url(#e1)"')
        return rounded(x, y, w, h, r, self.c["surface-container-high"])

    def chip(self, text, cx, cy, tone="surface-container-highest", on="on-surface-variant"):
        w = self.mono.width(text, 12, 0) + 20
        return (rounded(cx - w / 2, cy - 12, w, 24, 12, self.c[tone]) + "\n"
                + self.text(self.mono, text, 12, cx, cy + 4.5, on, "middle"))

    def open(self, width, height, label, background="surface"):
        return [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
            f'height="{height}" role="img" aria-label="{label}">',
            '<defs><filter id="e1" x="-10%" y="-10%" width="120%" height="140%">'
            '<feDropShadow dx="0" dy="1" stdDeviation="1.5" flood-color="#000" flood-opacity=".14"/></filter></defs>',
            f'<rect width="{width}" height="{height}" fill="{self.c[background]}"/>',
        ]


def double_arrow(look, x1, x2, y, color, width=2):
    """A horizontal link with a filled head at each end (the graph's arrowheads)."""
    c = look.c[color]
    out = [f'<path d="M{num(x1 + 8)} {num(y)} H{num(x2 - 8)}" stroke="{c}" stroke-width="{width}" stroke-linecap="round"/>']
    for tip, back in ((x1, x1 + 10), (x2, x2 - 10)):
        out.append(f'<path d="M{num(tip)} {num(y)} L{num(back)} {num(y - 5)} L{num(back)} {num(y + 5)} Z" fill="{c}"/>')
    return "\n".join(out)


def architecture(theme):
    """Browser, server and agents, and what flows between them."""
    look = Look(theme)
    c = look.c
    width, height = 1280, 480
    out = look.open(width, height, "Castor architecture: the browser talks to the server over HTTP and a "
                    "WebSocket; agents connect to the server over a WebSocket and run jobs in containers "
                    "or the host shell.")
    top, bottom, panel_w = 56, 432, 320
    panels = (
        (56, "language", "Browser", "SvelteKit",
         (("Board", "cards, columns"), ("Playbooks", "graph, runs"), ("Live output", "logs, results"))),
        (480, "dns", "Server", "FastAPI",
         (("API + events", "REST, /ws/ui, /api/mcp"), ("Dispatcher", "jobs to agents"), ("Parsers", "outputs, assets"))),
        (904, "deployed_code", "Agents", "any number",
         (("Job runner", "/ws/agent"), ("Containers", "podman, docker"), ("Host shell", "--allow-host"))),
    )
    rows_y, row_h = (144, 236, 328), 76
    for x, glyph, title, sub, rows in panels:
        out.append(rounded(x, top, panel_w, bottom - top, 28, c["surface-container-low"]))
        out.append(rounded(x + 24, top + 24, 44, 44, 14, c["primary-container"]))
        out.append(icon(glyph, x + 34, top + 34, 24, c["on-primary-container"]))
        out.append(look.text(look.title, title, 20, x + 84, top + 44, "on-surface"))
        out.append(look.text(look.body, sub, 14, x + 84, top + 64, "on-surface-variant"))
        for (name, detail), y in zip(rows, rows_y):
            out.append(look.card(x + 16, y, panel_w - 32, row_h, 16))
            out.append(look.text(look.title, name, 16, x + 36, y + 32, "on-surface"))
            out.append(look.text(look.mono, detail, 13, x + 36, y + 54, "on-surface-variant"))
    # Links at the level of the rows they connect.
    a_y = rows_y[0] + row_h / 2
    out.append(double_arrow(look, 376 + 6, 480 - 6, a_y, "outline"))
    out.append(look.chip("HTTP + WS", 428, a_y - 26))
    b_y = rows_y[1] + row_h / 2
    out.append(double_arrow(look, 800 + 6, 904 - 6, b_y, "primary", 2.5))
    out.append(look.chip("job.run", 852, b_y - 26, "primary-container", "on-primary-container"))
    out.append(look.chip("logs, files", 852, b_y + 26))
    out.append("</svg>\n")
    return "\n".join(out)


def playbook(theme):
    """An example playbook drawn like the app's graph: commands, transforms, fan-out and fan-in."""
    look = Look(theme)
    c = look.c
    width, height = 1280, 460
    out = look.open(width, height, "A playbook graph: one seed step fans out into three parallel branches, "
                    "transform steps reshape the data between commands, and a report step collects all "
                    "branches.", "surface-container-lowest")
    out.insert(2, '<defs><pattern id="dots" width="20" height="20" patternUnits="userSpaceOnUse">'
               f'<circle cx="10" cy="10" r="1.1" fill="{c["outline-variant"]}"/></pattern></defs>')
    out.append(f'<rect width="{width}" height="{height}" fill="url(#dots)"/>')

    node_w, node_h = 168, 80
    cols = [44 + i * ((width - 88 - node_w) / 5) for i in range(6)]
    rows = (160, 260, 360)  # node centre lines
    # key: (column, row, title, command id or ops, parser, transform)
    nodes = {
        "seed": (0, 1, "Seed hosts", "seed-hosts", "nmap.hosts", False),
        "svc": (1, 0, "Service scan", "service-scan", "nmap.services", False),
        "smb": (1, 1, "SMB port scan", "smb-scan", "nmap.hosts", False),
        "dns": (1, 2, "DNS names", "dns-names", "lines", False),
        "web": (2, 0, "Web URLs", "filter · format", None, True),
        "audit": (2, 1, "SMB audit", "smb-audit", "netexec.smb", False),
        "dir": (3, 0, "Dir brute force", "dir-brute", "ffuf.results", False),
        "unsigned": (3, 1, "Signing off", "filter · pluck", None, True),
        "interesting": (4, 0, "Interesting", "filter · pluck", None, True),
        "report": (5, 1, "Report", "report", "text", False),
    }
    edges = (
        ("seed", "svc"), ("seed", "smb"), ("seed", "dns"),
        ("svc", "web"), ("smb", "audit"),
        ("web", "dir"), ("audit", "unsigned"),
        ("dir", "interesting"),
        ("interesting", "report"), ("unsigned", "report"), ("dns", "report"),
    )

    def anchor(key, side):
        col, row, *_ = nodes[key]
        return cols[col] + (node_w if side == "out" else 0), rows[row]

    for a, b in edges:
        (x1, y1), (x2, y2) = anchor(a, "out"), anchor(b, "in")
        x2 -= 9  # leave room for the arrowhead
        if x2 - x1 > 140:  # long edges run straight, then bend into the target
            bx = x2 - 110
            d = f"M{num(x1)} {num(y1)} H{num(bx)} C{num(bx + 60)} {num(y1)} {num(x2 - 50)} {num(y2)} {num(x2)} {num(y2)}"
        else:
            mid = (x1 + x2) / 2
            d = f"M{num(x1)} {num(y1)} C{num(mid)} {num(y1)} {num(mid)} {num(y2)} {num(x2)} {num(y2)}"
        out.append(f'<path d="{d}" fill="none" stroke="{c["primary"]}" stroke-width="2.2" stroke-opacity=".85"/>')
        out.append(f'<path d="M{num(x2 + 9)} {num(y2)} L{num(x2 - 1)} {num(y2 - 5)} L{num(x2 - 1)} {num(y2 + 5)} Z" fill="{c["primary"]}"/>')

    for key, (col, row, title, sub, parser, transform) in nodes.items():
        x, y = cols[col], rows[row] - node_h / 2
        accent = c["tertiary"] if transform else c["primary"]
        if transform:
            out.append(rounded(x + .5, y + .5, node_w - 1, node_h - 1, 12, c["surface-container-low"],
                               f' stroke="{accent}" stroke-opacity=".7" stroke-dasharray="5 4"'))
        else:
            out.append(look.card(x, y, node_w, node_h, 12))
            out.append(rounded(x + 12, y, node_w - 24, 3, 1.5, accent))
        out.append(icon("filter_alt" if transform else "terminal", x + 12, y + 13, 16,
                        accent if transform else c["on-surface-variant"]))
        out.append(look.text(look.title, title, 14, x + 34, y + 26, "on-surface"))
        out.append(look.text(look.mono, sub, 11, x + 14, y + 46, "on-surface-variant"))
        out.append(icon("check_circle", x + 13, y + 56, 14, c["primary"]))
        out.append(look.text(look.body, "Done", 12, x + 32, y + 67, "primary"))
        if parser:
            w = look.mono.width(parser, 10, 0) + 12
            out.append(rounded(x + node_w - 12 - w, y + 55, w, 16, 4, c["tertiary-container"]))
            out.append(look.text(look.mono, parser, 10, x + node_w - 18, y + 66.5, "on-tertiary-container", "end"))
        # The graph's output handle, on steps something reads from.
        hy = y + node_h / 2
        if any(a == key for a, _ in edges):
            out.append(f'<circle cx="{num(x + node_w)}" cy="{num(hy)}" r="5" fill="{accent}" stroke="{c["surface-container-lowest"]}" stroke-width="2"/>')

    # Legend (the graph's own), top left; caption, bottom left.
    lx, ly = 48, 44
    out.append(rounded(lx, ly, 352, 36, 18, c["surface-container"]))
    out.append(icon("terminal", lx + 16, ly + 10, 16, c["on-surface-variant"]))
    out.append(look.text(look.body, "Command", 13, lx + 38, ly + 23, "on-surface"))
    out.append(icon("filter_alt", lx + 118, ly + 10, 16, c["tertiary"]))
    out.append(look.text(look.body, "Transform", 13, lx + 140, ly + 23, "tertiary"))
    out.append(rounded(lx + 232, ly + 17, 18, 2.5, 1.25, c["primary"]))
    out.append(look.text(look.body, "Data flow", 13, lx + 258, ly + 23, "on-surface"))
    out.append(look.text(look.body, "An example playbook: parallel branches that meet in one report", 14,
                         48, height - 30, "on-surface-variant"))
    out.append("</svg>\n")
    return "\n".join(out)


def footer():
    """The official AstraLabs horizontal lockup on a void panel, so that it
    shows on light and dark pages. The lockup paths are copied unchanged."""
    lockup = LOCKUP.read_text()
    inner = re.search(r"<svg[^>]*>(.*)</svg>", lockup, re.S).group(1)
    inner = re.sub(r"<!--.*?-->", "", inner, flags=re.S).strip()
    width, height = 640, 120
    lock_h = 52
    scale = lock_h / 105
    lock_w = 904.68 * scale
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width // 2}" height="{height // 2}" role="img" aria-label="AstraLabs">\n'
        f'<rect width="{width}" height="{height}" fill="{VOID}"/>\n'
        f'<g transform="translate({num((width - lock_w) / 2)} {num((height - lock_h) / 2)}) '
        f'scale({scale:.5f})">{inner}</g>\n'
        "</svg>\n"
    )



def main():
    outputs = {
        "banner.svg": castor_banner("dark"),
        "banner-light.svg": castor_banner("light"),
        "architecture.svg": architecture("dark"),
        "architecture-light.svg": architecture("light"),
        "playbook.svg": playbook("dark"),
        "playbook-light.svg": playbook("light"),
        "footer.svg": footer(),
    }
    for name, svg in outputs.items():
        (HERE / name).write_text(svg)
        print(f"wrote {name} ({len(svg)} bytes)")


if __name__ == "__main__":
    main()
