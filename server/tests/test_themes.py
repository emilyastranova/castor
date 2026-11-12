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
    assert client.get("/api/auth/me", headers=uh).json()["theme"] is None  # back to the instance default


def test_size_tokens():
    spec = parse_theme_spec("id: dense\nname: Dense\nsize: {spacing: 0.2rem, text: 0.9}\n")
    css = compile_css(spec)
    assert "--spacing: 0.2rem;" in css and "--md-sys-typescale-scale: 0.9;" in css
    assert "--spacing" not in compile_css(parse_theme_spec("id: plain\nname: Plain\n"))
    for bad in ("{spacing: 1rem}", "{spacing: 4em}", "{text: 3}"):
        with pytest.raises(ValueError, match="size"):
            parse_theme_spec(f"id: dense\nname: Dense\nsize: {bad}\n")


def test_contrast_check():
    from castor_server import theme_contrast as C

    # Castor's own colours here are the generated tokens.
    tokens = (Path(__file__).resolve().parents[2] / "web/src/lib/m3/tokens.css").read_text()
    for mode, block in (("light", tokens.split(":root[data-theme='dark']")[0]),
                        ("dark", tokens.split(":root[data-theme='dark']")[1].split("@media")[0])):
        for role, value in C.CASTOR[mode].items():
            assert f"--md-sys-color-{role}: {value};" in block, (mode, role)

    weak = parse_theme_spec("id: weak\nname: Weak\nmodes: light\ncolours:\n  light: {on-surface: '#999999', on-primary: '#ffffff', "
                            "primary: 'var(--md-sys-color-on-surface)', error: 'color-mix(in oklab, red, white)'}\n")
    found = {(w["text"], w["surface"]) for w in C.check(weak)}
    assert ("on-surface", "surface") in found and ("on-primary", "primary") in found  # a role pointing at another is followed
    assert not any(t == "error" for t, _ in found)  # color-mix: not worked out, so not reported
    assert C.check(weak)[0]["ratio"] <= C.check(weak)[-1]["ratio"]
    assert C.check(parse_theme_spec(SMALL)) == []
    # The built-ins pass, and High contrast is AAA (7:1) everywhere it sets.
    for path in sorted(THEMES.glob("*.yaml")):
        assert C.check(parse_theme_spec(path.read_text())) == [], path.name
    hc = parse_theme_spec((THEMES / "high-contrast.yaml").read_text())
    for mode, roles in (("light", hc.colours.light), ("dark", hc.colours.dark)):
        for text, grounds in C.PAIRS.items():
            for ground in grounds:
                assert C.ratio(C._rgb(roles[text], roles), C._rgb(roles[ground], roles)) >= 7, (mode, text, ground)


def test_instance_default_theme(client, auth):
    name = f"dflt-{uuid.uuid4().hex[:6]}"
    client.post("/api/users", json={"username": name, "role": "viewer", "password": "correct horse"}, headers=auth)
    token = client.post("/api/auth/login", json={"username": name, "password": "correct horse"}).json()["token"]
    client.cookies.clear()
    uh = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/auth/me", headers=uh).json()["theme"] is None  # never picked
    assert client.get("/api/themes/default").json()["id"] == ""  # public, Castor's own to start with

    assert client.patch("/api/settings", json={"default_theme": "nope"}, headers=auth).status_code == 422
    assert client.patch("/api/settings", json={"default_theme": "high-contrast"}, headers=uh).status_code == 403
    assert client.patch("/api/settings", json={"default_theme": "high-contrast"}, headers=auth).status_code == 200
    d = client.get("/api/themes/default").json()
    assert d["id"] == "high-contrast" and "data-skin='high-contrast'" in d["css"] and d["colour"] == "fixed"
    assert client.get("/api/settings", headers=uh).json()["default_theme"] == "high-contrast"

    # Picking Castor's own is a choice ("" ), and null goes back to the default.
    assert client.patch("/api/auth/me", json={"theme": ""}, headers=uh).json()["theme"] == ""
    assert client.patch("/api/auth/me", json={"display_name": "Still Castor"}, headers=uh).json()["theme"] == ""
    assert client.patch("/api/auth/me", json={"theme": None}, headers=uh).json()["theme"] is None

    # Deleting the default theme puts everyone back on Castor's own.
    custom = SMALL.replace("test-dusk", "test-default")
    assert client.post("/api/themes", json={"yaml": custom}, headers=auth).status_code == 201
    client.patch("/api/settings", json={"default_theme": "test-default"}, headers=auth)
    assert client.delete("/api/themes/test-default", headers=auth).status_code == 204
    assert client.get("/api/themes/default").json()["id"] == ""
    assert client.get("/api/settings", headers=auth).json()["default_theme"] == ""


def test_themes_travel_in_library_archives(client, auth):
    custom = SMALL.replace("test-dusk", "test-travel").replace("name: Dusk", "name: Travel")
    assert client.post("/api/themes", json={"yaml": custom}, headers=auth).status_code == 201
    data = client.post("/api/export", json={"themes": ["test-travel"]}, headers=auth)
    assert data.status_code == 200, data.text
    client.delete("/api/themes/test-travel", headers=auth)

    def preview(content, name, headers):
        return client.post("/api/import/preview", files={"file": (name, content, "application/octet-stream")}, headers=headers)

    # An operator can see it but not bring it in: themes are for admins.
    name = f"thop-{uuid.uuid4().hex[:6]}"
    client.post("/api/users", json={"username": name, "role": "operator", "password": "correct horse"}, headers=auth)
    token = client.post("/api/auth/login", json={"username": name, "password": "correct horse"}).json()["token"]
    client.cookies.clear()
    item = next(i for i in preview(data.content, "t.castor.zip", {"Authorization": f"Bearer {token}"}).json()["items"] if i["kind"] == "theme")
    assert item["choices"] == ["skip"] and "only admins" in item["notes"][0]

    p = preview(data.content, "t.castor.zip", auth).json()
    item = next(i for i in p["items"] if i["kind"] == "theme")
    assert item["id"] == "test-travel" and item["state"] == "new"
    r = client.post("/api/import", json={"token": p["token"], "filename": p["filename"]}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["imported"]["themes"] == ["Travel"]
    assert client.get("/api/themes/test-travel", headers=auth).json()["origin"] == "user"

    # A single theme .yaml imports the same way (here: the same again, so "same").
    p = preview(custom.encode(), "travel.yaml", auth).json()
    assert [(i["kind"], i["state"]) for i in p["items"]] == [("theme", "same")]
    client.delete("/api/themes/test-travel", headers=auth)
