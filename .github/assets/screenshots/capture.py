"""Take the README screenshots from a seeded demo instance (see seed_demo.py) with Playwright.

Playwright isn't a project dependency; use a throwaway venv:

    python3 -m venv /tmp/pw && /tmp/pw/bin/pip install playwright && /tmp/pw/bin/playwright install chromium
    /tmp/pw/bin/python .github/assets/screenshots/capture.py http://localhost:5174

Writes 1440x900 PNGs at 2x into this folder.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

WEB = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5174").rstrip("/")
OUT = Path(__file__).resolve().parent
PROJECT = "NWL26-001"
TIMEZONE = sys.argv[2] if len(sys.argv) > 2 else "Africa/Nairobi"


def settle(page: Page, ms: int = 900) -> None:
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(ms)  # fonts, icons, realtime refreshes


def shot(page: Page, name: str, clip: dict | None = None) -> None:
    page.screenshot(path=OUT / f"{name}.png", clip=clip)
    print("wrote", name)


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
            page.goto(f"{WEB}/login")
            page.get_by_label("Username").fill("admin")
            page.get_by_label("Password").fill("castor")
            page.keyboard.press("Enter")
            page.wait_for_url(f"{WEB}/")
            projects = page.request.get(f"{WEB}/api/projects").json()
            project = next(p for p in projects if p["codename"] == PROJECT)
            pid = project["id"]

            if theme == "dark":
                settle(page, 1500)
                shot(page, "dashboard")

                page.goto(f"{WEB}/projects/{pid}")
                settle(page)
                shot(page, "board")

                board = page.request.get(f"{WEB}/api/projects/{pid}/board").json()
                audit = next(t for t in board["tasks"] if t["title"].endswith("SMB signing audit"))
                page.goto(f"{WEB}/projects/{pid}?task={audit['id']}")
                settle(page)
                page.locator("dialog[open]").get_by_text("Comments", exact=False).first.click()
                settle(page, 600)
                shot(page, "card")

                runs = page.request.get(f"{WEB}/api/playbook-runs").json()
                run = next(r for r in runs if r["playbook_id"] == "pipeline-demo")
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
                page.goto(f"{WEB}/projects/{pid}/dashboard")
                settle(page, 1500)
                shot(page, "project-dashboard")
            ctx.close()
        browser.close()


if __name__ == "__main__":
    main()
