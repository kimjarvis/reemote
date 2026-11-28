# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline tests for the ``zabbix`` control-side API namespace (issue #72).

``host_group`` / ``host`` / ``host_macro`` are plain coroutine functions that
speak Zabbix JSON-RPC on the *controller*, so the whole suite runs against a
canned, stateful fake server: the single network seam
``zabbix._jsonrpc_request`` is monkeypatched to dispatch on the JSON-RPC
``method`` and mutate in-memory group/host/macro tables, exactly the
``files._fetch_url_bytes`` offline pattern (no HTTP client dependency, no
network). This proves the client envelope (result unwrap, error raising,
session-token / bearer auth), the fact-gated idempotence of each operation
(a second identical call issues no create/update/delete and reports
``changed=False``), the enum/normalization helpers, the unknown-group/template
guards, and the package exports.
"""

import itertools

import pytest

from reemote.modules import zabbix
from reemote.modules.zabbix import zabbix as zabbix_module

URL = "https://zabbix.example/api_jsonrpc.php"


class FakeZabbix:
    """A minimal stateful stand-in for the Zabbix JSON-RPC endpoint."""

    def __init__(self) -> None:
        self.groups: dict[str, str] = {}
        self.hosts: dict[str, dict] = {}
        self.macros: dict[str, dict[str, dict]] = {}
        self.templates = {"Linux generic": "T1", "Template App Zabbix agent": "T2"}
        self.proxies = {"zabbix-proxy-1": "P1"}
        self.token = "SESSION-TOKEN"
        self._gids = itertools.count(100)
        self._hids = itertools.count(200)
        self._mids = itertools.count(300)
        self._iids = itertools.count(400)
        self.calls: list[str] = []
        self.seen_bearer: list[str | None] = []
        self.seen_auth: list[str | None] = []

    # -- seam entrypoint ---------------------------------------------------
    def request(self, url, payload, *, validate_certs=True, http_auth=None, bearer=None):
        assert url == URL
        self.seen_bearer.append(bearer)
        self.seen_auth.append(payload.get("auth"))
        result = self.dispatch(payload["method"], payload.get("params") or {})
        return {"jsonrpc": "2.0", "result": result, "id": payload["id"]}

    def dispatch(self, method, params):
        self.calls.append(method)
        handler = getattr(self, f"_{method.replace('.', '_')}", None)
        if handler is None:
            raise AssertionError(f"unhandled method {method}")
        return handler(params)

    # -- auth --------------------------------------------------------------
    def _user_login(self, params):
        assert params["user"] == "admin"
        return self.token

    def _user_logout(self, params):
        return True

    # -- host groups -------------------------------------------------------
    def _hostgroup_get(self, params):
        names = params.get("filter", {}).get("name", [])
        return [{"groupid": self.groups[n], "name": n} for n in names if n in self.groups]

    def _hostgroup_create(self, params):
        gid = str(next(self._gids))
        self.groups[params["name"]] = gid
        return {"groupids": [gid]}

    def _hostgroup_delete(self, ids):
        for gid in ids:
            for name, value in list(self.groups.items()):
                if value == gid:
                    del self.groups[name]
        return {"groupids": ids}

    # -- hosts -------------------------------------------------------------
    def _host_get(self, params):
        name = params.get("filter", {}).get("host", [None])[0]
        rec = self.hosts.get(name)
        return [dict(rec)] if rec else []

    def _host_create(self, params):
        hid = str(next(self._hids))
        self.hosts[params["host"]] = self._record(hid, params)
        return {"hostids": [hid]}

    def _host_update(self, params):
        hostid = params["hostid"]
        existing = next(h for h in self.hosts.values() if h["hostid"] == hostid)
        merged = {**existing, **params}
        self.hosts[params["host"]] = self._record(hostid, merged)
        return {"hostids": [hostid]}

    def _host_delete(self, ids):
        for name, rec in list(self.hosts.items()):
            if rec["hostid"] in ids:
                del self.hosts[name]
        return {"hostids": ids}

    def _record(self, hostid, params):
        interfaces = [dict(i, interfaceid=str(next(self._iids))) for i in params.get("interfaces", [])]
        return {
            "hostid": hostid,
            "host": params["host"],
            "description": params.get("description", ""),
            "status": params.get("status", 0),
            "tls_connect": params.get("tls_connect", 0),
            "tls_accept": params.get("tls_accept", 0),
            "tls_psk_identity": params.get("tls_psk_identity", ""),
            "tls_psk": params.get("tls_psk", ""),
            "groups": [dict(g) for g in params.get("groups", [])],
            "interfaces": interfaces,
            "templates": [dict(t) for t in params.get("templates", [])],
        }

    # -- templates / proxies ----------------------------------------------
    def _template_get(self, params):
        names = params.get("filter", {}).get("host", [])
        return [
            {"templateid": self.templates[n], "host": n} for n in names if n in self.templates
        ]

    def _proxy_get(self, params):
        names = params.get("filter", {}).get("host", [])
        return [{"proxyid": self.proxies[n], "host": n} for n in names if n in self.proxies]

    # -- macros ------------------------------------------------------------
    def _hostmacro_get(self, params):
        hostid = params["hostids"][0]
        return [dict(entry) for entry in self.macros.get(hostid, {}).values()]

    def _hostmacro_create(self, params):
        mid = str(next(self._mids))
        entry = dict(params, hostmacroid=mid)
        self.macros.setdefault(params["hostid"], {})[params["macro"]] = entry
        return {"hostmacroids": [mid]}

    def _hostmacro_update(self, params):
        host_macros = self.macros.setdefault(params["hostid"], {})
        entry = host_macros[params["macro"]]
        entry.update(params)
        return {"hostmacroids": [entry["hostmacroid"]]}

    def _hostmacro_delete(self, ids):
        for host_macros in self.macros.values():
            for macro, entry in list(host_macros.items()):
                if entry["hostmacroid"] in ids:
                    del host_macros[macro]
        return {"hostmacroids": ids}


@pytest.fixture
def fake(monkeypatch):
    """Install a canned stateful Zabbix server behind the network seam."""
    server = FakeZabbix()
    monkeypatch.setattr(zabbix_module, "_jsonrpc_request", server.request)
    return server


@pytest.fixture
async def client(fake):
    """A logged-in user/password client wired to the fake server."""
    session = zabbix.ZabbixClient(URL, user="admin", password="secret")
    await session.login()
    return session


# --- client construction and authentication ---------------------------------


def test_client_requires_authentication():
    with pytest.raises(ValueError, match="api_token or a user/password"):
        zabbix.ZabbixClient(URL)


def test_client_rejects_non_http_scheme():
    with pytest.raises(ValueError, match="http\\(s\\) URL"):
        zabbix.ZabbixClient("zabbix://localhost")


async def test_login_stores_the_session_token(fake):
    session = zabbix.ZabbixClient(URL, user="admin", password="secret")
    token = await session.login()
    assert token == fake.token
    assert session.session_token == fake.token
    assert "user.login" in fake.calls
    # the login call itself carries no auth; later calls attach the token
    await session.call("hostgroup.get", {})
    assert fake.seen_auth[-1] == fake.token


async def test_api_token_skips_login_and_uses_bearer(fake):
    session = zabbix.ZabbixClient(URL, api_token="TOK")
    assert await session.login() is None
    assert fake.calls == []  # no user.login round-trip
    await session.call("hostgroup.get", {})
    assert fake.seen_bearer[-1] == "TOK"
    assert fake.seen_auth[-1] is None


async def test_connect_factory_logs_in(fake):
    session = await zabbix.connect(URL, user="admin", password="secret")
    assert isinstance(session, zabbix.ZabbixClient)
    assert session.session_token == fake.token
    assert "user.login" in fake.calls


async def test_call_unwraps_result(fake):
    fake.groups["Linux servers"] = "9"
    result = await (await zabbix.connect(URL, user="admin", password="secret")).call(
        "hostgroup.get", {"filter": {"name": ["Linux servers"]}}
    )
    assert result == [{"groupid": "9", "name": "Linux servers"}]


async def test_call_raises_on_error_object(monkeypatch):
    def boom(url, payload, **kwargs):
        return {"jsonrpc": "2.0", "id": 1, "error": {"message": "Not authorized", "data": ""}}

    monkeypatch.setattr(zabbix_module, "_jsonrpc_request", boom)
    session = zabbix.ZabbixClient(URL, api_token="TOK")
    with pytest.raises(zabbix.ZabbixAPIError, match="Not authorized"):
        await session.call("host.get", {})


async def test_logout_invalidates_session(fake):
    session = await zabbix.connect(URL, user="admin", password="secret")
    await session.logout()
    assert session.session_token is None
    assert "user.logout" in fake.calls


# --- host_group --------------------------------------------------------------


async def test_host_group_creates_missing(client, fake):
    report = await zabbix.host_group(client, "Linux servers")
    assert report == {"state": "present", "present": [], "created": [
        {"name": "Linux servers", "groupid": fake.groups["Linux servers"]}], "changed": True}


async def test_host_group_is_idempotent(client, fake):
    await zabbix.host_group(client, "Linux servers")
    fake.calls.clear()
    report = await zabbix.host_group(client, "Linux servers")
    assert report["changed"] is False
    assert "hostgroup.create" not in fake.calls


async def test_host_group_creates_only_missing_among_mixed(client, fake):
    await zabbix.host_group(client, ["Linux servers"])
    fake.calls.clear()
    report = await zabbix.host_group(client, ["Linux servers", "Databases"])
    assert [c["name"] for c in report["created"]] == ["Databases"]
    assert "Linux servers" in report["present"]


async def test_host_group_absent_deletes_present(client, fake):
    await zabbix.host_group(client, "Databases")
    report = await zabbix.host_group(client, "Databases", state="absent")
    assert report["deleted"] == ["Databases"]
    assert report["changed"] is True
    assert "Databases" not in fake.groups


async def test_host_group_absent_noop_when_missing(client, fake):
    fake.calls.clear()
    report = await zabbix.host_group(client, "Ghost", state="absent")
    assert report["changed"] is False
    assert "hostgroup.delete" not in fake.calls


async def test_host_group_empty_names_short_circuits(client, fake):
    fake.calls.clear()
    report = await zabbix.host_group(client, [])
    assert report == {"state": "present", "present": [], "changed": False}
    assert fake.calls == []


async def test_host_group_rejects_unknown_state(client):
    with pytest.raises(ValueError, match="state must be"):
        await zabbix.host_group(client, "x", state="sometimes")


# --- host --------------------------------------------------------------------


async def test_host_creates_when_absent(client, fake):
    await zabbix.host_group(client, "Linux servers")
    iface = {"type": 1, "main": 1, "use_ip": 1, "ip": "10.0.0.5", "dns": "", "port": "10050"}
    report = await zabbix.host(client, "web01", host_groups="Linux servers", interfaces=[iface])
    assert report["action"] == "created"
    assert report["changed"] is True
    gid = fake.groups["Linux servers"]
    assert fake.hosts["web01"]["groups"] == [{"groupid": gid}]
    assert fake.hosts["web01"]["interfaces"][0]["ip"] == "10.0.0.5"


async def test_host_is_idempotent(client, fake):
    await zabbix.host_group(client, "Linux servers")
    iface = {"type": 1, "main": 1, "ip": "10.0.0.5", "dns": "", "port": "10050"}
    await zabbix.host(client, "web01", host_groups="Linux servers", interfaces=[iface])
    fake.calls.clear()
    report = await zabbix.host(client, "web01", host_groups="Linux servers", interfaces=[iface])
    assert report["action"] == "unchanged"
    assert report["changed"] is False
    assert "host.update" not in fake.calls and "host.create" not in fake.calls


async def test_host_updates_when_status_differs(client, fake):
    await zabbix.host_group(client, "Linux servers")
    await zabbix.host(client, "web01", host_groups="Linux servers")
    report = await zabbix.host(client, "web01", host_groups="Linux servers", status="disabled")
    assert report["action"] == "updated"
    assert fake.hosts["web01"]["status"] == 1


async def test_host_updates_on_interface_change(client, fake):
    await zabbix.host_group(client, "Linux servers")
    await zabbix.host(
        client, "web01", host_groups="Linux servers",
        interfaces=[{"type": 1, "main": 1, "ip": "10.0.0.5", "dns": "", "port": "10050"}],
    )
    report = await zabbix.host(
        client, "web01", host_groups="Linux servers",
        interfaces=[{"type": 1, "main": 1, "ip": "10.0.0.5", "dns": "", "port": "10051"}],
    )
    assert report["action"] == "updated"


async def test_host_absent_deletes(client, fake):
    await zabbix.host_group(client, "Linux servers")
    await zabbix.host(client, "web01", host_groups="Linux servers")
    report = await zabbix.host(client, "web01", state="absent")
    assert report["action"] == "deleted" and report["changed"] is True
    assert "web01" not in fake.hosts


async def test_host_absent_noop_when_missing(client, fake):
    fake.calls.clear()
    report = await zabbix.host(client, "ghost", state="absent")
    assert report["action"] == "absent" and report["changed"] is False
    assert "host.delete" not in fake.calls


async def test_host_rejects_unknown_group_before_write(client, fake):
    fake.calls.clear()
    with pytest.raises(ValueError, match="unknown host group"):
        await zabbix.host(client, "web01", host_groups="Nope")
    assert "host.create" not in fake.calls


async def test_host_rejects_unknown_template(client, fake):
    await zabbix.host_group(client, "Linux servers")
    with pytest.raises(ValueError, match="unknown template"):
        await zabbix.host(client, "web01", host_groups="Linux servers", templates="Ghost tmpl")


async def test_host_links_known_template(client, fake):
    await zabbix.host_group(client, "Linux servers")
    await zabbix.host(
        client, "web01", host_groups="Linux servers", templates="Linux generic"
    )
    assert fake.hosts["web01"]["templates"][0]["templateid"] == "T1"


async def test_host_rejects_unknown_state(client):
    with pytest.raises(ValueError, match="state must be"):
        await zabbix.host(client, "web01", state="presentish")


# --- host_macro --------------------------------------------------------------


async def test_host_macro_creates_then_noop(client, fake):
    hostid = "201"
    report = await zabbix.host_macro(client, hostid, {"name": "SNAPSHOT_READ", "value": "yes"})
    assert report["created"] == ["{$SNAPSHOT_READ}"] and report["changed"] is True
    fake.calls.clear()
    again = await zabbix.host_macro(client, hostid, {"name": "SNAPSHOT_READ", "value": "yes"})
    assert again["changed"] is False
    assert "hostmacro.create" not in fake.calls


async def test_host_macro_updates_on_value_change(client, fake):
    hostid = "202"
    await zabbix.host_macro(client, hostid, {"name": "ROLE", "value": "web"})
    report = await zabbix.host_macro(client, hostid, {"name": "ROLE", "value": "db"})
    assert report["updated"] == ["{$ROLE}"]
    assert fake.macros[hostid]["{$ROLE}"]["value"] == "db"


async def test_host_macro_absent_deletes(client, fake):
    hostid = "203"
    await zabbix.host_macro(client, hostid, {"name": "TEMP", "value": "1"})
    report = await zabbix.host_macro(client, hostid, {"name": "TEMP"}, state="absent")
    assert report["deleted"] == ["{$TEMP}"] and report["changed"] is True


async def test_host_macro_bare_string_name(client, fake):
    report = await zabbix.host_macro(client, "204", "HELLO")
    assert report["created"] == ["{$HELLO}"]


async def test_host_macro_list_of_maps(client, fake):
    report = await zabbix.host_macro(
        client, "205", [{"name": "A", "value": "1"}, {"name": "B", "value": "2"}]
    )
    assert report["created"] == ["{$A}", "{$B}"]


async def test_host_macro_rejects_unknown_state(client):
    with pytest.raises(ValueError, match="state must be"):
        await zabbix.host_macro(client, "1", {"name": "X"}, state="weird")


# --- normalization helpers ---------------------------------------------------


def test_as_names_normalizes():
    assert zabbix_module._as_names("one") == ("one",)
    assert zabbix_module._as_names(["a", "b"]) == ("a", "b")
    assert zabbix_module._as_names(None) == ()


def test_tls_value_words_flags_and_ints():
    assert zabbix_module._tls_value("psk") == 1
    assert zabbix_module._tls_value("cert") == 2
    assert zabbix_module._tls_value("psk|cert") == 3
    assert zabbix_module._tls_value(2) == 2


def test_host_status_words():
    assert zabbix_module._host_status("enabled") == 0
    assert zabbix_module._host_status("disabled") == 1
    assert zabbix_module._host_status(1) == 1


def test_one_macro_wraps_bare_name():
    assert zabbix_module._one_macro({"name": "ROLE"})["macro"] == "{$ROLE}"
    assert zabbix_module._one_macro({"macro": "{$ROLE}"})["macro"] == "{$ROLE}"


def test_one_macro_reads_ansible_field_names():
    entry = zabbix_module._one_macro(
        {"macro_name": "ROLE", "macro_value": "web", "macro_description": "d", "macro_type": 0}
    )
    assert entry == {"macro": "{$ROLE}", "value": "web", "description": "d", "type": 0}


def test_one_macro_requires_name():
    with pytest.raises(ValueError, match="needs a name"):
        zabbix_module._one_macro({"value": "x"})


# --- exports -----------------------------------------------------------------


def test_namespace_exports():
    for name in ("ZabbixClient", "ZabbixAPIError", "connect", "host", "host_group", "host_macro"):
        assert hasattr(zabbix, name), name


def test_top_level_package_exposes_namespace():
    import reemote

    assert reemote.zabbix is zabbix
