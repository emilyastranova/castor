"""Sign-on buttons: branding, logos, order, and going straight to a provider."""

from __future__ import annotations

from castor_server.sso_branding import contrast_text

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 24"><script>alert(1)</script><rect width="120" height="24"/></svg>'


def _provider(client, auth, pid: str, name: str, **extra) -> dict:
    body = {"id": pid, "name": name, "issuer": f"https://{pid}.example.com", "client_id": "castor", **extra}
    r = client.post("/api/sso-providers", json=body, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def test_contrast():
    assert contrast_text("#ffcc00") == "#000000" and contrast_text("#1a237e") == "#ffffff"


def test_branding_logos_order_and_redirect(client, auth):
    a = _provider(client, auth, "brand-a", "Acme Entra", branding={"label": "Sign in with Acme", "style": "filled", "color": "#2F6FEB",
                                                                   "logo": "wordmark"})
    assert a["branding"]["color"] == "#2f6feb" and a["branding"]["has_logo"] is False
    _provider(client, auth, "brand-b", "Okta")
    bad = client.post("/api/sso-providers", json={"id": "brand-c", "name": "C", "issuer": "https://c.example.com", "client_id": "x",
                                                    "branding": {"color": "blue"}}, headers=auth)
    assert bad.status_code == 422

    # A wordmark chosen but not uploaded yet: the page shows the symbol instead.
    opts = {p["id"]: p for p in client.get("/api/auth/sso").json()["providers"]}
    assert opts["brand-a"]["logo"] == "symbol" and opts["brand-a"]["text_color"] == "#ffffff"
    assert opts["brand-a"]["label"] == "Sign in with Acme"

    r = client.put("/api/sso-providers/brand-a/logo", files={"file": ("acme.svg", SVG, "image/svg+xml")}, headers=auth)
    assert r.status_code == 200, r.text and r.json()["branding"]["has_logo"]
    assert client.put("/api/sso-providers/brand-a/logo", files={"file": ("x.txt", b"hello", "text/plain")}, headers=auth).status_code == 422
    opts = {p["id"]: p for p in client.get("/api/auth/sso").json()["providers"]}
    assert opts["brand-a"]["logo"] == "wordmark" and opts["brand-a"]["logo_url"].startswith("/api/auth/sso/brand-a/logo")
    logo = client.get(opts["brand-a"]["logo_url"])  # public: no sign-in needed
    assert logo.status_code == 200 and logo.headers["content-type"].startswith("image/svg+xml")
    assert "sandbox" in logo.headers["content-security-policy"] and "default-src 'none'" in logo.headers["content-security-policy"]
    assert client.get("/api/auth/sso/brand-b/logo").status_code == 404

    # Editing other fields keeps the logo.
    r = client.put("/api/sso-providers/brand-a", json={**{k: a[k] for k in ("id", "name", "issuer", "client_id")},
                                                       "branding": {"style": "outlined", "color": "#2f6feb", "logo": "wordmark"}}, headers=auth)
    assert r.json()["branding"]["has_logo"] and r.json()["branding"]["style"] == "outlined"

    # Order.
    assert client.post("/api/sso-providers/order", json={"ids": ["brand-b", "brand-a"]}, headers=auth).status_code == 204
    ids = [p["id"] for p in client.get("/api/auth/sso").json()["providers"]]
    assert ids.index("brand-b") < ids.index("brand-a")

    # Straight to a provider: only one that's on; deleting it stops going there.
    assert client.patch("/api/settings", json={"sso_redirect": "nope"}, headers=auth).status_code == 422
    r = client.patch("/api/settings", json={"sso_redirect": "brand-a", "sign_in_message": "Use your Acme account", "password_form": "collapsed"},
                     headers=auth)
    assert r.status_code == 200, r.text
    o = client.get("/api/auth/sso").json()
    assert o["redirect"] == "brand-a" and o["message"] == "Use your Acme account" and o["password_form"] == "collapsed"
    assert client.delete("/api/sso-providers/brand-a", headers=auth).status_code == 204
    assert client.get("/api/auth/sso").json()["redirect"] is None
    client.delete("/api/sso-providers/brand-b", headers=auth)
    client.patch("/api/settings", json={"sign_in_message": "", "password_form": "shown"}, headers=auth)
