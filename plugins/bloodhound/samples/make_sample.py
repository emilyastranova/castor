"""Writes samples/corp-sharphound.zip: a small, made-up SharpHound CE (v6) collection of a domain
CORP.LOCAL, for the tests and for trying the plugin. Nothing in it is real.

    python3 samples/make_sample.py
"""

import json
import zipfile
from pathlib import Path

D = "S-1-5-21-1111-2222-3333"
P = "S-1-5-21-4444-5555-6666"
meta = lambda t, n: {"methods": 46067, "type": t, "count": n, "version": 6}  # noqa: E731


def user(rid, name, enabled=True, spn=False, asrep=False, admincount=False, desc=None, aces=()):
    props = {"name": f"{name.upper()}@CORP.LOCAL", "domain": "CORP.LOCAL", "domainsid": D, "samaccountname": name,
             "enabled": enabled, "hasspn": spn, "dontreqpreauth": asrep, "admincount": admincount}
    if spn:
        props["serviceprincipalnames"] = [f"MSSQLSvc/srv01.corp.local:1433"]
    if desc:
        props["description"] = desc
    return {"ObjectIdentifier": f"{D}-{rid}", "Properties": props, "Aces": list(aces), "AllowedToDelegate": [], "SPNTargets": []}


def ace(who, right, kind="User"):
    return {"PrincipalSID": who, "PrincipalType": kind, "RightName": right, "IsInherited": False}


users = [
    user(1103, "alice"),
    user(1104, "bob", aces=[ace(f"{D}-1107", "ForceChangePassword")]),
    user(1105, "carol.da", admincount=True),
    user(1106, "dave", enabled=False, desc="left in 2025"),
    user(1107, "svc_sql", spn=True, desc="SQL service"),
    user(1108, "erin", asrep=True),
    user(1109, "backup_svc"),
]
groups = [
    {"ObjectIdentifier": f"{D}-512", "Properties": {"name": "DOMAIN ADMINS@CORP.LOCAL", "domain": "CORP.LOCAL", "admincount": True},
     "Members": [{"ObjectIdentifier": f"{D}-1105", "ObjectType": "User"}], "Aces": []},
    {"ObjectIdentifier": f"{D}-513", "Properties": {"name": "DOMAIN USERS@CORP.LOCAL", "domain": "CORP.LOCAL"},
     "Members": [{"ObjectIdentifier": f"{D}-{r}", "ObjectType": "User"} for r in range(1103, 1110)], "Aces": []},
    {"ObjectIdentifier": f"{D}-516", "Properties": {"name": "DOMAIN CONTROLLERS@CORP.LOCAL", "domain": "CORP.LOCAL"},
     "Members": [{"ObjectIdentifier": f"{D}-1000", "ObjectType": "Computer"}], "Aces": []},
    {"ObjectIdentifier": f"{D}-1110", "Properties": {"name": "IT ADMINS@CORP.LOCAL", "domain": "CORP.LOCAL"},
     "Members": [{"ObjectIdentifier": f"{D}-1104", "ObjectType": "User"}], "Aces": []},
]


def computer(rid, name, os, admins=(), sessions=(), dc=False):
    return {
        "ObjectIdentifier": f"{D}-{rid}",
        "Properties": {"name": f"{name.upper()}.CORP.LOCAL", "domain": "CORP.LOCAL", "domainsid": D, "samaccountname": f"{name.upper()}$",
                       "operatingsystem": os, "enabled": True, "isdc": dc},
        "LocalGroups": [{"ObjectIdentifier": f"{D}-{rid}-544", "Name": f"ADMINISTRATORS@{name.upper()}.CORP.LOCAL", "Collected": True,
                         "Results": [{"ObjectIdentifier": a, "ObjectType": t} for a, t in admins]}],
        "Sessions": {"Collected": True, "Results": [{"UserSID": f"{D}-{u}", "ComputerSID": f"{D}-{rid}"} for u in sessions]},
        "PrivilegedSessions": {"Collected": True, "Results": []},
        "RegistrySessions": {"Collected": True, "Results": []},
        "Aces": [],
    }


computers = [
    computer(1000, "dc01", "Windows Server 2022 Standard", admins=[(f"{D}-512", "Group")], dc=True),
    computer(1001, "ws01", "Windows 11 Enterprise", admins=[(f"{D}-1103", "User"), (f"{D}-1110", "Group")], sessions=[1103, 1104]),
    computer(1002, "srv01", "Windows Server 2019 Standard", admins=[(f"{D}-1110", "Group")], sessions=[1105, 1107]),
]
domains = [
    {"ObjectIdentifier": D, "Properties": {"name": "CORP.LOCAL", "domain": "CORP.LOCAL", "functionallevel": "2016", "netbios": "CORP"},
     "Trusts": [{"TargetDomainSid": P, "TargetDomainName": "PARTNER.LOCAL", "IsTransitive": False, "TrustDirection": "Bidirectional",
                 "TrustType": "External"}],
     "Aces": [ace(f"{D}-516", "GetChanges", "Group"), ace(f"{D}-516", "GetChangesAll", "Group"),
              ace(f"{D}-1109", "GetChanges"), ace(f"{D}-1109", "GetChangesAll")]},
    {"ObjectIdentifier": P, "Properties": {"name": "PARTNER.LOCAL", "domain": "PARTNER.LOCAL", "functionallevel": "2012", "netbios": "PARTNER"},
     "Trusts": [], "Aces": []},
]

out = Path(__file__).with_name("corp-sharphound.zip")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for name, kind, data in (("users", "users", users), ("groups", "groups", groups), ("computers", "computers", computers),
                             ("domains", "domains", domains)):
        # SharpHound writes a byte-order mark first.
        z.writestr(f"20260101000000_{name}.json", "﻿" + json.dumps({"data": data, "meta": meta(kind, len(data))}))
print(out)
