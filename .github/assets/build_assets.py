"""Make the README images in .github/assets/ from the AstraLabs brand files.

The images follow the AstraLabs brand guide. All text is converted to
outlines, because GitHub does not load fonts in SVG images.

Usage:

    python3 .github/assets/build_assets.py [path/to/astralabs_design]

The script needs fontTools (pip install fonttools). The default path of the
brand repository is ../astralabs_design next to this repository.
"""

import math
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

# Brand tokens (astralabs_design/README.md, Color).
VOID = "#0A0909"
PLATE = "#131212"
RULE = "#2C2A29"
MARK = "#FFFFFF"
BONE = "#EDE6E1"
ASH = "#8E8380"
ACID = "#B6FF2E"

MARK_PATHS = (
    '<path d="M0 519.62 L300 0 L542.84 420.62 L466.63 420.62 L300 132 L76.21 519.62 Z"/>'
    '<path d="M114.32 519.62 L300 198 L338.11 264 L228.63 453.62 L561.89 453.62 L600 519.62 Z"/>'
)


def num(value):
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


class Face:
    """One instance of a variable font (Martian Mono unless another is given)."""

    def __init__(self, weight, width, font_path=FONT, **axes):
        font = instancer.instantiateVariableFont(TTFont(font_path), {"wght": weight, "wdth": width, **axes})
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


WORDMARK = Face(300, 112.5)
LABEL = Face(500, 85)
DATA = Face(400, 100)


def label(text, x, baseline, fill=ASH, size=13, anchor="start", face=LABEL, tracking=0.14):
    d = face.text(text.upper(), size, tracking, x, baseline, anchor)
    return f'<path fill="{fill}" d="{d}"/>'


def wordmark_paths(path_text):
    """Returns the letter paths and the acid paths of `[PATH]`, in lockup
    units (the coordinates of astralabs-lockup.svg).

    The measures come from the AstraLabs lockup: letter size 56, baseline
    74.9, tracking 0.36 em, and 0.14 em of extra space inside each bracket.
    """
    size, baseline, tracking, bracket = 56, 74.9, 0.36, 0.14
    step = WORDMARK.advance("A", size) + tracking * size
    x = 163.24  # origin of "[" in the AstraLabs lockup
    letters, acid = [], [WORDMARK.glyph("[", size, x, baseline)]
    x += step + bracket * size
    for char in path_text:
        target = acid if char == "/" else letters
        target.append(WORDMARK.glyph(char, size, x, baseline))
        x += step
    # x is now one step after the last letter.
    x += bracket * size
    acid.append(WORDMARK.glyph("]", size, x, baseline))
    right = x + 474 * size / 1000  # right edge of "]"
    return " ".join(letters), " ".join(acid), right


def frame(width, height):
    """The dot grid and the corner ticks of the AstraLabs banner."""
    ticks = []
    for x, dx in ((24, 14), (width - 24, -14)):
        for y, dy in ((24, 14), (height - 24, -14)):
            ticks.append(
                f'<path d="M{x} {y + dy} V{y} H{x + dx}" fill="none" stroke="{BONE}" '
                f'stroke-opacity=".55" stroke-width="1.5"/>'
            )
    return (
        '<defs><pattern id="dots" width="16" height="16" patternUnits="userSpaceOnUse">'
        f'<circle cx="8" cy="8" r="1" fill="{BONE}" fill-opacity=".16"/></pattern></defs>\n'
        f'<rect width="{width}" height="{height}" fill="{VOID}"/>\n'
        f'<rect width="{width}" height="{height}" fill="url(#dots)"/>\n' + "\n".join(ticks) + "\n"
    )


# ---------- the Castor banner: the app's own look (Material You tokens, Roboto Flex) ----------
# It follows the UI rather than the AstraLabs terminal style: the README is where people first
# see Castor, so it should look like the thing they'll use. The footer keeps the AstraLabs mark.

CACHE = HERE / ".cache"  # downloaded once, not committed (see .gitignore)
ROBOTO_URL = ("https://github.com/google/fonts/raw/main/ofl/robotoflex/"
              "RobotoFlex%5BGRAD%2CXOPQ%2CXTRA%2CYOPQ%2CYTAS%2CYTDE%2CYTFI%2CYTLC%2CYTUC%2Copsz%2Cslnt%2Cwdth%2Cwght%5D.ttf")
RADAR_URL = "https://fonts.gstatic.com/s/i/short-term/release/materialsymbolsrounded/radar/fill1/48px.svg"
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


def banner():
    width, height = 1280, 360
    letters, acid, right = wordmark_paths("CASTOR")
    lockup_width = right + 15.45  # same right margin as the AstraLabs lockup
    # Same letter size as the AstraLabs banner, unless the name needs to shrink to fit.
    scale = min(600 / 904.68, (width - 2 * 120) / lockup_width)
    x = (width - lockup_width * scale) / 2
    y = (height - 105 * scale) / 2 - 8
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="Castor">\n'
        + frame(width, height)
        + f'<g transform="translate({num(x)} {num(y)}) scale({scale:.5f})">\n'
        f'<g transform="scale(0.20207)" fill="{MARK}">{MARK_PATHS}</g>\n'
        f'<path fill="{MARK}" d="{letters}"/>\n'
        f'<path fill="{ACID}" d="{acid}"/>\n'
        "</g>\n"
        + label("Kanban board / workflow orchestrator", 48, 334, size=15)
        + "\n"
        + label("MIT", width - 48, 334, size=15, anchor="end")
        + "\n</svg>\n"
    )


def arrow(x1, y1, x2, y2, color, width=1.0, heads=(False, True)):
    """A line with 60-degree heads (the brand angle)."""
    out = [
        f'<path d="M{num(x1)} {num(y1)} L{num(x2)} {num(y2)}" stroke="{color}" '
        f'stroke-width="{width}" fill="none"/>'
    ]
    angle = math.atan2(y2 - y1, x2 - x1)
    for at_start, (px, py, a) in zip(heads, ((x1, y1, angle + math.pi), (x2, y2, angle))):
        if not at_start:
            continue
        length = 9
        points = []
        for side in (-1, 1):
            b = a + math.pi - side * math.pi / 6
            points.append((px + length * math.cos(b), py + length * math.sin(b)))
        (ax, ay), (bx, by) = points
        out.append(
            f'<path d="M{num(ax)} {num(ay)} L{num(px)} {num(py)} L{num(bx)} {num(by)}" '
            f'stroke="{color}" stroke-width="{width}" fill="none"/>'
        )
    return "\n".join(out)


def box(x, y, w, h, title, sub=None, dashed=False, stroke=BONE, title_fill=BONE):
    """A component box: an uppercase title and an optional ash subtitle."""
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    out = [
        f'<rect x="{num(x + .5)}" y="{num(y + .5)}" width="{num(w - 1)}" height="{num(h - 1)}" '
        f'fill="{VOID}" stroke="{stroke}" stroke-opacity=".8"{dash}/>'
    ]
    cx = x + w / 2
    if sub:
        out.append(f'<path fill="{title_fill}" d="{DATA.text(title.upper(), 14, 0.06, cx, y + h / 2 - 2, "middle")}"/>')
        out.append(f'<path fill="{ASH}" d="{DATA.text(sub, 11, 0.02, cx, y + h / 2 + 15, "middle")}"/>')
    else:
        out.append(f'<path fill="{title_fill}" d="{DATA.text(title.upper(), 14, 0.06, cx, y + h / 2 + 5, "middle")}"/>')
    return "\n".join(out)


def architecture():
    """Browser, server and agents, and what flows between them."""
    width, height = 1280, 460
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="Castor architecture: '
        'the browser talks to the server over HTTP and a WebSocket; agents connect to the '
        'server over a WebSocket and run jobs in containers or the host shell.">',
        frame(width, height),
    ]
    top, bottom = 96, 412
    panel_w = 320
    panels = (
        (72, "Browser", "SvelteKit",
         (("Board", "cards, columns"), ("Playbooks", "graph, runs"), ("Live output", "logs, results"))),
        (480, "Server", "FastAPI",
         (("API + events", "REST, /ws/ui"), ("Dispatcher", "jobs to agents"), ("Parsers", "outputs, transforms"))),
        (888, "Agents", "any number",
         (("Job runner", "/ws/agent"), ("Containers", "podman, docker"), ("Host shell", "--allow-host"))),
    )
    rows_y = (124, 220, 316)
    row_h = 64
    for x, title, sub, rows in panels:
        out.append(
            f'<rect x="{x + .5}" y="{top + .5}" width="{panel_w - 1}" height="{bottom - top - 1}" '
            f'fill="{PLATE}" stroke="{RULE}"/>'
        )
        out.append(label(title, x, top - 14, fill=BONE))
        out.append(label(sub, x + panel_w, top - 14, anchor="end"))
        for (name, detail), y in zip(rows, rows_y):
            out.append(box(x + 32, y, panel_w - 64, row_h, name, detail))
    # Links between the panels, at the level of the rows they connect.
    a_y = rows_y[0] + row_h / 2
    out.append(arrow(392 + 8, a_y, 480 - 8, a_y, BONE, 1.2, heads=(True, True)))
    out.append(label("HTTP + WS", 436, a_y - 14, fill=BONE, anchor="middle", size=11))
    b_y = rows_y[1] + row_h / 2
    out.append(arrow(800 + 8, b_y, 888 - 8, b_y, ACID, 1.5, heads=(True, True)))
    out.append(label("job.run", 844, b_y - 14, fill=BONE, anchor="middle", size=11))
    out.append(label("logs, files", 844, b_y + 26, anchor="middle", size=11))
    out.append("</svg>\n")
    return "\n".join(out)


def link(x1, y1, x2, y2, color=BONE, width=1.2, opacity=".7"):
    """An edge from a node's right side to another's left side; level changes use the
    brand's 60-degree angle."""
    if abs(y1 - y2) < 0.5:
        d = f"M{num(x1)} {num(y1)} H{num(x2)}"
    else:
        run = abs(y2 - y1) / math.sqrt(3)  # horizontal run of a 60-degree diagonal
        bend = max(x1 + 6, x2 - 12 - run)
        d = f"M{num(x1)} {num(y1)} H{num(bend)} L{num(bend + run)} {num(y2)} H{num(x2)}"
    head = []
    for side in (-1, 1):
        b = math.pi - side * math.pi / 6
        head.append((x2 + 8 * math.cos(b), y2 + 8 * math.sin(b)))
    (ax, ay), (bx, by) = head
    return (
        f'<path d="{d}" fill="none" stroke="{color}" stroke-opacity="{opacity}" stroke-width="{width}"/>\n'
        f'<path d="M{num(ax)} {num(ay)} L{num(x2)} {num(y2)} L{num(bx)} {num(by)}" fill="none" '
        f'stroke="{color}" stroke-opacity="{opacity}" stroke-width="{width}"/>'
    )


def playbook():
    """The pipeline-demo playbook as a graph: commands, transforms, fan-out and fan-in."""
    width, height = 1280, 440
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="A playbook graph: one seed '
        'step fans out into three parallel branches, transform steps reshape the data between '
        'commands, and a report step collects all branches.">',
        frame(width, height),
    ]
    # Sized so the longest title fits and a 60-degree edge fits between columns.
    node_w, node_h = 148, 50
    cols = [56 + i * ((width - 112 - node_w) / 5) for i in range(6)]
    rows = (160, 232, 304)  # node centre lines
    nodes = {
        "seed": (0, 1, "Seed hosts", "nmap.hosts", False),
        "svc": (1, 0, "Service scan", "nmap.services", False),
        "smb": (1, 1, "SMB scan", "nmap.hosts", False),
        "dns": (1, 2, "DNS names", "lines", False),
        "web": (2, 0, "Web targets", "filter / format", True),
        "audit": (2, 1, "SMB audit", "netexec.smb", False),
        "dir": (3, 0, "Dir brute", "ffuf.results", False),
        "unsigned": (3, 1, "Signing off", "filter / pluck", True),
        "interesting": (4, 0, "Interesting", "filter / pluck", True),
        "report": (5, 1, "Report", "text", False),
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
        return (cols[col] + (node_w if side == "out" else 0), rows[row])

    for a, b in edges:
        (x1, y1), (x2, y2) = anchor(a, "out"), anchor(b, "in")
        transform_edge = nodes[a][4] or nodes[b][4]
        out.append(link(x1, y1, x2, y2, ACID if transform_edge else BONE, 1.4 if transform_edge else 1.2,
                        "1" if transform_edge else ".6"))
    for col, row, name, sub, transform in nodes.values():
        out.append(box(cols[col], rows[row] - node_h / 2, node_w, node_h, name, sub,
                       dashed=transform, stroke=ACID if transform else BONE))

    # Legend, top left; caption, bottom.
    out.append(f'<rect x="48.5" y="54.5" width="23" height="13" fill="{VOID}" stroke="{BONE}" stroke-opacity=".8"/>')
    out.append(label("Command: a card, run by an agent", 82, 66, fill=BONE, size=12))
    out.append(f'<rect x="420.5" y="54.5" width="23" height="13" fill="{VOID}" stroke="{ACID}" stroke-dasharray="6 4"/>')
    out.append(label("Transform: runs on the server", 454, 66, fill=BONE, size=12))
    out.append(label("an example playbook", 48, 416, size=13))
    out.append(label("parallel branches / fan-in", width - 48, 416, size=13, anchor="end"))
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
        "architecture.svg": architecture(),
        "playbook.svg": playbook(),
        "footer.svg": footer(),
    }
    for name, svg in outputs.items():
        (HERE / name).write_text(svg)
        print(f"wrote {name} ({len(svg)} bytes)")


if __name__ == "__main__":
    main()
