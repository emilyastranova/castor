"""Company branding: the name and logos (public), client logos, and exports that carry them."""

import base64
import uuid

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 10"><rect width="40" height="10"/></svg>'


def _user(client, auth, role="operator"):
    name = f"br{uuid.uuid4().hex[:8]}"
    client.post("/api/users", json={"username": name, "role": role, "password": "a password"}, headers=auth)
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    return {"Authorization": f"Bearer {token}"}


def test_the_company_name_and_logos(client, auth):
    client.cookies.clear()
    out = client.get("/api/branding").json()  # public: the sign-in page reads it
    assert set(out) >= {"name", "tagline", "powered_by", "mark_url", "wordmark_url", "wordmark_dark_url"}

    r = client.patch("/api/settings", json={"brand_name": "  Acme   Red Team ", "brand_tagline": "Offensive security",
                                            "report_footer": "Confidential & privileged"}, headers=auth)
    assert r.status_code == 200, r.text
    assert client.get("/api/branding").json()["name"] == "Acme Red Team"
    assert client.patch("/api/settings", json={"branding_logos": {"mark": "../../etc/passwd"}}, headers=auth).status_code == 422

    # Admins upload logos; others can't; anything but an image is refused.
    operator = _user(client, auth)
    assert client.put("/api/branding/logo/mark", files={"file": ("m.png", PNG, "image/png")}, headers=operator).status_code == 403
    assert client.put("/api/branding/logo/mark", files={"file": ("m.txt", b"hello", "text/plain")}, headers=auth).status_code == 422
    r = client.put("/api/branding/logo/mark", files={"file": ("m.png", PNG, "image/png")}, headers=auth)
    assert r.status_code == 200, r.text
    url = r.json()["mark_url"]
    assert url.startswith("/api/branding/logo/mark?v=") and r.json()["mark_type"] == "image/png"
    client.cookies.clear()
    logo = client.get(url)
    assert logo.status_code == 200 and logo.content == PNG and "default-src 'none'" in logo.headers["content-security-policy"]

    # A new upload replaces the old file; removing it forgets it.
    again = client.put("/api/branding/logo/mark", files={"file": ("m.svg", SVG, "image/svg+xml")}, headers=auth).json()
    assert again["mark_url"] != url and again["mark_type"] == "image/svg+xml"
    assert client.delete("/api/branding/logo/mark", headers=auth).json()["mark_url"] is None
    assert client.get("/api/branding/logo/mark").status_code == 404
    client.patch("/api/settings", json={"brand_name": "", "brand_tagline": "", "report_footer": ""}, headers=auth)


def test_exports_carry_the_company_and_client_logos(client, auth):
    acme = client.post("/api/clients", json={"name": f"Client {uuid.uuid4().hex[:5]}"}, headers=auth).json()
    r = client.put(f"/api/clients/{acme['id']}/logo", files={"file": ("c.png", PNG, "image/png")}, headers=auth)
    assert r.status_code == 200 and r.json()["logo_url"].startswith(f"/api/clients/{acme['id']}/logo?v=")
    assert r.json()["logo_dark_url"] is None
    client.cookies.clear()
    assert client.get(f"/api/clients/{acme['id']}/logo").status_code == 401  # a client's logo isn't public
    assert client.get(f"/api/clients/{acme['id']}/logo", headers=auth).content == PNG

    client.put("/api/branding/logo/wordmark", files={"file": ("w.svg", SVG, "image/svg+xml")}, headers=auth)
    client.patch("/api/settings", json={"brand_name": "Acme Red Team", "report_footer": 'Confidential & "privileged" </style>'},
                 headers=auth)
    code = f"BR-{uuid.uuid4().hex[:5]}"
    project = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal", "client_id": acme["id"]},
                          headers=auth).json()
    html = client.get(f"/api/projects/{project['id']}/timeline/export", params={"format": "html"}, headers=auth).text
    assert 'class="wordmark" src="data:image/svg+xml;base64,' in html
    assert 'class="client-logo" src="data:image/png;base64,' in html
    assert "from Acme Red Team (Castor)" in html
    assert "Confidential & 'privileged' ‹/style›" in html  # the page footer, kept inside its CSS string
    client.delete("/api/branding/logo/wordmark", headers=auth)
    client.patch("/api/settings", json={"brand_name": "", "brand_tagline": "", "report_footer": ""}, headers=auth)
