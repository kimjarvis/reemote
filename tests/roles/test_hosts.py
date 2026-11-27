# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``hosts`` role (design doc §17.5).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, hosts(v), host_factory=...)``. The load-bearing
assertion is the same as every migrated role's: run the role, then run it
*again* against the same persistent hosts, and the second pass must report no
change anywhere -- here that covers both ``files.install`` content guards and
both ``files.file`` metadata companions.

Two simulation bridges are set explicitly, in ``make_hosts``:

* ``default_ipv4_addr`` seeds the ``DefaultIpv4Address`` fact seam (the
  ``ansible_default_ipv4`` counterpart); the empty-dict case models a host
  with no default route.
* ``login_path_visible`` answers the traversal-visibility question the
  file-read facts ask before an elevated re-probe, so the in-memory host
  never receives a probe shell it cannot model.

No fixtures are needed: the role reads no package state and fetches nothing
controller-side -- both bodies are rendered control-side from the vars and
the barrier result.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.roles import HostsEntry, HostsVars, hosts
from reemote.roles.hosts import (
    HOSTNAME_PATH,
    HOSTS_PATH,
    HostsPlan,
    render_hosts,
    resolve_hosts,
    short_hostname,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from tests.simulated_host import SimulatedHost

TARGET = "web1.example.com"
ADDRESS = "192.168.1.100"
DEFAULT_SEAM = {
    "address": ADDRESS,
    "mask_bits": 24,
    "interface": "eth0",
    "gateway": "192.168.1.1",
}

REQUEST_NAMES = (
    "Set up /etc/hostname.",
    "Ensure /etc/hostname ownership and mode.",
    "Set up /etc/hosts.",
    "Ensure /etc/hosts ownership and mode.",
)

# The role's default /etc/hosts body for this host: header, loopback line,
# the default-route line (address from the barrier, FQDN plus short name),
# two blank lines, the fixed IPv6 block, one trailing newline. Byte-equal to
# an Ansible-defaults render of the upstream templates/hosts.j2 except for
# the header text (deviation D3 in the role module).
EXPECTED_HOSTS = """# Reemote managed
127.0.0.1 localhost.localdomain localhost
192.168.1.100 web1.example.com web1


# The following lines are desirable for IPv6 capable hosts
::1     ip6-localhost ip6-loopback
fe00::0 ip6-localnet
ff00::0 ip6-mcastprefix
ff02::1 ip6-allnodes
ff02::2 ip6-allrouters
"""
EXPECTED_HOSTNAME = "web1\n"


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one RedHat host to be skipped.

    The role variables ride on the entry's ``roles`` mapping under ``"hosts"``,
    written with the model's bare field names, which is what
    ``resolve_vars(HostsVars, "hosts", inventory)`` binds. The target's name is
    dotted so every render proves the short-name split. With no overrides the
    role defaults apply.
    """
    entry: dict[str, Any] = {
        "host": TARGET,
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.10",
            "username": "supervisor",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "webservers",
        "roles": {"hosts": dict(overrides)},
    }
    return [
        entry,
        {
            "host": "centos",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "db",
        },
    ]


def _regular_file(host: SimulatedHost, path: str, text: str, mode: int = 644) -> None:
    """Seed an existing regular file: content, existence and stat metadata."""
    data = text.encode("utf-8")
    host.remote_files[path] = data
    host.existing_files.add(path)
    host.file_stats[path] = {
        "user": "root",
        "group": "root",
        "mode": mode,
        "size": len(data),
    }


def make_hosts(
    inventory: list[dict[str, Any]],
    *,
    converged: bool = False,
    mode: int = 644,
    no_route: bool = False,
) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts.

    Only the Ubuntu target gets the role-relevant seeding: the RedHat entry
    keeps its stock simulated state, so a role that reached it would be caught
    by the untouched-store assertions. ``converged`` pre-places both rendered
    files (what a rerun sees), ``mode`` drifts their file mode, and
    ``no_route`` empties the default-address seam.
    """
    hosts_map: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            host.default_ipv4_addr = {} if no_route else dict(DEFAULT_SEAM)
            # The file-read facts' traversal gate, answered offline seam-first
            # so the simulated shell never sees a hidden-parent probe.
            host.login_path_visible = lambda path: True
            if converged:
                _regular_file(host, HOSTNAME_PATH, EXPECTED_HOSTNAME, mode)
                _regular_file(host, HOSTS_PATH, EXPECTED_HOSTS, mode)
        hosts_map[host.name] = host
    return hosts_map


async def run_role(
    inventory: list[dict[str, Any]],
    hosts_map: dict[str, SimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve the role's variables and run one wave pass against persistent hosts.

    The same ``Host`` objects come back for every entry, so state survives from
    one pass to the next -- which is the whole point of the second run.
    """
    v = resolve_vars(HostsVars, "hosts", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts_map[entry["host"]]

    report, _hosts = await run_waves(
        inventory, hosts(v), host_factory=factory, dry=dry
    )
    return report


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def entry_named(report: Any, name: str, host: str = TARGET) -> Any:
    """The one log entry with *name* on *host*."""
    return next(
        entry
        for entry in report.merged().log
        if entry.name == name and entry.host == host
    )


def host_records(report: Any, host: str) -> list[Any]:
    """Every record (request step or barrier) attributed to *host*."""
    return [record for record in report.records if record.host == host]


# --- the first pass writes both files -----------------------------------------


async def test_first_pass_writes_both_files():
    inventory = make_inventory()
    hosts_map = make_hosts(inventory)
    report = await run_role(inventory, hosts_map)
    host = hosts_map[TARGET]

    # Both installs ran and changed; the companions no-op because the install
    # commands already carried owner/group/mode.
    assert entry_named(report, REQUEST_NAMES[0]).changed
    assert not entry_named(report, REQUEST_NAMES[1]).changed
    assert entry_named(report, REQUEST_NAMES[2]).changed
    assert not entry_named(report, REQUEST_NAMES[3]).changed

    assert host.remote_files[HOSTNAME_PATH] == EXPECTED_HOSTNAME.encode("utf-8")
    assert host.remote_files[HOSTS_PATH] == EXPECTED_HOSTS.encode("utf-8")
    stats = host.file_stats[HOSTS_PATH]
    assert stats["user"] == "root"
    assert stats["group"] == "root"
    assert stats["mode"] == 644


async def test_barrier_then_one_request_wave():
    """The whole task list is one pass: one fact barrier, then four requests.

    The old two-deploy split (gather, then factory) is the
    ``DefaultIpv4Address`` barrier instead: the driver runs it mid-stream and
    resumes the body with the address.
    """
    inventory = make_inventory()
    hosts_map = make_hosts(inventory)
    report = await run_role(inventory, hosts_map)

    barriers = [
        record.barrier for record in host_records(report, TARGET) if record.barrier
    ]
    assert len(barriers) == 1
    assert type(barriers[0]).__name__ == "FactRequest"

    ordered = [entry.name for entry in report.merged().log if entry.host == TARGET]
    assert ordered == list(REQUEST_NAMES)


async def test_non_ubuntu_host_emits_nothing():
    """The family guard is the body's opening ``if``, not a ``when`` on each op.

    A host outside the ``"Ubuntu"`` family returns before it yields anything,
    so it records no request and no barrier at all.
    """
    inventory = make_inventory()
    hosts_map = make_hosts(inventory)
    report = await run_role(inventory, hosts_map)

    skipped = hosts_map["centos"]
    assert host_records(report, "centos") == []
    assert [e for e in report.merged().log if e.host == "centos"] == []
    assert skipped.remote_files == {}
    assert skipped.existing_files == set()


# --- the second pass is a no-op ------------------------------------------------


async def test_second_pass_changes_nothing():
    inventory = make_inventory()
    hosts_map = make_hosts(inventory)
    first = await run_role(inventory, hosts_map)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts_map)
    assert changed_entries(second) == []
    assert (
        entry_named(second, REQUEST_NAMES[0]).reason
        == f"content already up to date ({HOSTNAME_PATH})"
    )
    assert (
        entry_named(second, REQUEST_NAMES[2]).reason
        == f"content already up to date ({HOSTS_PATH})"
    )


async def test_converged_host_is_a_full_noop():
    """Both files already rendered: every request no-ops from the first pass."""
    inventory = make_inventory()
    hosts_map = make_hosts(inventory, converged=True)
    report = await run_role(inventory, hosts_map)

    assert changed_entries(report) == []
    assert [e.name for e in report.merged().log if e.host == TARGET] == list(
        REQUEST_NAMES
    )


async def test_dry_preview_parks_at_the_barrier():
    """``dry`` is a whole-run preview that stops at the first barrier (§6.3).

    The role's first item is the address fact, so the preview parks there: no
    barrier runs and no request is applied.
    """
    inventory = make_inventory()
    hosts_map = make_hosts(inventory)
    host = hosts_map[TARGET]

    report = await run_role(inventory, hosts_map, dry=True)

    assert report.merged().log == []
    assert host.remote_files == {}
    assert host.existing_files == set()
    assert "barrier: DefaultIpv4Address \u2192 unknown until run" in report.tree()


# --- metadata drift (deviation D8) ----------------------------------------------


async def test_mode_drift_converges_via_the_companion():
    """Content equal but mode drifted: the installs no-op (content-only
    idempotence), the companions chmod -- Ansible's ``template`` re-applied
    owner/mode every run, split across the pair."""
    inventory = make_inventory()
    hosts_map = make_hosts(inventory, converged=True, mode=600)
    report = await run_role(inventory, hosts_map)

    assert not entry_named(report, REQUEST_NAMES[0]).changed
    assert entry_named(report, REQUEST_NAMES[1]).changed
    assert not entry_named(report, REQUEST_NAMES[2]).changed
    assert entry_named(report, REQUEST_NAMES[3]).changed
    assert hosts_map[TARGET].file_stats[HOSTS_PATH]["mode"] == 644

    second = await run_role(inventory, hosts_map)
    assert changed_entries(second) == []


# --- variables on the default path -----------------------------------------------


async def test_additional_hosts_are_appended():
    inventory = make_inventory(
        additional_hosts=[
            {"address": "10.0.0.5", "hostnames": ["db.example.com", "db"]},
        ],
    )
    hosts_map = make_hosts(inventory)
    await run_role(inventory, hosts_map)

    body = hosts_map[TARGET].remote_files[HOSTS_PATH].decode("utf-8")
    assert body == EXPECTED_HOSTS.replace(
        "\n\n\n# The following",
        "\n10.0.0.5 db.example.com db\n\n\n# The following",
    )

    second = await run_role(inventory, hosts_map)
    assert changed_entries(second) == []


async def test_default_ipv4_address_override_skips_the_barrier():
    """The per-host variable replaces the ``ansible_default_ipv4`` lookup."""
    inventory = make_inventory(default_ipv4_address="10.9.8.7")
    hosts_map = make_hosts(inventory, no_route=True)
    report = await run_role(inventory, hosts_map)

    assert not [r for r in host_records(report, TARGET) if r.barrier]
    body = hosts_map[TARGET].remote_files[HOSTS_PATH].decode("utf-8")
    assert f"10.9.8.7 {TARGET} web1" in body


async def test_hostname_content_override_is_verbatim():
    inventory = make_inventory(hostname_content="custom-host\n")
    hosts_map = make_hosts(inventory)
    await run_role(inventory, hosts_map)

    assert hosts_map[TARGET].remote_files[HOSTNAME_PATH] == b"custom-host\n"
    # The hosts file still renders from the fact: only hostname_content is set.
    assert hosts_map[TARGET].remote_files[HOSTS_PATH] == EXPECTED_HOSTS.encode("utf-8")


async def test_hosts_content_override_skips_the_barrier():
    """D4/D5: a verbatim body needs no address, so the host parks on nothing."""
    inventory = make_inventory(hosts_content="10.0.0.1 custom\n")
    hosts_map = make_hosts(inventory)
    report = await run_role(inventory, hosts_map)

    assert not [r for r in host_records(report, TARGET) if r.barrier]
    assert hosts_map[TARGET].remote_files[HOSTS_PATH] == b"10.0.0.1 custom\n"
    # The override wave starts at wave 0 -- the tree has no barrier wave.
    assert "barrier" not in report.tree()


# --- the fail-fast gate -----------------------------------------------------------


async def test_no_default_route_fails_fast():
    """Fact answers nothing and no override covers it: raise before writing."""
    inventory = make_inventory()
    hosts_map = make_hosts(inventory, no_route=True)

    with pytest.raises(ValueError, match="no default IPv4 address"):
        await run_role(inventory, hosts_map)

    host = hosts_map[TARGET]
    assert host.remote_files == {}
    assert host.existing_files == set()


# --- the pure half, unit-tested directly --------------------------------------------


def test_short_hostname():
    assert short_hostname("web1.example.com") == "web1"
    assert short_hostname("plain") == "plain"
    assert short_hostname("a.b.c") == "a"


def test_render_hosts_shape():
    entries = (
        HostsEntry(address="127.0.0.1", hostnames=("localhost",)),
        HostsEntry(address=ADDRESS, hostnames=(TARGET, "web1")),
    )
    rendered = render_hosts(entries)

    assert rendered.startswith("# Reemote managed\n127.0.0.1 localhost\n")
    assert f"{ADDRESS} {TARGET} web1\n\n\n# The following" in rendered
    assert rendered.endswith("ff02::2 ip6-allrouters\n")
    assert rendered.count("\n\n\n") == 1


def test_resolve_hosts_defaults():
    plan = resolve_hosts(HostsVars(), TARGET, ADDRESS)

    assert plan == HostsPlan(
        hostname_content=EXPECTED_HOSTNAME,
        hosts_content=EXPECTED_HOSTS,
        file_group="root",
    )
    assert hash(plan)  # frozen and hashable, as a deploy body's local must be


def test_resolve_hosts_overrides():
    vars_ = HostsVars(
        hosts_content="verbatim\n",
        hostname_content="name\n",
        file_group="wheel",
    )
    # The address is only needed when the default body is rendered.
    plan = resolve_hosts(vars_, TARGET, None)

    assert plan.hosts_content == "verbatim\n"
    assert plan.hostname_content == "name\n"
    assert plan.file_group == "wheel"


def test_resolve_hosts_default_hostnames_override():
    vars_ = HostsVars(default_ipv4_hostnames=("web.example.com",))
    plan = resolve_hosts(vars_, TARGET, ADDRESS)

    assert f"{ADDRESS} web.example.com\n" in plan.hosts_content
    # The short name drops out: the override replaces the whole tuple.
    assert f"{ADDRESS} web.example.com web1" not in plan.hosts_content


def test_resolve_hosts_without_address_raises():
    with pytest.raises(ValueError, match="needs a default IPv4 address"):
        resolve_hosts(HostsVars(), TARGET, None)


# --- the variables model -------------------------------------------------------------


def test_inventory_binds_the_prefixed_variables():
    inventory = make_inventory(hostname_content="bound\n")
    v = resolve_vars(HostsVars, "hosts", inventory)

    assert v[TARGET].hostname_content == "bound\n"
    # The other role's host keeps the defaults: no hosts_ keys, nothing bound.
    assert v["centos"] == HostsVars()


def test_entry_rejects_bad_values():
    with pytest.raises(ValidationError, match="non-empty address"):
        HostsEntry(address="", hostnames=("a",))
    with pytest.raises(ValidationError, match="non-empty hostname"):
        HostsEntry(address="10.0.0.1", hostnames=())
    with pytest.raises(ValidationError, match="non-empty hostname"):
        HostsEntry(address="10.0.0.1", hostnames=("ok", ""))


def test_vars_reject_bad_values():
    with pytest.raises(ValidationError, match="non-empty or null"):
        HostsVars(default_ipv4_address="")
    with pytest.raises(ValidationError, match="non-empty names or null"):
        HostsVars(default_ipv4_hostnames=("web1", ""))
    with pytest.raises(ValidationError, match="non-empty group name"):
        HostsVars(file_group="")


def test_vars_are_frozen_and_reject_unknown_fields():
    v = HostsVars()
    with pytest.raises(ValidationError):
        v.file_group = "wheel"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        HostsVars(hosts_file_group="wheel")  # the Ansible name, not the field


def test_typoed_role_variable_is_reported_not_ignored():
    inventory = make_inventory(additional_host=[{"address": "10.0.0.2"}])
    hosts_map = make_hosts(inventory)

    with pytest.raises(RoleVarsError, match="additional_host"):
        resolve_vars(HostsVars, "hosts", inventory)
    assert hosts_map[TARGET].remote_files == {}


# --- the wave tree (§7.2) --------------------------------------------------------------

# Pinned from a real simulated run (``pytest tests/roles/test_hosts.py -k
# tree`` prints the diff against them). The spool directory hashes are derived
# from the destination path, so they are stable across runs.
FIRST_PASS_TREE = """(root)
  wave 0
    barrier: DefaultIpv4Address → {'address': '192.168.1.100', 'mask_bits': 24, 'interface': 'eth0', 'gateway': '192.168.1.1'}
  wave 1
    web1.example.com: Set up /etc/hostname.    [changed: content /etc/hostname is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-d9c5f609a163521e/content
        $ sudo sh -c 'mkdir -p /etc && cp /tmp/reemote-install-d9c5f609a163521e/content /etc/hostname && chown root:root /etc/hostname && chmod 644 /etc/hostname && rm -rf /tmp/reemote-install-d9c5f609a163521e'
    web1.example.com: Ensure /etc/hostname ownership and mode. [no change: file /etc/hostname already exists]
    web1.example.com: Set up /etc/hosts.       [changed: content /etc/hosts is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-77dbc373a1fc690f/content
        $ sudo sh -c 'mkdir -p /etc && cp /tmp/reemote-install-77dbc373a1fc690f/content /etc/hosts && chown root:root /etc/hosts && chmod 644 /etc/hosts && rm -rf /tmp/reemote-install-77dbc373a1fc690f'
    web1.example.com: Ensure /etc/hosts ownership and mode. [no change: file /etc/hosts already exists]"""

CONVERGED_TREE = """(root)
  wave 0
    barrier: DefaultIpv4Address → {'address': '192.168.1.100', 'mask_bits': 24, 'interface': 'eth0', 'gateway': '192.168.1.1'}
  wave 1
    web1.example.com: Set up /etc/hostname.    [no change: content already up to date (/etc/hostname)]
    web1.example.com: Ensure /etc/hostname ownership and mode. [no change: file /etc/hostname already exists]
    web1.example.com: Set up /etc/hosts.       [no change: content already up to date (/etc/hosts)]
    web1.example.com: Ensure /etc/hosts ownership and mode. [no change: file /etc/hosts already exists]"""

OVERRIDE_TREE = """(root)
  wave 0
    web1.example.com: Set up /etc/hostname.    [changed: content /etc/hostname is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-d9c5f609a163521e/content
        $ sudo sh -c 'mkdir -p /etc && cp /tmp/reemote-install-d9c5f609a163521e/content /etc/hostname && chown root:root /etc/hostname && chmod 644 /etc/hostname && rm -rf /tmp/reemote-install-d9c5f609a163521e'
    web1.example.com: Ensure /etc/hostname ownership and mode. [no change: file /etc/hostname already exists]
    web1.example.com: Set up /etc/hosts.       [changed: content /etc/hosts is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-77dbc373a1fc690f/content
        $ sudo sh -c 'mkdir -p /etc && cp /tmp/reemote-install-77dbc373a1fc690f/content /etc/hosts && chown root:root /etc/hosts && chmod 644 /etc/hosts && rm -rf /tmp/reemote-install-77dbc373a1fc690f'
    web1.example.com: Ensure /etc/hosts ownership and mode. [no change: file /etc/hosts already exists]"""


async def test_tree_snapshot():
    """The whole run as one tree: the barrier wave, then the four requests.

    The second tree is what a rerun prints -- same two waves, every line a
    no-change -- and it is the preview ``dry`` deliberately refuses to fake.
    With ``hosts_content`` set there is no barrier wave at all: the requests
    start the tree.
    """
    inventory = make_inventory()
    hosts_map = make_hosts(inventory)
    first = await run_role(inventory, hosts_map)

    assert first.tree() == FIRST_PASS_TREE

    second = await run_role(inventory, hosts_map)
    assert second.tree() == CONVERGED_TREE

    override_inv = make_inventory(hosts_content="10.0.0.1 custom\n")
    override_hosts = make_hosts(override_inv)
    override = await run_role(override_inv, override_hosts)
    assert override.tree() == OVERRIDE_TREE
