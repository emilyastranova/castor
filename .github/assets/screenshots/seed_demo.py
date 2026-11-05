"""Fill a fresh Castor instance with a small fictional engagement for the README screenshots.

Run it against a throwaway instance, never a real one (it creates users with a known
password). From the repo root:

    # .claude/launch.json "demo-api" (API on :8100, data in .demo/) and "demo-web" (UI on :5174)
    .venv/bin/castor-agent --server ws://localhost:8100/ws/agent --secret demo-agent-secret \\
        --name lab-agent-01 --tags lab --allow-host --max-concurrency 4 --workdir /tmp/castor-demo-agent
    python3 .github/assets/screenshots/seed_demo.py http://localhost:8100

Then sign in to http://localhost:5174 as admin / castor and take the screenshots (see
README.md in this folder). Stdlib only.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

API = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8100").rstrip("/")
PASSWORD = "demo-password"  # the demo users' password (throwaway instance only)


def call(method: str, path: str, token: str | None = None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}/api{path}", data=data, method=method)
    req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as err:
        raise SystemExit(f"{method} {path}: {err.code} {err.read().decode()[:300]}") from None


# Illustrated portraits (DiceBear "Lorelei", CC0): no real faces in a public README.
AVATAR = "https://api.dicebear.com/9.x/lorelei/png?seed={seed}&size=256&backgroundColor={bg}"


def set_avatar(token: str, user_id: str, seed: str, bg: str) -> None:
    url = AVATAR.format(seed=urllib.parse.quote(seed), bg=bg)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"user-agent": "castor-demo-seed"})) as resp:
        image = resp.read()
    boundary = "castordemo" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"avatar.png\"\r\n"
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + image + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{API}/api/users/{user_id}/avatar/upload", data=body, method="POST")
    req.add_header("content-type", f"multipart/form-data; boundary={boundary}")
    req.add_header("authorization", f"Bearer {token}")
    urllib.request.urlopen(req).read()


def login(username: str, password: str) -> str:
    return call("POST", "/auth/login", body={"username": username, "password": password})["token"]


def wait_for_runs(token: str, run_ids: list[str], timeout: float = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        runs = [call("GET", f"/playbook-runs/{rid}", token) for rid in run_ids]
        if all(r["status"] not in ("pending", "running") or any(s == "attention" for s in r.get("step_status", {}).values()) for r in runs):
            return
        time.sleep(2)
    print("warning: playbook runs still going", file=sys.stderr)


def main() -> None:
    admin = login("admin", "castor")
    call("PATCH", "/auth/me", admin, {"display_name": "Alex Morgan"})
    me = call("GET", "/auth/me", admin)

    people = {}
    for username, name, role in [
        ("sam", "Sam Patel", "operator"),
        ("jordan", "Jordan Lee", "operator"),
        ("riley", "Riley Chen", "viewer"),
    ]:
        user = call("POST", "/users", admin, {"username": username, "display_name": name, "role": role, "password": PASSWORD})
        people[username] = user
    sam, jordan = login("sam", PASSWORD), login("jordan", PASSWORD)
    for user_id, seed, bg in [
        (me["id"], "Alex Morgan", "c0aede"),
        (people["sam"]["id"], "Sam Patel", "b6e3f4"),
        (people["jordan"]["id"], "Jordan Lee", "ffdfbf"),
        (people["riley"]["id"], "Riley Chen", "d1f4d9"),
    ]:
        set_avatar(admin, user_id, seed, bg)

    client = call("POST", "/clients", admin, {"name": "Northwind Logistics"})
    nwl = call("POST", "/projects", admin, {
        "codename": "NWL26-001", "name": "Northwind internal network test", "kind": "internal",
        "client_id": client["id"], "description": "Internal assessment of the Northwind HQ and warehouse networks.",
    })
    lab = call("POST", "/projects", admin, {"codename": "LAB", "name": "Lab tooling", "kind": "pipeline"})
    call("PUT", f"/projects/{nwl['id']}/members", admin, [people[u]["id"] for u in ("sam", "jordan", "riley")])
    call("PUT", f"/projects/{lab['id']}/members", admin, [people["sam"]["id"]])

    columns = {c["role"]: c["id"] for c in call("GET", f"/projects/{nwl['id']}/board", admin)["columns"]}

    def card(title, column, token=admin, **extra):
        return call("POST", f"/projects/{nwl['id']}/tasks", token, {"title": title, "column_id": columns[column], **extra})

    # Pinned notes first, so they're the oldest activity.
    for title, color, body in [
        ("Status", "yellow", "External phase **done**. Internal testing runs this week.\n\n- [x] Kickoff call\n- [x] VPN access\n- [ ] Domain creds from client"),
        ("Objectives", "blue", "1. Domain admin from a standard user\n2. Reach the warehouse OT segment\n3. Phish 2 of 5 test users"),
        ("Scope", "green", "In: `10.20.0.0/16`, `northwind.local`\n\nOut: the payroll vendor and `10.20.99.0/24` (printers)"),
        ("Contacts", "pink", "**Dana Brooks** (IT lead): weekdays 8 to 6\n\nEmergency stop: call the SOC line in the RoE"),
    ]:
        call("POST", f"/projects/{nwl['id']}/notes", admin, {"title": title, "body": body, "color": color})

    card("Review rules of engagement", "done", priority=2, assignees=[me["id"]], tags=["admin"])
    card("Confirm scope exclusions with client", "todo", priority=3, assignees=["sam"], tags=["scope"],
         description="Printers and the payroll vendor are out; confirm the warehouse OT range.")
    card("Phishing pretext: shipping delay notice", "backlog", tags=["social"], assignees=["jordan"])
    card("Kerberoast service accounts", "backlog", priority=2, tags=["ad"])
    card("Password spray (one attempt per account)", "backlog", tags=["ad"], priority=1)
    card("Write up finding: SMB signing not required", "todo", tags=["report"], assignees=[me["id"]])
    card("Check DNS zone transfer", "todo", kind="command", tags=["dns"],
         command="echo 'Trying AXFR against ns1.northwind.local'; echo 'Transfer failed: REFUSED' >&2; exit 1")

    # Two demo playbooks (fake tool output, no network): one finishes, one stops for a decision.
    runs = [
        call("POST", "/playbooks/pipeline-demo/run", admin, {"project_id": nwl["id"], "inputs": {"subnet": "10.20.4", "host_count": 6, "speed": 0.3}}),
        call("POST", "/playbooks/branching-demo/run", sam, {"project_id": nwl["id"], "inputs": {"subnet": "10.20.8", "fail_at": "smb_audit", "speed": 0.3}}),
    ]
    run_dns = [t for t in call("GET", f"/projects/{nwl['id']}/board", admin)["tasks"] if t["title"] == "Check DNS zone transfer"][0]
    call("POST", f"/tasks/{run_dns['id']}/run", admin)
    wait_for_runs(admin, [r["id"] for r in runs])
    time.sleep(3)

    # People talking on the cards, with mentions.
    tasks = {t["title"]: t for t in call("GET", f"/projects/{nwl['id']}/board", admin)["tasks"]}
    # Steps are cards hidden inside their run's card; find the SMB audit through its run.
    branching = call("GET", f"/playbook-runs/{runs[1]['id']}", admin)
    smb = next(t for t in branching["tasks"] if t["title"].endswith("SMB signing audit"))
    call("POST", f"/tasks/{smb['id']}/comments", sam, {"body": "@admin three hosts don't require SMB signing. Relay looks viable from the user VLAN; want me to queue the relay check?"})
    call("POST", f"/tasks/{smb['id']}/comments", admin, {"body": "Yes, go ahead @sam. Keep it to the HQ range for now."})
    scope = tasks["Confirm scope exclusions with client"]
    call("POST", f"/tasks/{scope['id']}/comments", jordan, {"body": "Dana confirmed the OT range is `10.20.40.0/24`. @admin can you add it to the Scope note?"})
    call("PATCH", f"/tasks/{tasks['Kerberoast service accounts']['id']}", admin, {"assignees": ["jordan"]})

    call("POST", f"/projects/{lab['id']}/tasks", sam, {"title": "Build the NetExec tool image on lab-agent-01", "assignees": ["sam"]})
    print(f"seeded: projects {nwl['codename']} and {lab['codename']}, users alex(admin)/sam/jordan/riley")


if __name__ == "__main__":
    main()
