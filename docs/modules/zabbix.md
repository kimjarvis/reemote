# zabbix module

::: reemote.modules.zabbix.zabbix
    options:
      show_root_heading: false
      members:
        - connect
        - host
        - host_group
        - host_macro



<!---

`reemote.modules.zabbix.zabbix` is reemote's answer to the community Ansible
modules `zabbix_group`, `zabbix_host` and `zabbix_hostmacro` — the Zabbix API
branch of the `ansible-zabbix-agent` role, the one that was scoped out as the
gap tracked by issue #72. Those tasks run `delegate_to: localhost` with
`become: no`: they never touch the target host, they open an HTTPS connection
from the *controller* to the Zabbix server's `api_jsonrpc.php` endpoint and
register the monitored host there. That is the control-side shape, not the
operation family — the same stance `files.fetch_url` takes for a controller GET
(issue #20) — so this namespace ships plain coroutine functions rather than
`op()` operations that yield command descriptors. A deploy awaits them directly
in `main()` or an `async def` role factory (work identical for every host, the
datadog pattern), or behind a `control` barrier whose input is a gathered fact
(a per-host registration, the `repo_epel`/`control_fetch` pattern).

`ZabbixClient` wraps the JSON-RPC envelope plus the two authentication styles
the community modules expose: an `api_token` sent as an `Authorization: Bearer`
header (Zabbix >= 6.4, no login call) or a `user`/`password` pair exchanged for
a session token by `login` (sent as the `auth` body field thereafter). The TLS
and HTTP basic-auth knobs (`validate_certs`, `http_login_user`/
`http_login_password`) live on the same client because they belong to the single
HTTPS connection it opens; `connect` is the one-shot build-and-login form. Every
call goes through the module-level `_jsonrpc_request` seam — `urllib` in a
worker thread, the POST twin of `files._fetch_url_bytes` — so no HTTP client
dependency is added and tests replace the seam with a canned responder to stay
offline.

`host_group` creates or removes host groups, `host` creates/updates/deletes a
monitored host (resolving group and template names to ids first, mapping the
Ansible TLS/`status`/IPMI word-forms to the integers the API stores, and taking
the host's interface list — including its IP, which `hardware.DefaultIpv4Address`
covers for the default route and a public/private partition of all addresses
does not yet, per issue #64), and `host_macro` reconciles host-level macros from
either the Ansible `macro_name`/`macro_value` fields or the short API names. Each
is idempotent the way an operation gates on a fact: it reads current state
through `hostgroup.get` / `host.get` / `hostmacro.get` before writing anything,
issues a create/update/delete only when the live state actually differs, and
returns a report whose `changed` flag is true only when a call hit the API — so
a second identical pass reports `changed=False` and queues no write.

--->
