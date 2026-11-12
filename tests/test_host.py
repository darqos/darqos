# darqos
# Copyright (C) 2026 David Arnold

# Tests for darq.host: what's asked of the host Linux, the machine's
# name and network addresses, from ip(8)'s JSON.

import json
import subprocess
import types

import darq.host as host


def interface(name, *addresses):
    return {"ifname": name, "addr_info": [{"family": family, "local": address, "scope": scope}
                                          for family, address, scope in addresses]}


def test_ipv4_addresses_in_order():
    output = json.dumps([interface("wlp1s0", ("inet", "192.168.86.172", "global"),
                                   ("inet6", "fde7::1", "global")),
                         interface("eth0", ("inet", "10.0.0.5", "global"), ("inet", "10.0.0.5", "global"))])
    assert host.parse_addresses(output) == ["192.168.86.172", "10.0.0.5"]


def test_ipv6_only_if_theres_no_ipv4():
    output = json.dumps([interface("wlp1s0", ("inet6", "fde7::1", "global"), ("inet6", "fe80::1", "link"))])
    assert host.parse_addresses(output) == ["fde7::1"]


def test_none_and_nonsense():
    assert host.parse_addresses("[]") == []
    assert host.parse_addresses("") == []
    assert host.parse_addresses("not json") == []
    assert host.parse_addresses('{"ifname": "x"}') == []


def test_addresses_asks_ip():
    asked = []

    def run(command, **options):
        asked.append(command)
        return types.SimpleNamespace(returncode=0, stdout=json.dumps([interface("e", ("inet", "192.0.2.1", "global"))]),
                                     stderr="")

    assert host.addresses(run) == ["192.0.2.1"]
    assert asked == [["ip", "-j", "addr", "show", "scope", "global"]]


def test_addresses_when_ip_cant_say(caplog):
    def failed(command, **options):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="no")

    def missing(command, **options):
        raise FileNotFoundError("ip")

    def slow(command, **options):
        raise subprocess.TimeoutExpired(command, 2)

    assert host.addresses(failed) == host.addresses(missing) == host.addresses(slow) == []
    assert "ip failed" in caplog.text


def test_describe():
    assert host.describe("darqos", ["192.168.86.172"]) == "darqos  ·  192.168.86.172"
    assert host.describe("darqos", ["10.0.0.5", "192.0.2.1"]) == "darqos  ·  10.0.0.5  ·  192.0.2.1"
    assert host.describe("darqos", []) == "darqos  ·  No network"
    assert host.describe("", ["192.0.2.1"]) == "192.0.2.1"
