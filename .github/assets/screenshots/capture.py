"""Take the README screenshots from a seeded demo instance (see seed_demo.py) with Playwright.

Playwright isn't a project dependency; use a throwaway venv:

    python3 -m venv /tmp/pw && /tmp/pw/bin/pip install playwright && /tmp/pw/bin/playwright install chromium
    /tmp/pw/bin/python .github/assets/screenshots/capture.py http://localhost:5174

Writes 1440x900 PNGs at 2x into this folder, plus phone.png (three 390x844 phone screens;
needs ImageMagick's `magick`).
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, sync_playwright

WEB = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5174").rstrip("/")
OUT = Path(__file__).resolve().parent
PROJECT = "NWL26-001"


def daytime_zone() -> str:
    """A timezone where it's early afternoon right now, so Home doesn't greet with "Working late"."""
    now = datetime.now(UTC)
    for name in ("Europe/London", "America/New_York", "America/Los_Angeles", "Asia/Tokyo", "Australia/Sydney",
                 "Asia/Kolkata", "Europe/Berlin", "America/Chicago", "Asia/Singapore", "Pacific/Auckland",
                 "America/Sao_Paulo", "Asia/Dubai", "Pacific/Honolulu"):
        if 10 <= now.astimezone(ZoneInfo(name)).hour < 16:
            return name
    return "UTC"


TIMEZONE = sys.argv[2] if len(sys.argv) > 2 else daytime_zone()


def settle(page: Page, ms: int = 900) -> None:
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(ms)  # fonts, icons, realtime refreshes


def shot(page: Page, name: str, clip: dict | None = None) -> None:
    page.screenshot(path=OUT / f"{name}.png", clip=clip)
    print("wrote", name)


def login(page: Page) -> str:
    """Sign in and make the demo project current (opening its Home does that). Returns its id."""
    page.goto(f"{WEB}/login")
    page.get_by_label("Username").fill("admin")
    page.get_by_label("Password").fill("castor")
    page.keyboard.press("Enter")
    page.wait_for_url(lambda url: "/login" not in url)
    projects = page.request.get(f"{WEB}/api/projects").json()
    return next(p for p in projects if p["codename"] == PROJECT)["id"]


def phone(browser, pid: str) -> None:
    """Three phone screens (Home, the board, the More sheet) side by side in one image."""
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, color_scheme="dark",
                              timezone_id=TIMEZONE, is_mobile=True, has_touch=True)
    ctx.add_init_script("localStorage.setItem('castor.theme', 'dark')")
    page = ctx.new_page()
    login(page)
    parts = []
    page.goto(f"{WEB}/projects/{pid}/dashboard")
    settle(page, 1500)
    parts.append(OUT / "_phone-home.png")
    page.screenshot(path=parts[-1])
    page.goto(f"{WEB}/projects/{pid}")
    settle(page)
    parts.append(OUT / "_phone-board.png")
    page.screenshot(path=parts[-1])
    page.get_by_role("button", name="More").click()
    page.wait_for_timeout(600)
    parts.append(OUT / "_phone-more.png")
    page.screenshot(path=parts[-1])
    ctx.close()
    # One image, the screens 48px apart on a transparent background (ImageMagick).
    subprocess.run(["magick", *map(str, parts), "-background", "none", "-splice", "48x0", "+append", "-chop", "48x0",
                    str(OUT / "phone.png")], check=True)
    for part in parts:
        part.unlink()
    print("wrote phone")


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for theme in ("dark", "light"):
            # A daytime timezone, so the greeting isn't "Working late" when they're taken at night
            # (relative times like "4m ago" don't depend on it).
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 900}, device_scale_factor=2, color_scheme=theme, timezone_id=TIMEZONE
            )
            ctx.add_init_script(f"localStorage.setItem('castor.theme', '{theme}')")
            page = ctx.new_page()
            pid = login(page)

            if theme == "dark":
                page.goto(f"{WEB}/projects/{pid}/dashboard")
                settle(page, 1500)
                shot(page, "dashboard")

                page.goto(f"{WEB}/projects/{pid}")
                settle(page)
                shot(page, "board")

                # A playbook run's card, open on its Run tab (progress, decision, graph, steps).
                board = page.request.get(f"{WEB}/api/projects/{pid}/board").json()
                run_card = next(t for t in board["tasks"] if t.get("run") and t["run"]["playbook_id"] == "smb-signing-review")
                page.goto(f"{WEB}/projects/{pid}?task={run_card['id']}")
                settle(page, 1500)
                shot(page, "card")

                runs = page.request.get(f"{WEB}/api/playbook-runs").json()
                run = next(r for r in runs if r["playbook_id"] == "internal-recon")
                page.goto(f"{WEB}/playbooks?run={run['id']}")
                settle(page, 1500)
                page.get_by_role("button", name="Fullscreen").click()
                page.wait_for_timeout(500)
                page.get_by_role("button", name="Fit View").click()
                settle(page, 800)
                # The graph is wide: keep the band it occupies, plus some room around it.
                box = page.evaluate(
                    """() => {
                        const nodes = [...document.querySelectorAll('[data-step-id]')]
                            .map((n) => n.getBoundingClientRect()).filter((r) => r.width > 40 && r.height > 30);
                        if (!nodes.length) return null;
                        return { top: Math.min(...nodes.map((r) => r.top)), bottom: Math.max(...nodes.map((r) => r.bottom)) };
                    }"""
                )
                top, bottom = (box["top"], box["bottom"]) if box else (290, 610)
                shot(page, "playbook", {"x": 0, "y": max(0, top - 60), "width": 1440, "height": min(900, bottom - top + 120)})
            else:
                page.goto(f"{WEB}/projects/{pid}/assets")
                settle(page, 1500)
                shot(page, "assets")
            ctx.close()
        phone(browser, pid)
        browser.close()


if __name__ == "__main__":
    main()
