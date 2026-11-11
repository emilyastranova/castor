"""Themes (theme_spec.py, api/themes.py): the spec, what it compiles to, and the CSS it refuses."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from castor_server.theme_spec import check_css, compile_css, parse_theme_spec

THEMES = Path(__file__).resolve().parents[2] / "themes"

SMALL = """
id: test-dusk
name: Dusk
modes: both
colour: palette
colours:
  light: {primary: "#6750a4", surface: "#fffbfe"}
  dark: {primary: "#d0bcff", surface: "#1c1b1f", note-red: "#5a1d1d", blue: "#9ecaff"}
css: |
  .m3-btn { letter-spacing: 0.02em; }
"""


def test_every_builtin_theme_parses():
    files = sorted(THEMES.glob("*.yaml"))
    assert files
    for path in files:
        spec = parse_theme_spec(path.read_text())
        assert spec.id == path.stem
        assert f"html:root[data-skin='{spec.id}']" in compile_css(spec)


def test_compiles_into_its_own_scope():
    css = compile_css(parse_theme_spec(SMALL))
    scope = "html:root[data-skin='test-dusk']"
    assert f"{scope}[data-theme='dark'] {{" in css and "@media (prefers-color-scheme: dark)" in css
    assert "--md-sys-color-primary: #d0bcff;" in css and "--note-red: #5a1d1d;" in css
    assert "--md-custom-color-blue: #9ecaff;" in css
    assert f"{scope} {{\n.m3-btn" in css  # the extra CSS sits inside the scope


def test_accent_themes_take_the_theme_colour():
    spec = parse_theme_spec("id: acc\nname: Acc\nmodes: dark\ncolour: accent\ncolours:\n  dark: {primary: '#ff0066'}\n")
    css = compile_css(spec)
    assert "--md-sys-color-primary: var(--theme-accent, #ff0066);" in css
    assert "--theme-primary-hover: var(--theme-accent-hover, var(--md-sys-color-primary));" in css


@pytest.mark.parametrize("css,why", [
    ("} body { display: none }", "without its {"),
    (".a { color: red; ", "isn't closed"),
    ("@import url(https://evil.example/x.css);", "@import"),
    (".a { background: url(https://evil.example/leak?) }", "this server"),
    (".a { background: url(//evil.example/x) }", "this server"),
    (".a { width: expression(alert(1)) }", "script"),
    ("</style><script>alert(1)</script>", "</"),
    (".a { content: '\\7d' }", "backslash"),
    (".a { content: '}' } }", "without its {"),
])
def test_refuses_css_that_could_escape(css, why):
    with pytest.raises(ValueError, match=why):
        check_css(css)


def test_allows_ordinary_css():
    check_css(".a { background: url('/fonts/x.png'); content: '{'; }\n/* } */\n.b:hover { color: var(--x) }")


@pytest.mark.parametrize("yaml_text,why", [
    ("id: x1\nname: X\ncolours:\n  dark: {primary: 'red; } body {'}\n", "a colour"),
    ("id: x1\nname: X\ncolours:\n  dark: {primry: '#fff'}\n", "not a colour role"),
    ("id: x1\nname: X\nfonts: ['http://fonts.example/x.css']\n", "https://"),
    ("id: x1\nname: X\ntype: {body: 'Inter; } *{'}\n", "one CSS value"),
    ("id: x1\nname: X\nmodes: dark\ncolours:\n  light: {primary: '#fff'}\n", "dark-only"),
    ("id: X!\nname: X\n", "id"),
])
def test_refuses_bad_specs(yaml_text, why):
    with pytest.raises(ValueError, match=why):
        parse_theme_spec(yaml_text)


def test_custom_themes_are_for_admins_and_people_pick_them(client, auth):
    name = f"themer-{uuid.uuid4().hex[:6]}"
    assert client.post("/api/users", json={"username": name, "role": "operator", "password": "correct horse"}, headers=auth).status_code == 201
    token = client.post("/api/auth/login", json={"username": name, "password": "correct horse"}).json()["token"]
    client.cookies.clear()
    uh = {"Authorization": f"Bearer {token}"}
    listed = {t["id"]: t for t in client.get("/api/themes", headers=uh).json()}
    assert "astralabs" in listed and listed["astralabs"]["origin"] == "builtin" and listed["astralabs"]["css"]
    assert client.post("/api/themes", json={"yaml": SMALL}, headers=uh).status_code == 403
    r = client.post("/api/themes/validate", json={"yaml": SMALL}, headers=uh).json()
    assert r["ok"] and "test-dusk" in r["theme"]["css"]
    assert not client.post("/api/themes/validate", json={"yaml": "id: x\n"}, headers=uh).json()["ok"]

    assert client.post("/api/themes", json={"yaml": SMALL}, headers=auth).status_code == 201
    assert client.post("/api/themes", json={"yaml": SMALL}, headers=auth).status_code == 409
    assert client.patch("/api/auth/me", json={"theme": "test-dusk"}, headers=uh).json()["theme"] == "test-dusk"
    assert client.patch("/api/auth/me", json={"theme": "nope"}, headers=uh).status_code == 422
    assert client.get("/api/themes/test-dusk", headers=uh).json()["used_by"] == 1
    changed = SMALL.replace("name: Dusk", "name: Dusk 2")
    assert client.put("/api/themes/test-dusk", json={"yaml": changed}, headers=auth).json()["name"] == "Dusk 2"
    assert client.put("/api/themes/astralabs", json={"yaml": changed}, headers=auth).status_code == 409
    assert client.delete("/api/themes/astralabs", headers=auth).status_code == 409
    assert client.delete("/api/themes/test-dusk", headers=auth).status_code == 204
    assert client.get("/api/auth/me", headers=uh).json()["theme"] == ""  # back to Castor's own
