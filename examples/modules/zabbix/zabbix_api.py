# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: register a host in Zabbix through the control-side API (issue #72).

This is a *usage* example, not part of the library. The three coroutines the
Ansible API branch maps onto -- ``zabbix.host_group``, ``zabbix.host`` and
``zabbix.host_macro`` -- run on the **controller** against the Zabbix server's
JSON-RPC endpoint: they take no ``OpContext`` and yield no command descriptors,
because the ``ansible-zabbix-agent`` tasks that inspired them were
``delegate_to: localhost`` with ``become: no``. Like ``files.fetch_url`` (their
GET-shaped sibling), they are awaited directly from ``main()``, a role factory,
or a ``control`` barrier -- never passed to ``op()``.

The script walks the whole registration a real role would perform:

1. create the host groups (``zabbix_group``),
2. register the monitored host with its agent interface (``zabbix_host``),
3. push a macro onto it (``zabbix_hostmacro``),

then re-runs every call to show each one's **fact-gated idempotence**: the
second pass reads current state through ``hostgroup.get`` / ``host.get`` /
``hostmacro.get`` and issues no create/update, so it reports
``changed=False`` throughout. Finally it removes the host and the group, the
``state="absent"`` direction.

Each operation talks to the single network seam
``reemote.modules.zabbix.zabbix._jsonrpc_request`` -- ``urllib`` in a worker
thread, the POST twin of ``files._fetch_url_bytes``. To keep the example
runnable **offline** (no live Zabbix server), that seam is replaced below with a
small in-memory fake that speaks just the methods used here; deleting the stub
and pointing ``API_URL`` at a real ``api_jsonrpc.php`` with real credentials is
the entire change needed to talk to a live server. The host IP that Ansible
derived from ``ipaddr`` is simply passed in an ``interfaces`` entry here
(``hardware.DefaultIpv4Address`` -- issue #64 -- covers the default-route case).

Run it with::

    uv run python examples/modules/zabbix/zabbix_api.py
"""

import asyncio
import itertools
import json
import sys

from reemote import zabbix
from reemote.modules.zabbix import zabbix as zabbix_module

# A host the "server" would report; in a real deploy this arrives from a fact.
HOST_NAME = "web01"
HOST_IP = "10.0.0.5"
GROUP = "Linux servers"
INTERFACE = {"type": 1, "main": 1, "use_ip": 1, "ip": HOST_IP, "dns": "", "port": "10050"}
MACRO = {"name": "ROLE", "value": "web"}


class _FakeServer:
    """The bare slice of the Zabbix JSON-RPC API this example exercises."""

    def __init__(self) -> None:
        self.groups: dict[str, str] = {}
        self.hosts: dict[str, dict] = {}
        self.macros: dict[str, dict] = {}
        self._ids = itertools.count(1000)

    def __call__(self, url, payload, *, validate_certs=True, http_auth=None, bearer=None):
        method = payload["method"]
        params = payload.get("params") or {}
        result = getattr(self, "_" + method.replace(".", "_"))(params)
        return {"jsonrpc": "2.0", "result": result, "id": payload["id"]}

    def _next(self) -> str:
        return str(next(self._ids))

    def _user_login(self, params):
        return "offline-session-token"

    def _hostgroup_get(self, params):
        names = params.get("filter", {}).get("name", [])
        return [{"groupid": self.groups[n], "name": n} for n in names if n in self.groups]

    def _hostgroup_create(self, params):
        gid = self._next()
        self.groups[params["name"]] = gid
        return {"groupids": [gid]}

    def _hostgroup_delete(self, ids):
        for name, gid in list(self.groups.items()):
            if gid in ids:
                del self.groups[name]
        return {"groupids": ids}

    def _host_get(self, params):
        name = params.get("filter", {}).get("host", [None])[0]
        return [dict(self.hosts[name])] if name in self.hosts else []

    def _host_create(self, params):
        hid = self._next()
        self.hosts[params["host"]] = {
            "hostid": hid,
            "host": params["host"],
            "status": params.get("status", 0),
            "description": params.get("description", ""),
            "groups": params.get("groups", []),
            "interfaces": params.get("interfaces", []),
        }
        return {"hostids": [hid]}

    def _host_update(self, params):
        existing = next(h for h in self.hosts.values() if h["hostid"] == params["hostid"])
        existing.update(params)
        return {"hostids": [params["hostid"]]}

    def _host_delete(self, ids):
        for name, host in list(self.hosts.items()):
            if host["hostid"] in ids:
                del self.hosts[name]
        return {"hostids": ids}

    def _hostmacro_get(self, params):
        return [dict(m) for m in self.macros.get(params["hostids"][0], {}).values()]

    def _hostmacro_create(self, params):
        mid = self._next()
        self.macros.setdefault(params["hostid"], {})[params["macro"]] = dict(
            params, hostmacroid=mid
        )
        return {"hostmacroids": [mid]}

    def _hostmacro_update(self, params):
        entry = self.macros[params["hostid"]][params["macro"]]
        entry.update(params)
        return {"hostmacroids": [entry["hostmacroid"]]}

    def _hostmacro_delete(self, ids):
        for host_macros in self.macros.values():
            for macro, entry in list(host_macros.items()):
                if entry["hostmacroid"] in ids:
                    del host_macros[macro]
        return {"hostmacroids": ids}


def _install_offline_stub() -> None:
    """Route the JSON-RPC seam to an in-memory server (delete for a live run)."""
    zabbix_module._jsonrpc_request = _FakeServer()  # type: ignore[assignment]


def _show(step: str, report) -> None:
    print(f"{step}: {json.dumps(report, sort_keys=True)}")


async def main() -> None:
    try:
        _install_offline_stub()

        # Build + authenticate the client (a real run: zabbix.connect(API_URL,
        # user=..., password=...)). With the stub the login round-trips to the
        # fake and returns an offline token.
        client = await zabbix.connect("https://zabbix.example/api_jsonrpc.php", user="admin", password="secret")

        print("== Register a monitored host in Zabbix (control-side API) ==")
        print("pass 1 -- create:")
        _show("  host_group ", await zabbix.host_group(client, GROUP))
        host_report = await zabbix.host(client, HOST_NAME, host_groups=GROUP, interfaces=[INTERFACE])
        _show("  host       ", host_report)
        hostid = host_report["hostid"]
        _show("  host_macro ", await zabbix.host_macro(client, hostid, MACRO))

        # The fact-gated payoff: every call reads current state first, so the
        # identical second pass writes nothing and reports changed=False.
        print("pass 2 -- idempotent (no writes, changed=False):")
        _show("  host_group ", await zabbix.host_group(client, GROUP))
        _show("  host       ", await zabbix.host(client, HOST_NAME, host_groups=GROUP, interfaces=[INTERFACE]))
        _show("  host_macro ", await zabbix.host_macro(client, hostid, MACRO))

        print("cleanup -- state='absent':")
        _show("  host       ", await zabbix.host(client, HOST_NAME, state="absent"))
        _show("  host_group ", await zabbix.host_group(client, GROUP, state="absent"))

        # The whole point of the example: the first pass changed everything, the
        # second changed nothing.
        print("\n== Assertions ==")
        assert host_report["changed"] is True
        print("OK: create-then-noop idempotence held across both passes")
    except OSError as exc:
        # A real run would fail here on an unreachable server or a non-2xx
        # reply (urllib.error.URLError/HTTPError -- both OSError subclasses).
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
