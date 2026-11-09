"""The SSH config SSH hosts are reached with: ssh_config syntax, OpenSSH's precedence rules and
ProxyJump chains, and what Castor refuses or ignores."""

import pytest

from castor_server.ssh_config import ConfigError, jump_spec, parse, route

CONFIG = """
# defaults for everything, as OpenSSH reads them: the first value found wins
Host bastion
    HostName bastion.example.net
    User jumper
    Port 2200

Host inner
    HostName 10.1.0.5
    ProxyJump bastion

Host lab-*  !lab-direct
    ProxyJump inner
    User = kali

Host *.corp
    HostName %h.example.com
    ProxyJump alice@gw1:2222,bob@[2001:db8::1]:22

Host *
    User fallback
    ServerAliveInterval 20
    ConnectTimeout 5
"""


def labels(text, host, user=None, port=None):
    return [h.label for h in route(parse(text), host, user, port)]


def test_a_host_without_config_is_reached_directly():
    assert labels("", "10.0.0.9", "root", None) == ["root@10.0.0.9:22"]
    assert labels("", "10.0.0.9", "root", 2022) == ["root@10.0.0.9:2022"]
    with pytest.raises(ConfigError, match="no username"):
        route(parse(""), "10.0.0.9", None, None)


def test_aliases_users_ports_and_keepalives():
    hops = route(parse(CONFIG), "bastion", None, None)
    assert [h.label for h in hops] == ["jumper@bastion.example.net:2200"]
    assert hops[0].keepalive_interval == 20 and hops[0].connect_timeout == 5
    # The host's own fields beat the config, like ssh's -l and -p.
    assert labels(CONFIG, "bastion", "root", 22) == ["root@bastion.example.net:22"]
    assert labels(CONFIG, "plain-host") == ["fallback@plain-host:22"]


def test_proxyjump_chains_through_each_jump_hosts_own_config():
    # lab-1 → inner → bastion: each jump host gets its own config (its User, and its own
    # ProxyJump when it is the only jump).
    assert labels(CONFIG, "lab-1") == [
        "jumper@bastion.example.net:2200", "fallback@10.1.0.5:22", "kali@lab-1:22",
    ]
    assert labels(CONFIG, "lab-direct") == ["fallback@lab-direct:22"]  # negated pattern
    # A list goes through each in turn; a later hop's own ProxyJump is replaced by the list.
    assert labels(CONFIG, "db.corp", "dba") == [
        "alice@gw1:2222", "bob@[2001:db8::1]:22", "dba@db.corp.example.com:22",
    ]


def test_jump_users_default_to_the_hosts_user():
    assert labels("Host t\n  ProxyJump gw\n", "t", "me") == ["me@gw:22", "me@t:22"]
    assert labels("Host t\n  ProxyJump ssh://ops@gw:2222\n", "t", "me") == ["ops@gw:2222", "me@t:22"]
    assert labels("Host t\n  ProxyJump none\nHost *\n  ProxyJump gw\n", "t", "me") == ["me@t:22"]


def test_loops_are_caught():
    with pytest.raises(ConfigError, match="loop"):
        route(parse("Host a\n  ProxyJump b\nHost b\n  ProxyJump a\n"), "a", "u", None)


def test_commands_files_and_match_are_refused_and_the_rest_ignored():
    parsed = parse("Host x\n  ProxyCommand nc %h %p\nInclude ~/.ssh/other\nMatch exec true\n"
                   "  IdentityFile ~/.ssh/id_rsa\n  ForwardAgent yes\n  Port nope\n  HostName %r.x\n  ProxyJump a@@b\n")
    assert len(parsed.errors) == 6
    assert any("ProxyCommand" in e for e in parsed.errors) and any("Include" in e for e in parsed.errors)
    assert any("Match" in e for e in parsed.errors) and any("Port must be a number" in e for e in parsed.errors)
    assert [w.split(":")[1].split()[0] for w in parsed.warnings] == ["IdentityFile", "ForwardAgent"]
    with pytest.raises(ConfigError, match="has errors"):
        route(parsed, "x", "u", None)


def test_jump_specs():
    assert jump_spec("gw").host == "gw"
    j = jump_spec("ops@[fe80::1]:2200")
    assert (j.user, j.host, j.port) == ("ops", "fe80::1", 2200)
    for bad in ("", "a@", "host:99999", "[::1"):
        with pytest.raises(ConfigError):
            jump_spec(bad)
