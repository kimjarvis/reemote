# Module examples: zabbix

Scripts under `examples/modules/zabbix/` — the control-side Zabbix API
namespace (issue #72), whose `host_group`/`host`/`host_macro` coroutines are the
port of the community Ansible modules the `zabbix_agent` role's API branch was
scoped out to. Unlike every other module page here, these are **not** `op()`
operations: they run on the controller against the server's JSON-RPC endpoint, so
the script awaits them directly rather than driving a two-phase deploy. See the
[Examples overview](../../examples.md) for the shared error-reporting contract and
the [`zabbix` module page](../../modules/zabbix.md) for the API itself.

## Registering a monitored host through the API

The one script demonstrates the whole registration a real role would perform and
its idempotence: create the host group (`zabbix_group`), register the host with
its agent interface (`zabbix_host`), push a macro onto it (`zabbix_hostmacro`),
then re-run each call to show the fact-gated no-op (`changed=False`, no create
or update issued), and finally remove the host and the group with
`state="absent"`. Because there is no live Zabbix server in the demo inventory,
the script replaces the single network seam
`reemote.modules.zabbix.zabbix._jsonrpc_request` with a small in-memory fake that
speaks just the methods used — the offline sibling of the way
`control_fetch.py` stubs `files.fetch_url` — so the example runs entirely in
memory and its whole path is exercised by the pytest suite. Deleting the stub and
pointing `connect(...)` at a real `api_jsonrpc.php` with real credentials is the
only change needed to talk to a live server; the host IP that Ansible derived
from `ipaddr` is simply passed in an `interfaces` entry here.

::: zabbix_api
    options:
      members: []
