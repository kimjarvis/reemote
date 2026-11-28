# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """Control-side Zabbix API operations.
#
# The ``zabbix`` namespace is reemote's answer to the community Ansible modules
# ``zabbix_group``, ``zabbix_host`` and ``zabbix_hostmacro`` (the API branch of
# ``ansible-zabbix-agent``, issue #72). Those tasks run ``delegate_to: localhost``
# with ``become: no``: they never touch the target host, they open an HTTPS
# connection *from the controller* to the Zabbix server's JSON-RPC endpoint and
# register the host there. That is the **control-side** shape, not the operation
# family -- the same stance ``files.fetch_url`` takes for a controller GET (issue
# #20), and the reason this namespace ships plain coroutine functions rather
# than ``op()`` operations that yield command descriptors.
#
# Consequently the three entry points (``host_group``, ``host``, ``host_macro``)
# and the :class:`ZabbixClient` behind them take no ``OpContext``, yield no
# ``Command``, and are awaited directly -- either inside an ``async def`` role
# factory (work identical for every host, the datadog ``await files.fetch_url``
# pattern) or behind a ``control`` barrier (per-host registration whose input is
# a gathered fact, the ``repo_epel``/``control_fetch`` pattern). Each is
# **idempotent** the way an operation gates on a fact: it reads current state
# through ``hostgroup.get`` / ``host.get`` / ``hostmacro.get`` first and issues a
# create/update/delete only when the live state actually differs, then reports
# what it changed.
#
# The one network write is the module-level :func:`_jsonrpc_request` seam --
# ``urllib`` in a worker thread (``asyncio.to_thread``), mirroring
# ``files._fetch_url_bytes`` so no HTTP client dependency is added (the runtime
# set stays asyncssh/distro/pydantic). Tests and the offline example replace that
# seam with a canned JSON-RPC responder and never touch the network.
# """

import asyncio
import base64
import itertools
import json
import ssl
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

__all__ = [
    "ZabbixAPIError",
    "ZabbixClient",
    "connect",
    "host",
    "host_group",
    "host_macro",
]


class ZabbixAPIError(RuntimeError):
    """The Zabbix server returned an ``error`` object for a JSON-RPC call.

    A transport failure (an unreachable server, a non-2xx HTTP response) is
    *not* wrapped here -- it propagates as ``urllib.error.URLError``/
    ``HTTPError`` (both ``OSError`` subclasses), exactly as a control-side
    ``files.fetch_url`` failure does. This error carries the API-level failure:
    bad credentials, a rejected method, a validation message, or a call issued
    without authentication.
    """


def _jsonrpc_request(
    url: str,
    payload: dict[str, Any],
    *,
    validate_certs: bool = True,
    http_auth: tuple[str, str] | None = None,
    bearer: str | None = None,
) -> dict[str, Any]:
    """POST one JSON-RPC ``payload`` to a Zabbix ``url`` and return the reply.

    The single network write behind :class:`ZabbixClient` (the seam
    ``files._fetch_url_bytes``'s POST-shaped twin): the *control* machine opens
    the connection, so the call works when only the controller has egress to the
    Zabbix server. The body is the ``payload`` serialized as JSON with the
    ``application/json-rpc`` content type the API expects; ``http_auth`` adds an
    HTTP basic-auth header (Ansible's ``http_login_user``/``http_login_password``),
    and ``bearer`` an ``Authorization: Bearer`` header (Zabbix >= 6.4 API tokens).
    ``validate_certs=False`` (Ansible's ``validate_certs: false``) uses an SSL
    context that skips certificate verification -- the answer for a
    self-signed frontend. The raw decoded reply dict is returned untouched; the
    client interprets its ``result``/``error``. An unreachable URL or a non-2xx
    response raises ``urllib.error.URLError``/``HTTPError`` (both ``OSError``).
    **Tests replace this function to stay offline.**
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json-rpc")
    if bearer is not None:
        request.add_header("Authorization", f"Bearer {bearer}")
    if http_auth is not None:
        token = base64.b64encode(":".join(http_auth).encode("utf-8")).decode("ascii")
        request.add_header("Authorization", f"Basic {token}")
    context = None if validate_certs else ssl._create_unverified_context()
    with urllib.request.urlopen(request, context=context) as response:
        raw = response.read()
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ZabbixAPIError(
            f"expected a JSON object reply from {url}, got {type(parsed).__name__}"
        )
    return parsed


def _as_names(value: str | Iterable[str] | None) -> tuple[str, ...]:
    """Normalize a name-or-iterable-of-names argument into a hashable tuple.

    Ansible accepts both a single group name and a list; a ``str`` is one name,
    any other iterable is many, and ``None`` is none. Returned as a tuple so the
    caller can freeze it into a plan or hash it without a surprise mutation.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


class ZabbixClient:
    """A logged-in Zabbix JSON-RPC session, driven control-side.

    Thin wrapper over the API's ``{"jsonrpc", "method", "params", "id"}``
    envelope plus the two authentication styles the community modules expose:
    an ``api_token`` (sent as an ``Authorization: Bearer`` header, no login
    call) or a ``user``/``password`` pair exchanged for a session token by
    :meth:`login` (sent as the ``auth`` body field on later calls). TLS and
    HTTP basic-auth knobs (``validate_certs``, ``http_login_user``/
    ``http_login_password``) belong to the same client because they belong to
    the single HTTPS connection it opens.

    All network I/O goes through the module-level :func:`_jsonrpc_request`
    seam inside a worker thread, so a client is safe to await from ``main()``,
    a role factory, or a ``control`` barrier. Tests replace the seam to drive a
    client against a canned server.

    Args:
        url (str): The Zabbix frontend ``api_jsonrpc.php`` endpoint (an
            ``http(s)`` URL).
        user (str | None, optional): Username for ``user.login``. Defaults to
            None (required unless ``api_token`` is given).
        password (str | None, optional): Password for ``user.login``. Defaults
            to None (required unless ``api_token`` is given).
        api_token (str | None, optional): A Zabbix API token (>= 6.4) that
            replaces username/password login. Defaults to None.
        validate_certs (bool, optional): Verify the frontend's TLS certificate.
            ``False`` accepts a self-signed certificate. Defaults to True.
        http_login_user (str | None, optional): HTTP basic-auth user in front
            of the frontend. Defaults to None.
        http_login_password (str | None, optional): HTTP basic-auth password.
            Defaults to None.

    **Raises:**

    ```python
    ZabbixAPIError, OSError, ValueError
    ```
    """

    def __init__(
        self,
        url: str,
        *,
        user: str | None = None,
        password: str | None = None,
        api_token: str | None = None,
        validate_certs: bool = True,
        http_login_user: str | None = None,
        http_login_password: str | None = None,
    ) -> None:
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"url must be an http(s) URL, got: {url!r}")
        if api_token is None and not (user and password):
            raise ValueError("authenticate with either api_token or a user/password pair")
        self.url = url
        self.user = user
        self.password = password
        self.api_token = api_token
        self.validate_certs = validate_certs
        self.http_auth = (
            (http_login_user, http_login_password) if http_login_user is not None else None
        )
        self.session_token: str | None = None
        self._ids = itertools.count(1)

    async def call(self, method: str, params: Any = None) -> Any:
        """Invoke one API ``method`` and return its ``result``.

        Attaches the active credential (the bearer token from ``api_token``, or
        the session token from :meth:`login` in the ``auth`` body field), posts
        through the seam, and unwraps the reply. A reply carrying an ``error``
        object raises :class:`ZabbixAPIError`; a transport failure raises
        ``OSError``.
        """
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": {} if params is None else params,
            "id": next(self._ids),
        }
        bearer: str | None = None
        if self.api_token is not None:
            bearer = self.api_token
        elif self.session_token is not None:
            payload["auth"] = self.session_token
        response = await asyncio.to_thread(
            _jsonrpc_request,
            self.url,
            payload,
            validate_certs=self.validate_certs,
            http_auth=self.http_auth,
            bearer=bearer,
        )
        error = response.get("error")
        if error:
            raise ZabbixAPIError(
                f"{method}: {error.get('message', '')} - {error.get('data', '')}".rstrip(" -")
            )
        return response.get("result")

    async def login(self) -> str | None:
        """Exchange ``user``/``password`` for a session token (no-op with a token).

        Returns the session token, or ``None`` when the client authenticates
        with ``api_token`` and needs no login round-trip.
        """
        if self.api_token is not None:
            return None
        self.session_token = await self.call(
            "user.login", {"user": self.user, "password": self.password}
        )
        return self.session_token

    async def logout(self) -> None:
        """Invalidate a session token obtained from :meth:`login` (best effort)."""
        if self.api_token is not None or self.session_token is None:
            return
        token = self.session_token
        self.session_token = None
        await self.call("user.logout", [token])


async def connect(url: str, **kwargs: Any) -> ZabbixClient:
    """Build a :class:`ZabbixClient` and log it in, ready for API calls.

    Args:
        url (str): The Zabbix ``api_jsonrpc.php`` endpoint.
        **kwargs: Forwarded to :class:`ZabbixClient` (``user``, ``password``,
            ``api_token``, ``validate_certs``, ``http_login_user``,
            ``http_login_password``).

    Returns:
        ZabbixClient: An authenticated client.

    **Raises:**

    ```python
    ZabbixAPIError, OSError, ValueError
    ```
    """

    # The one-shot form of ``client = ZabbixClient(...); await client.login()``
    # for a user/password session; with an ``api_token`` it is simply the
    # constructor (no login call fires).


    client = ZabbixClient(url, **kwargs)
    await client.login()
    return client


async def host_group(
    client: ZabbixClient,
    group_names: str | Iterable[str],
    *,
    state: str = "present",
) -> dict[str, Any]:
    """Create or remove Zabbix host groups idempotently (Ansible ``zabbix_group``).


    Args:
        client (ZabbixClient): An authenticated client.
        group_names (str | Iterable[str]): One group name or a list of them.
        state (str, optional): ``"present"`` to create, ``"absent"`` to remove.
            Defaults to "present".

    Returns:
        dict: ``{"state", "present", "created"/"deleted", "changed"}`` naming
        the groups that already matched and the ones written, with ``changed``
        true only when a call actually hit the API.

    **Raises:**

    ```python
    ZabbixAPIError, OSError, ValueError
    ```

    Examples:
        ```python
        report = await host_group(client, "Linux servers")
        report = await host_group(client, ["Linux servers", "Databases"], state="absent")
        ```
    """

    # Reads which of the requested groups already exist through
    # ``hostgroup.get`` before writing anything, then creates only the missing
    # ones (``hostgroup.create``) -- or, for ``state="absent"``, deletes only the
    # ones present (``hostgroup.delete``). Re-running with the same argument is a
    # no-op.



    if state not in ("present", "absent"):
        raise ValueError(f"state must be 'present' or 'absent', got: {state!r}")
    names = list(_as_names(group_names))
    if not names:
        return {"state": state, "present": [], "changed": False}

    found = await client.call(
        "hostgroup.get", {"output": ["groupid", "name"], "filter": {"name": names}}
    )
    existing = {entry["name"]: entry["groupid"] for entry in found}

    if state == "present":
        created = []
        for name in names:
            if name in existing:
                continue
            result = await client.call("hostgroup.create", {"name": name})
            created.append({"name": name, "groupid": result["groupids"][0]})
        return {
            "state": state,
            "present": sorted(existing),
            "created": created,
            "changed": bool(created),
        }

    absent = [name for name in names if name not in existing]
    group_ids = [gid for name, gid in existing.items() if name in names]
    if group_ids:
        await client.call("hostgroup.delete", group_ids)
    return {
        "state": state,
        "present": absent,
        "deleted": sorted(existing),
        "changed": bool(group_ids),
    }


async def _resolve_group_ids(client: ZabbixClient, group_names: Sequence[str]) -> list[str]:
    """Map host group names to their ``groupid`` values via ``hostgroup.get``."""
    if not group_names:
        return []
    found = await client.call(
        "hostgroup.get", {"output": ["groupid", "name"], "filter": {"name": list(group_names)}}
    )
    by_name = {entry["name"]: entry["groupid"] for entry in found}
    missing = [name for name in group_names if name not in by_name]
    if missing:
        raise ValueError(f"unknown host group(s): {', '.join(missing)}")
    return [by_name[name] for name in group_names]


async def _resolve_template_ids(client: ZabbixClient, names: Sequence[str]) -> list[str]:
    """Map template technical names to ids via ``template.get`` (unknown -> raise)."""
    if not names:
        return []
    found = await client.call(
        "template.get", {"output": ["templateid", "host"], "filter": {"host": list(names)}}
    )
    by_name = {entry["host"]: entry["templateid"] for entry in found}
    missing = [name for name in names if name not in by_name]
    if missing:
        raise ValueError(f"unknown template(s): {', '.join(missing)}")
    return [by_name[name] for name in names]


async def _resolve_proxy_id(client: ZabbixClient, name: str | None) -> str | None:
    """Map a monitoring proxy name to its id via ``proxy.get`` (unknown -> raise)."""
    if name is None:
        return None
    found = await client.call(
        "proxy.get", {"output": ["proxyid", "host"], "filter": {"host": [name]}}
    )
    if not found:
        raise ValueError(f"unknown proxy: {name!r}")
    return found[0]["proxyid"]


# Ansible-style -> integer mappings for the enum-valued host fields.
_STATUS_WORDS = {"enabled": 0, "disabled": 1, "monitored": 0, "unmonitored": 1}
_TLS_WORDS = {"no": 0, "psk": 1, "cert": 2}
_IPMI_AUTHTYPE_WORDS = {
    "none": 0,
    "raw": 1,
    "otp": 2,
    "device": 3,
    "md2": 4,
    "md5": 5,
    "straight": 6,
    "chen": 7,
    "smic": 8,
    "password": 90,
    "oem": 91,
}


def _host_status(value: str | int) -> int:
    return value if isinstance(value, int) else _STATUS_WORDS[str(value).lower()]


def _tls_value(value: str | int) -> int:
    # The API accepts bit flags; a combined "psk|cert" string sums the flags.
    if isinstance(value, int):
        return value
    lowered = str(value).lower()
    if "|" in lowered:
        return sum(_TLS_WORDS[part.strip()] for part in lowered.split("|"))
    return _TLS_WORDS[lowered]


def _ipmi_authtype_value(value: str | int) -> int:
    return value if isinstance(value, int) else _IPMI_AUTHTYPE_WORDS[str(value).lower()]


def _host_params(
    host_name: str,
    group_ids: list[str],
    interfaces: Sequence[Mapping[str, Any]],
    *,
    visible_name: str | None,
    description: str | None,
    status: str | int | None,
    template_ids: list[str],
    proxy_id: str | None,
    tls_connect: str | int | None,
    tls_accept: str | int | None,
    tls_psk_identity: str | None,
    tls_psk: str | None,
    ipmi_authtype: str | int | None,
    inventory: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build the ``host.create``/``host.update`` params dict from the arguments.

    Only fields the caller actually set are emitted, so an ``update`` sends the
    desired shape without blanking untouched columns. ``status``/``tls_*``/
    ``ipmi_authtype`` accept the Ansible names (``enabled``/``disabled``, the
    ``no``/``psk``/``cert`` words) or raw ints and normalize to the integers the
    API stores.
    """
    params: dict[str, Any] = {
        "host": host_name,
        "groups": [{"groupid": gid} for gid in group_ids],
        "interfaces": [dict(interface) for interface in interfaces],
    }
    if visible_name is not None:
        params["visible_name"] = visible_name
    if description is not None:
        params["description"] = description
    if status is not None:
        params["status"] = _host_status(status)
    if template_ids:
        params["templates"] = [{"templateid": tid} for tid in template_ids]
    if proxy_id is not None:
        params["proxyid"] = proxy_id
    if tls_connect is not None:
        params["tls_connect"] = _tls_value(tls_connect)
    if tls_accept is not None:
        params["tls_accept"] = _tls_value(tls_accept)
    if tls_psk_identity is not None:
        params["tls_psk_identity"] = tls_psk_identity
    if tls_psk is not None:
        params["tls_psk"] = tls_psk
    if ipmi_authtype is not None:
        params["ipmi_authtype"] = _ipmi_authtype_value(ipmi_authtype)
    if inventory is not None:
        params["inventory"] = {"inventory": {str(k): str(v) for k, v in inventory.items()}}
    return params


def _interfaces_match(
    live: Sequence[Mapping[str, Any]], wanted: Sequence[Mapping[str, Any]]
) -> bool:
    """Whether every wanted interface has a live twin on type/main/address."""

    def signature(iface: Mapping[str, Any]) -> tuple:
        return (
            str(iface.get("type")),
            str(iface.get("main")),
            str(iface.get("ip", "")),
            str(iface.get("dns", "")),
            str(iface.get("port", "")),
        )

    live_sigs = {signature(iface) for iface in live}
    return all(signature(wanted_iface) in live_sigs for wanted_iface in wanted)


def _host_matches(entry: Mapping[str, Any], params: Mapping[str, Any]) -> bool:
    """Whether the live host ``entry`` already reflects the desired ``params``.

    Compares only the fields present in ``params`` (an update sends the full
    desired shape, so ``groups``/``interfaces``/``status``/``tls_*``/
    ``description`` are checked when requested). Interfaces are matched as a
    set on their address-bearing columns; groups as a subset of ids. This is the
    fact-gate that makes a repeated identical call report ``unchanged``.
    """
    if "description" in params and entry.get("description", "") != params["description"]:
        return False
    if "status" in params and str(entry.get("status")) != str(params["status"]):
        return False
    for field in ("tls_connect", "tls_accept", "tls_psk_identity", "tls_psk"):
        if field in params and str(entry.get(field, "")) != str(params[field]):
            return False
    if "groups" in params:
        have = {group["groupid"] for group in entry.get("groups", [])}
        want = {group["groupid"] for group in params["groups"]}
        if want and not want.issubset(have):
            return False
    if "interfaces" in params:
        return _interfaces_match(entry.get("interfaces", []), params["interfaces"])
    return True


async def host(
    client: ZabbixClient,
    host_name: str,
    *,
    host_groups: str | Iterable[str] = (),
    interfaces: Sequence[Mapping[str, Any]] = (),
    state: str = "present",
    visible_name: str | None = None,
    description: str | None = None,
    status: str | int | None = None,
    templates: str | Iterable[str] = (),
    proxy: str | None = None,
    tls_connect: str | int | None = None,
    tls_accept: str | int | None = None,
    tls_psk_identity: str | None = None,
    tls_psk: str | None = None,
    ipmi_authtype: str | int | None = None,
    inventory: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create, update or delete a monitored Zabbix host (Ansible ``zabbix_host``).

    Args:
        client (ZabbixClient): An authenticated client.
        host_name (str): The technical host name.
        host_groups (str | Iterable[str], optional): Group name(s) the host
            belongs to. Defaults to () (only meaningful for creation).
        interfaces (Sequence[Mapping], optional): Host interface dicts (``type``,
            ``main``, ``ip``/``dns``, ``port``). Defaults to ().
        state (str, optional): ``"present"`` or ``"absent"``. Defaults to
            "present".
        visible_name (str | None, optional): The display name. Defaults to None
            (the API defaults it to ``host_name`` on create).
        description (str | None, optional): Free-text host description.
        status (str | int | None, optional): ``"enabled"``/``"disabled"`` (or
            0/1).
        templates (str | Iterable[str], optional): Template technical names to
            link, resolved to ids via ``template.get``.
        proxy (str | None, optional): Monitoring proxy name, resolved via
            ``proxy.get``.
        tls_connect (str | int | None, optional): Connections to host
            (``no``/``psk``/``cert`` or a bit flag).
        tls_accept (str | int | None, optional): Accept connections from host.
        tls_psk_identity (str | None, optional): TLS-PSK identity.
        tls_psk (str | None, optional): TLS-PSK key (hex).
        ipmi_authtype (str | int | None, optional): IPMI authentication
            algorithm name or id.
        inventory (Mapping | None, optional): Manual inventory fields as a
            name/value mapping.

    Returns:
        dict: ``{"host_name", "hostid", "state", "action", "changed"}`` where
        ``action`` is ``"created"``/``"updated"``/``"deleted"``/``"unchanged"``/
        ``"absent"``.

    **Raises:**

    ```python
    ZabbixAPIError, OSError, ValueError
    ```

    Examples:
        ```python
        await host(
            client,
            "web01",
            host_groups="Linux servers",
            interfaces=[{"type": 1, "main": 1, "use_ip": 1, "ip": "10.0.0.5", "dns": "", "port": "10050"}],
        )
        await host(client, "web01", host_groups="Linux servers", status="disabled")
        ```
    """

    # Looks the host up by name with ``host.get`` (pulling back groups,
    # interfaces and the TLS/inventory columns it compares), then does the least:
    # a missing ``"present"`` host is created with ``host.create``, an existing one
    # whose desired fields differ is written with ``host.update``, a ``"absent"``
    # host is removed with ``host.delete``, and anything already matching is left
    # untouched -- so a second identical call reports ``changed=False``. Host
    # group and template *names* are resolved to ids first, and an unknown group
    # raises before any write.
    #
    # The host's IP (Ansible's ``zabbix_agent_ip``, derived from
    # ``ansible_all_ipv4_addresses`` plus the ``ipaddr`` filter) is the caller's
    # job -- pass it in an ``interfaces`` entry. The default-route case is
    # ``hardware.DefaultIpv4Address`` (issue #64); a public/private partition of
    # *all* addresses still has no fact and is out of scope here.




    if state not in ("present", "absent"):
        raise ValueError(f"state must be 'present' or 'absent', got: {state!r}")

    current = await client.call(
        "host.get",
        {
            "output": "extend",
            "selectGroups": ["groupid", "name"],
            "selectInterfaces": ["interfaceid", "type", "main", "ip", "dns", "port", "use_ip"],
            "filter": {"host": [host_name]},
        },
    )
    entry = current[0] if current else None

    if state == "absent":
        if entry is None:
            return {
                "host_name": host_name,
                "hostid": None,
                "state": state,
                "action": "absent",
                "changed": False,
            }
        await client.call("host.delete", [entry["hostid"]])
        return {
            "host_name": host_name,
            "hostid": entry["hostid"],
            "state": state,
            "action": "deleted",
            "changed": True,
        }

    group_ids = await _resolve_group_ids(client, _as_names(host_groups))
    template_ids = await _resolve_template_ids(client, _as_names(templates))
    proxy_id = await _resolve_proxy_id(client, proxy)

    params = _host_params(
        host_name,
        group_ids,
        interfaces,
        visible_name=visible_name,
        description=description,
        status=status,
        template_ids=template_ids,
        proxy_id=proxy_id,
        tls_connect=tls_connect,
        tls_accept=tls_accept,
        tls_psk_identity=tls_psk_identity,
        tls_psk=tls_psk,
        ipmi_authtype=ipmi_authtype,
        inventory=inventory,
    )

    if entry is None:
        result = await client.call("host.create", params)
        return {
            "host_name": host_name,
            "hostid": result["hostids"][0],
            "state": state,
            "action": "created",
            "changed": True,
        }

    if _host_matches(entry, params):
        return {
            "host_name": host_name,
            "hostid": entry["hostid"],
            "state": state,
            "action": "unchanged",
            "changed": False,
        }

    params["hostid"] = entry["hostid"]
    await client.call("host.update", params)
    return {
        "host_name": host_name,
        "hostid": entry["hostid"],
        "state": state,
        "action": "updated",
        "changed": True,
    }


def _one_macro(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a single macro mapping to the API's field names.

    The Ansible field names (``macro_name``, ``macro_value``,
    ``macro_description``, ``macro_type``) and the short API names
    (``name``/``value``/``description``/``type``) are both read, and a bare name
    is wrapped in Zabbix's ``{$...}`` macro syntax.
    """
    name = entry.get("macro") or entry.get("macro_name") or entry.get("name")
    if not name:
        raise ValueError(f"macro entry needs a name: {entry!r}")
    macro = name if name.startswith("{$") else f"{{${name}}}"
    return {
        "macro": macro,
        "value": entry.get("value", entry.get("macro_value", "")),
        "description": entry.get("description", entry.get("macro_description")),
        "type": entry.get("type", entry.get("macro_type")),
    }


def _normalize_macros(
    macros: str | Iterable[Mapping[str, Any]] | Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Coerce the accepted macro spellings into ``{macro, value, description, type}``.

    A mapping is one macro, a ``str`` is a macro name with an empty value, and a
    list is many of either.
    """
    if isinstance(macros, str):
        return [_one_macro({"name": macros, "value": ""})]
    if isinstance(macros, Mapping):
        return [_one_macro(macros)]
    return [_one_macro(entry) for entry in macros]


async def host_macro(
    client: ZabbixClient,
    host_id: str,
    macros: str | Iterable[Mapping[str, Any]] | Mapping[str, Any],
    *,
    state: str = "present",
) -> dict[str, Any]:
    """Create, update or delete host-level macros (Ansible ``zabbix_hostmacro``).


    Args:
        client (ZabbixClient): An authenticated client.
        host_id (str): The host's ``hostid`` the macros belong to.
        macros: One macro mapping, a list of them, or a macro name ``str``.
        state (str, optional): ``"present"`` to create/update, ``"absent"`` to
            delete by name. Defaults to "present".

    Returns:
        dict: ``{"hostid", "state", "created", "updated", "deleted", "changed"}``
        naming each macro by its (normalized) ``{$NAME}`` macro.

    **Raises:**

    ```python
    ZabbixAPIError, OSError, ValueError
    ```

    Examples:
        ```python
        await host_macro(client, hostid, {"name": "SNAPSHOT_READ", "value": "yes"})
        await host_macro(client, hostid, zabbix_macros, state="absent")
        ```
    """

    # Reads the host's current macros with ``hostmacro.get`` and reconciles: a
    # ``"present"`` macro missing from the host is created, one present with a
    # different value/description/type is updated, and a ``"absent"`` macro is
    # deleted. ``macros`` accepts a single ``{"name": ..., "value": ...}``-style
    # mapping, a list of them, or (for the one-macro case) a bare macro name whose
    # ``value``/``description``/``type`` are read from the same mapping -- the
    # shape the role's ``zabbix_macros`` list already produces.


    if state not in ("present", "absent"):
        raise ValueError(f"state must be 'present' or 'absent', got: {state!r}")

    desired = _normalize_macros(macros)
    live = await client.call("hostmacro.get", {"output": "extend", "hostids": [host_id]})
    by_macro = {entry["macro"]: entry for entry in live}

    created: list[str] = []
    updated: list[str] = []
    deleted: list[str] = []

    for wanted in desired:
        macro = wanted["macro"]
        entry = by_macro.get(macro)
        if state == "present":
            if entry is None:
                await client.call("hostmacro.create", _macro_params(host_id, wanted))
                created.append(macro)
            elif _macro_differs(entry, wanted):
                await client.call("hostmacro.update", _macro_params(host_id, wanted))
                updated.append(macro)
        elif entry is not None:
            await client.call("hostmacro.delete", [entry["hostmacroid"]])
            deleted.append(macro)

    return {
        "hostid": host_id,
        "state": state,
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "changed": bool(created or updated or deleted),
    }


def _macro_params(host_id: str, wanted: Mapping[str, Any]) -> dict[str, Any]:
    """Build the ``hostmacro.create``/``.update`` params for one normalized macro."""
    params: dict[str, Any] = {
        "hostid": host_id,
        "macro": wanted["macro"],
        "value": wanted["value"],
    }
    if wanted.get("description") is not None:
        params["description"] = wanted["description"]
    if wanted.get("type") is not None:
        params["type"] = wanted["type"]
    return params


def _macro_differs(entry: Mapping[str, Any], wanted: Mapping[str, Any]) -> bool:
    """Whether a live macro differs from the desired value/description/type."""
    if entry.get("value") != wanted["value"]:
        return True
    if wanted.get("description") is not None and entry.get("description", "") != wanted["description"]:
        return True
    return bool(
        wanted.get("type") is not None and str(entry.get("type")) != str(wanted["type"])
    )
