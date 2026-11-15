"""Clients: one per name (whatever its case), refused clearly rather than failing in the database."""

import uuid


def test_client_names_are_unique(client, auth):
    w = uuid.uuid4().hex[:6]
    made = client.post("/api/clients", json={"name": f"  Acme {w} "}, headers=auth)
    assert made.status_code == 201 and made.json()["name"] == f"Acme {w}"
    again = client.post("/api/clients", json={"name": f"ACME {w}"}, headers=auth)
    assert again.status_code == 409 and f"Acme {w}" in again.json()["detail"]
    assert client.post("/api/clients", json={"name": "   "}, headers=auth).status_code == 422

    other = client.post("/api/clients", json={"name": f"Globex {w}"}, headers=auth).json()
    taken = client.patch(f"/api/clients/{other['id']}", json={"name": f"acme {w}"}, headers=auth)
    assert taken.status_code == 409
    # Renaming a client to itself (another case) is fine.
    renamed = client.patch(f"/api/clients/{made.json()['id']}", json={"name": f"ACME {w}"}, headers=auth)
    assert renamed.status_code == 200 and renamed.json()["name"] == f"ACME {w}"
    for c in (made.json(), other):
        client.delete(f"/api/clients/{c['id']}", headers=auth)
