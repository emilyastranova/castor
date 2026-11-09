"""Record the README's showcase GIFs from a seeded demo instance (see README.md in this folder).

Same setup as capture.py: the demo API and UI, a host agent, seed_demo.py, Playwright in a
throwaway venv. Needs ffmpeg.

    /tmp/pw/bin/python .github/assets/screenshots/showcase.py http://localhost:5174 [scene ...]

Each scene drives the real UI with a visible cursor while Chromium's screencast captures frames
at 2x; the frames are timed, downscaled and turned into a GIF with a palette made for that GIF.
Scenes make the cards they need through the API, so they can be re-run on the same instance.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

WEB = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5174").rstrip("/")
ONLY = set(sys.argv[2:])
OUT = Path(__file__).resolve().parents[1] / "showcase"
PROJECT = "NWL26-001"
SIZE = {"width": 1200, "height": 750}
FPS = 12
COLORS = 128  # the UI's flat colours fit; fewer keeps the files small

# A cursor the screencast can see (headless Chromium draws none): an arrow that follows the
# mouse, dips while the button is held, and leaves a ring where it clicks.
CURSOR = """
(() => {
  const install = () => {
    if (document.getElementById('__cursor')) return;
    const c = document.createElement('div');
    c.id = '__cursor';
    c.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24"><path d="M5 2.5v17.2l4.6-4.4 2.9 6.6 2.9-1.3-2.9-6.5h6.4z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    Object.assign(c.style, {position: 'fixed', left: '-40px', top: '-40px', zIndex: 2147483647,
      pointerEvents: 'none', transition: 'transform 120ms ease', transformOrigin: '5px 3px',
      filter: 'drop-shadow(0 2px 3px rgba(0,0,0,.45))'});
    // An open <dialog> sits in the top layer, above any z-index: the cursor goes inside the
    // topmost one so it stays visible over it.
    const host = () => [...document.querySelectorAll('dialog[open]')].pop() || document.documentElement;
    const lift = (el) => { const h = host(); if (el.parentNode !== h) h.appendChild(el); };
    lift(c);
    addEventListener('mousemove', (e) => { lift(c); c.style.left = (e.clientX - 5) + 'px'; c.style.top = (e.clientY - 3) + 'px'; }, true);
    addEventListener('mousedown', (e) => {
      c.style.transform = 'scale(.85)';
      const r = document.createElement('div');
      Object.assign(r.style, {position: 'fixed', left: (e.clientX - 18) + 'px', top: (e.clientY - 18) + 'px',
        width: '36px', height: '36px', borderRadius: '50%', border: '2px solid rgba(255,255,255,.85)',
        zIndex: 2147483646, pointerEvents: 'none', transition: 'transform 450ms ease-out, opacity 450ms ease-out'});
      host().appendChild(r);
      requestAnimationFrame(() => { r.style.transform = 'scale(1.8)'; r.style.opacity = '0'; });
      setTimeout(() => r.remove(), 500);
    }, true);
    addEventListener('mouseup', () => { c.style.transform = ''; }, true);
  };
  if (document.readyState === 'loading') addEventListener('DOMContentLoaded', install); else install();
})();
"""


class Recorder:
    """Chromium's screencast at 2x: each frame is kept with the time it was painted."""

    def __init__(self, page: Page):
        self.page = page
        self.cdp = page.context.new_cdp_session(page)
        self.dir = Path(tempfile.mkdtemp(prefix="castor-gif-"))
        self.frames: list[tuple[float, Path]] = []
        self.cdp.on("Page.screencastFrame", self._frame)

    def _frame(self, ev) -> None:
        path = self.dir / f"{len(self.frames):05d}.png"
        path.write_bytes(base64.b64decode(ev["data"]))
        self.frames.append((time.monotonic(), path))
        self.cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})

    def start(self) -> None:
        self.cdp.send("Page.startScreencast", {"format": "png", "everyNthFrame": 1,
                                               "maxWidth": SIZE["width"] * 2, "maxHeight": SIZE["height"] * 2})
        self.page.wait_for_timeout(300)
        self.t0 = time.monotonic()

    def stop(self) -> None:
        self.page.wait_for_timeout(200)
        self.end = time.monotonic()
        self.cdp.send("Page.stopScreencast")

    def gif(self, name: str) -> None:
        """Frames → constant-rate video → a palette made for this GIF → the GIF."""
        frames = [(t, p) for t, p in self.frames if t >= self.t0 - 0.5]
        if not frames:
            raise SystemExit(f"{name}: no frames")
        lines = []
        for i, (t, p) in enumerate(frames):
            nxt = frames[i + 1][0] if i + 1 < len(frames) else self.end
            lines += [f"file '{p}'", f"duration {max(nxt - max(t, self.t0), 0.001):.3f}"]
        lines.append(f"file '{frames[-1][1]}'")
        concat = self.dir / "frames.txt"
        concat.write_text("\n".join(lines) + "\n")
        OUT.mkdir(exist_ok=True)
        scale = f"fps={FPS},scale={SIZE['width']}:-1:flags=lanczos"
        palette = self.dir / "palette.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", concat,
                        "-vf", f"{scale},palettegen=max_colors={COLORS}:stats_mode=diff", palette], check=True)
        target = OUT / f"{name}.gif"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", concat, "-i", palette,
                        "-lavfi", f"{scale}[v];[v][1:v]paletteuse=dither=none:diff_mode=rectangle",
                        "-loop", "0", target], check=True)
        print(f"wrote {target.name}: {target.stat().st_size / 1e6:.1f} MB, {self.end - self.t0:.1f}s")
        if not os.environ.get("KEEP_FRAMES"):
            shutil.rmtree(self.dir, ignore_errors=True)
        else:
            print("frames kept in", self.dir)


# ----- moving like a person -----

_pos = [SIZE["width"] / 2, SIZE["height"] - 60]


def glide(page: Page, x: float, y: float, ms: int = 650) -> None:
    """Ease the mouse to (x, y) along a slight curve."""
    x0, y0 = _pos
    steps = max(8, ms // 16)
    bend = (x - x0) * 0.08, (y - y0) * -0.08
    for i in range(1, steps + 1):
        t = i / steps
        e = 0.5 - math.cos(math.pi * t) / 2
        arc = math.sin(math.pi * t)
        page.mouse.move(x0 + (x - x0) * e + bend[1] * arc, y0 + (y - y0) * e + bend[0] * arc)
        page.wait_for_timeout(16)
    _pos[:] = [x, y]


def centre(page: Page, locator) -> tuple[float, float]:
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def click(page: Page, locator, ms: int = 650, pause: int = 350) -> None:
    glide(page, *centre(page, locator), ms)
    page.wait_for_timeout(120)
    page.mouse.down()
    page.wait_for_timeout(90)
    page.mouse.up()
    page.wait_for_timeout(pause)


def drag(page: Page, locator, x: float, y: float, ms: int = 1100) -> None:
    glide(page, *centre(page, locator), 600)
    page.wait_for_timeout(150)
    page.mouse.down()
    page.wait_for_timeout(120)
    glide(page, _pos[0] + 6, _pos[1] + 6, 120)  # get the drag going
    glide(page, x, y, ms)
    page.wait_for_timeout(250)
    page.mouse.up()


def type_slowly(page: Page, text: str, ms: int = 70) -> None:
    for ch in text:
        page.keyboard.type(ch)
        page.wait_for_timeout(ms)


# ----- the demo instance -----


def api(page: Page, method: str, path: str, body=None):
    r = page.request.fetch(f"{WEB}/api{path}", method=method, data=json.dumps(body) if body is not None else None,
                           headers={"content-type": "application/json"})
    if not r.ok:
        raise SystemExit(f"{method} {path}: {r.status} {r.text()}")
    return r.json() if r.text() else None


def login(page: Page) -> dict:
    page.goto(f"{WEB}/login")
    page.get_by_label("Username").fill("admin")
    page.get_by_label("Password").fill("castor")
    page.keyboard.press("Enter")
    page.wait_for_url(lambda url: "/login" not in url)
    return next(p for p in api(page, "GET", "/projects") if p["codename"] == PROJECT)


def board(page: Page, pid: str) -> dict:
    b = api(page, "GET", f"/projects/{pid}/board")
    b["by_role"] = {c["role"]: c for c in b["columns"]}
    return b


def tidy(page: Page) -> None:
    """Re-recording leaves a run card per take: keep only the newest run of each playbook."""
    newest: dict[str, dict] = {}
    for r in sorted(api(page, "GET", "/playbook-runs"), key=lambda r: r["created_at"] or "", reverse=True):
        if r["status"] in ("running", "pending") or not r.get("card_id"):
            continue
        if r["playbook_id"] in newest:
            page.request.delete(f"{WEB}/api/tasks/{r['card_id']}")
        else:
            newest[r["playbook_id"]] = r


def settle(page: Page, ms: int = 900) -> None:
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(ms)


# ----- scenes -----


LAB_SCAN = """id: lab-service-scan
name: Nmap service scan
schema_version: 1
description: Version-detect open TCP ports (demo agent; its nmap is demo-bin/nmap)
tags: [network, service, nmap]
args:
  - name: targets
    type: list
    description: Hosts or ranges to scan
    required: true
  - name: ports
    type: string
    default: "1-1000"
command: nmap -sV -p {{ ports }} -oX service-scan.xml {{ targets }}
outputs:
  - name: services
    type: list<service>
    parser: nmap.services
    source: /work/service-scan.xml
"""


def ensure_command(page: Page, yaml: str) -> None:
    cid = yaml.split("\n", 1)[0].split(":", 1)[1].strip()
    r = page.request.get(f"{WEB}/api/commands/{cid}")
    if r.ok:
        api(page, "PUT", f"/commands/{cid}", {"yaml": yaml})
    else:
        api(page, "POST", "/commands", {"yaml": yaml})


def fresh_card(page: Page, pid: str, title: str, **fields) -> dict:
    b = board(page, pid)
    for t in b["tasks"]:
        if t["title"] == title:
            api(page, "DELETE", f"/tasks/{t['id']}")
    column = b["by_role"][fields.pop("column", "todo")]["id"]
    return api(page, "POST", f"/projects/{pid}/tasks", {"title": title, "column_id": column, "position": 1, **fields})


def scroll_to(page: Page, locator, ms: int = 900) -> None:
    """Wheel the scroll area under the cursor until the locator's top is near the top third."""
    for _ in range(40):
        box = locator.bounding_box()
        if box is None or box["y"] < SIZE["height"] * 0.3:
            break
        page.mouse.wheel(0, min(140, box["y"] - SIZE["height"] * 0.25))
        page.wait_for_timeout(ms // 12)


def scene_run_card(page: Page, project: dict) -> None:
    """Drag a command card into In Progress: it runs on an agent, the console streams, and the
    results come back as a table."""
    pid = project["id"]
    ensure_command(page, LAB_SCAN)
    card = fresh_card(page, pid, "Service scan: 10.20.4.0/24", kind="command",
                      command_ref="lab-service-scan", command_args={"targets": ["10.20.4.0/24"]})
    page.goto(f"{WEB}/projects/{pid}")
    settle(page, 1200)
    rec = Recorder(page)
    rec.start()
    page.wait_for_timeout(800)
    src = page.locator(f'[data-card="{card["id"]}"]')
    active = page.locator("section", has=page.get_by_role("heading", name="In Progress", exact=True))
    ax, ay = centre(page, active.locator("h2"))
    drag(page, src, ax, ay + 90)
    page.wait_for_timeout(1300)
    click(page, src, pause=700)
    glide(page, SIZE["width"] * 0.35, SIZE["height"] * 0.6, 400)
    scroll_to(page, page.get_by_role("tab", name=re.compile("Output")))
    page.wait_for_timeout(300)
    done = page.get_by_text("Nmap done:")
    done.wait_for(timeout=20000)
    page.wait_for_timeout(1600)
    click(page, page.get_by_role("group", name="Run view").get_by_role("button", name=re.compile("Results")), pause=600)
    page.wait_for_timeout(600)
    row = page.locator("table tbody tr").nth(3)
    if row.count():
        glide(page, *centre(page, row), 700)
    page.wait_for_timeout(2400)
    rec.stop()
    rec.gif("run-a-card")


def showcase_recon(yaml: str) -> str:
    """The seeded Internal recon, with the fixture's test knobs (speed, fail_at, host count) fixed
    inside it, so the run dialog asks only for what a real playbook would."""
    yaml = re.sub(r"^inputs:\n(?:  .*\n|\n)+?(?=\S)", (
        "inputs:\n  - name: subnet\n    type: string\n    description: Network to sweep (first three octets)\n"
        "    default: 10.20.0\n\n"), yaml, count=1, flags=re.MULTILINE)
    yaml = yaml.replace('speed: "{{ inputs.speed }}"', 'speed: "0.7"')
    yaml = yaml.replace('fail_at: "{{ inputs.fail_at }}"', 'fail_at: "none"').replace('empty_at: "{{ inputs.empty_at }}"', 'empty_at: "none"')
    yaml = yaml.replace('count: "{{ inputs.host_count }}"', 'count: "8"')
    yaml = yaml.replace("Invents a run of live hosts", "Sweeps the subnet for live hosts")
    return yaml.replace(" (slowest branch)", "").replace(" (fastest branch)", "")


def node_span(page: Page) -> tuple[float, float]:
    """Left and right edges of the graph's nodes on screen."""
    return tuple(page.evaluate("""() => {
        const r = [...document.querySelectorAll('[data-step-id]')].map((n) => n.getBoundingClientRect())
            .filter((b) => b.width > 40);
        return [Math.min(...r.map((b) => b.left)), Math.max(...r.map((b) => b.right))];
    }"""))


def node_middle(page: Page) -> float:
    """The vertical middle of the graph's nodes on screen."""
    return page.evaluate("""() => {
        const r = [...document.querySelectorAll('[data-step-id]')].map((n) => n.getBoundingClientRect())
            .filter((b) => b.width > 40);
        return (Math.min(...r.map((b) => b.top)) + Math.max(...r.map((b) => b.bottom))) / 2;
    }""")


def pan(page: Page, dx: float, dy: float = 0, ms: int = 1300) -> None:
    """Drag the canvas from empty space near its top edge."""
    y = 70 if dy >= 0 else 70 - dy
    x = SIZE["width"] / 2 - dx / 2
    glide(page, x, y, 500)
    page.mouse.down()
    glide(page, x + dx, y + dy, ms)
    page.mouse.up()


def run_status(page: Page) -> dict:
    runs = api(page, "GET", "/playbook-runs")
    return next(r for r in runs if r["playbook_id"] == "internal-recon")


def scene_playbook(page: Page, project: dict) -> None:
    """Launch a playbook: its graph fills in live as steps run in parallel and results flow
    through the transforms between them; the view follows the run."""
    pb = api(page, "GET", "/playbooks/internal-recon")
    api(page, "PUT", "/playbooks/internal-recon", {"yaml": showcase_recon(pb["source_yaml"])})
    page.goto(f"{WEB}/projects/{project['id']}/dashboard")  # makes it the current project
    settle(page, 500)
    page.goto(f"{WEB}/playbooks")
    settle(page, 1000)
    rec = Recorder(page)
    rec.start()
    page.wait_for_timeout(700)
    click(page, page.get_by_role("heading", name="Internal recon", exact=True).first, pause=900)
    click(page, page.get_by_role("button", name=re.compile("Run Playbook")).last, pause=700)
    field = page.get_by_role("dialog").get_by_label("subnet")
    click(page, field, pause=200)
    page.keyboard.press("ControlOrMeta+a")
    type_slowly(page, "10.20.4", 90)
    page.wait_for_timeout(500)
    click(page, page.get_by_role("button", name=re.compile("Launch Playbook")), pause=700)
    click(page, page.get_by_role("button", name="Fullscreen").first, pause=350)
    seed = page.locator('[data-step-id="seed_hosts"]').first
    sx, sy = centre(page, seed)
    glide(page, sx - 30, sy, 450)
    for _ in range(5):  # scroll to zoom, around the first step
        page.mouse.wheel(0, -100)
        page.wait_for_timeout(70)

    def wait_for(step: str, states=("running", "succeeded", "failed", "skipped")) -> None:
        for _ in range(160):
            if run_status(page)["step_status"].get(step) in states:
                return
            page.wait_for_timeout(150)

    wait_for("web_targets", ("succeeded", "skipped", "failed"))  # the first branches are through
    page.wait_for_timeout(700)
    pan(page, -SIZE["width"] * 0.42, SIZE["height"] / 2 - node_middle(page))
    wait_for("report", ("running", "succeeded", "failed"))
    page.wait_for_timeout(300)
    _, right = node_span(page)
    pan(page, SIZE["width"] - 70 - right, ms=1000)
    glide(page, SIZE["width"] - 60, SIZE["height"] - 120, 600)
    for _ in range(120):
        if run_status(page)["status"] not in ("running", "pending"):
            break
        page.wait_for_timeout(250)
    page.wait_for_timeout(2600)
    rec.stop()
    rec.gif("playbook-run")


def wait_job(page: Page, task_id: str, timeout: float = 60) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        task = api(page, "GET", f"/tasks/{task_id}")
        job = task.get("latest_job") or {}
        if job.get("status") in ("succeeded", "failed", "lost", "cancelled"):
            page.wait_for_timeout(1500)  # parsing runs after job.finished
            return task
        page.wait_for_timeout(300)
    raise SystemExit(f"task {task_id} didn't finish")


def scene_filter_results(page: Page, project: dict) -> None:
    """Parsed results are tables you can query: hosts that don't require SMB signing, by name."""
    pid = project["id"]
    # Out of name order, so sorting by name visibly reorders them.
    targets = ",".join(f"10.20.8.{n}" for n in (23, 11, 29, 15, 17, 26, 12, 31, 20, 24, 14, 27, 18, 21))
    card = fresh_card(page, pid, "SMB signing audit: file servers", kind="command", command_ref="smb-audit",
                      command_args={"targets": targets, "delay": 1, "step": "file-servers"})
    b = board(page, pid)
    api(page, "POST", f"/tasks/{card['id']}/move", {"column_id": b["by_role"]["active"]["id"], "position": 1})
    wait_job(page, card["id"])
    page.goto(f"{WEB}/projects/{pid}?task={card['id']}")
    settle(page, 1200)
    glide(page, SIZE["width"] * 0.35, 175, 50)  # over the dialog's text, not the table's own scroll area
    tab = page.get_by_role("tab", name=re.compile("Output"))
    for _ in range(40):
        box = tab.bounding_box()
        if box is None or box["y"] < 140:
            break
        page.mouse.wheel(0, min(160, box["y"] - 120))
        page.wait_for_timeout(40)
    page.get_by_role("group", name="Run view").get_by_role("button", name=re.compile("Results")).click()
    settle(page, 900)
    rec = Recorder(page)
    rec.start()
    page.wait_for_timeout(1200)
    box = page.get_by_role("dialog").get_by_placeholder(re.compile("^Filter:")).first
    click(page, box, pause=300)
    type_slowly(page, "signing:false", 110)
    page.wait_for_timeout(1500)
    click(page, page.get_by_role("button", name=re.compile("NetBIOS name")).first, pause=1200)
    row = page.locator("table tbody tr").nth(2)
    glide(page, *centre(page, row), 800)
    page.wait_for_timeout(2600)
    rec.stop()
    rec.gif("filter-results")


def showcase_review(yaml: str) -> str:
    """The seeded SMB signing review with its test knobs fixed inside: the audit always fails,
    so the run stops for a decision, and the only input is the subnet."""
    yaml = re.sub(r"^inputs:\n(?:  .*\n|\n)+?(?=\S)", (
        "inputs:\n  - name: subnet\n    type: string\n    description: Network to review (first three octets)\n"
        "    default: 10.20.0\n\n"), yaml, count=1, flags=re.MULTILINE)
    for name, value in (("speed", "0.6"), ("fail_at", "smb_audit"), ("empty_at", "none"), ("signing", "mixed"), ("host_count", "8")):
        yaml = yaml.replace(f"{{{{ inputs.{name} }}}}", value)
    return yaml


def scene_decision(page: Page, project: dict) -> None:
    """A step stops for a person: choose what happens, and the playbook takes the branch that
    handles it."""
    pid = project["id"]
    pb = api(page, "GET", "/playbooks/smb-signing-review")
    api(page, "PUT", "/playbooks/smb-signing-review", {"yaml": showcase_review(pb["source_yaml"])})
    for r in api(page, "GET", "/playbook-runs"):  # one card asking, not several
        if r["playbook_id"] == "smb-signing-review":
            if r["status"] in ("running", "pending"):
                api(page, "POST", f"/playbook-runs/{r['id']}/cancel")
            if r.get("card_id"):
                page.request.delete(f"{WEB}/api/tasks/{r['card_id']}")
    run = api(page, "POST", "/playbooks/smb-signing-review/run", {"project_id": pid, "inputs": {"subnet": "10.20.8"}})
    for _ in range(200):
        run = api(page, "GET", f"/playbook-runs/{run['id']}")
        if run["step_status"].get("smb_audit") == "attention":
            break
        page.wait_for_timeout(250)
    page.goto(f"{WEB}/projects/{pid}")
    settle(page, 1500)
    rec = Recorder(page)
    rec.start()
    page.wait_for_timeout(800)
    click(page, page.locator(f'[data-card="{run["card_id"]}"]'), pause=1400)
    fail = page.get_by_role("dialog").locator("label", has_text="Fail it:")
    glide(page, *centre(page, fail), 700)
    page.wait_for_timeout(250)
    click(page, fail, ms=200, pause=500)
    click(page, page.get_by_role("dialog").get_by_label("Note (optional)"), pause=200)
    type_slowly(page, "Audit timed out on two hosts; use the fallback", 45)
    page.wait_for_timeout(400)
    click(page, page.get_by_role("button", name=re.compile("Apply decision")), pause=900)
    confirm = page.get_by_role("dialog").get_by_role("button", name=re.compile("^(close )?Fail$"))
    click(page, confirm.last, pause=600)
    # Down to the run's graph, wheeling over the sidebar (over the graph it would zoom it).
    # Down to the step list, which shows the branch taken, wheeling over the sidebar (over the
    # graph it would zoom it).
    glide(page, SIZE["width"] * 0.83, SIZE["height"] * 0.45, 600)
    steps = page.get_by_role("dialog").get_by_text("Steps", exact=True).first
    for _ in range(60):
        box = steps.bounding_box()
        if box is None or box["y"] < 170:
            break
        page.mouse.wheel(0, min(60, box["y"] - 150))
        page.wait_for_timeout(40)
    glide(page, SIZE["width"] * 0.62, SIZE["height"] * 0.85, 700)
    for _ in range(120):
        if api(page, "GET", f"/playbook-runs/{run['id']}")["status"] not in ("running", "pending"):
            break
        page.wait_for_timeout(250)
    page.wait_for_timeout(2800)
    rec.stop()
    rec.gif("answer-a-decision")


SCENES = {"run-a-card": scene_run_card, "playbook-run": scene_playbook, "filter-results": scene_filter_results,
          "answer-a-decision": scene_decision}


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, scene in SCENES.items():
            if ONLY and name not in ONLY:
                continue
            ctx = browser.new_context(viewport=SIZE, device_scale_factor=2, color_scheme="dark")
            ctx.add_init_script("localStorage.setItem('castor.theme', 'dark'); localStorage.setItem('castor.board.layout', 'board');")
            ctx.add_init_script(CURSOR)
            page = ctx.new_page()
            project = login(page)
            tidy(page)
            scene(page, project)
            ctx.close()
        browser.close()


if __name__ == "__main__":
    main()
