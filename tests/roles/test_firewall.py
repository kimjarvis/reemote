# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``firewall`` role.

The role is library code, so it is imported (``from reemote.roles import
firewall``) and replayed against ``SimulatedHost`` through the real wave
driver: ``run_waves(inventory, firewall(v), host_factory=...)``. There is no
harness of its own -- the barriers inside the body are the snapshot points and
the driver runs them, so a test only runs the role (the
``tests/roles/test_apache.py`` precedent).

Two assertions carry the migration's claims:

* **convergence** -- run the role, then run it again against the same
  persistent hosts, and the second pass reports no change anywhere. Because the
  rerun re-derives the ``creates=`` gate, the flush decision and the configure
  report through ``run_waves``, this proves the barriers are honest rather than
  that a frozen plan was reused.
* **handler gating** -- the ``Restart firewall (handler)`` line exists only on
  the pass whose ``checkpoint("configure done")`` found a changed notifying
  request. The converged pass has no handler record at all.

Seeding choices, none of them a bridge:

* the flush rides the first-class action seam (issue #16): ``seed()`` plants
  the canned ``sudo iptables -F`` record in ``host.shell_results`` and the
  body's ``act`` barrier reads it through ``SimulatedHost._run_shell``.
  ``host.ran_shells`` is what the once-only assertions read -- the point of
  making the ``creates=`` gate a barrier is that the flush cannot re-fire once
  the script exists.
* no between-pass bridge is needed at all. ``install_file`` records the script
  and the unit into ``existing_files``/``file_stats`` (so the second pass's
  ``FileExists`` gate reads *True* and the content comparisons converge) and
  marks the unit's daemon reload pending, which the state-gated
  ``daemon_reload=True`` consumes in the same wave and clears (issue #50). The
  systemctl arms maintain ``services``/``enabled_services``.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from reemote import CommandError, run_waves
from reemote.roles import FirewallVars, firewall
from reemote.roles.firewall import (
    FIREWALL_SCRIPT,
    FIREWALL_UNIT,
    NOTIFYING,
    SCRIPT_TEMPLATE,
    UNIT_FILE,
    ForwardedPort,
    render_firewall_script,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from reemote.stages import scoped
from tests.simulated_host import SimulatedHost

FLUSH = "sudo iptables -F"

INSTALL_NAMES = ("Ensure iptables is present",)
#: The configure wave of the default inventory: the rendered script, its mode
#: companion, the (non-notifying) unit file, then the (non-notifying) service
#: task.
CONFIGURE_NAMES = (
    "Copy firewall script into place",
    "Set firewall script mode",
    "Copy firewall systemd unit file into place",
    "Configure the firewall service",
)
#: The one configure request that no-ops even on a fresh host: the paired
#: ``files.file`` runs after the install wrote the file at mode 0744, so the
#: in-wave sequencing leaves it nothing to do.
UNCHANGED_FIRST_PASS = ("Set firewall script mode",)
HANDLER_NAME = "Restart firewall (handler)"


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one Alpine host to be skipped.

    Since the RedHat extension, ``"RedHat"`` is a target family (the RedHat
    section below runs one), so only a third family proves the guard. The role
    variables ride on the entry's ``roles`` mapping under ``"firewall"``,
    written with the model's bare field names, which is what
    ``resolve_vars(FirewallVars, "firewall", inventory)`` binds, so a test
    changes a variable exactly the way an operator does. The base entry keeps
    the role's defaults so the pinned trees below hold; parametrized overrides
    exercise the branches.
    """
    entry: dict[str, Any] = {
        "host": "ubuntu",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.10",
            "username": "supervisor",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "ubuntu",
        "roles": {"firewall": {}},
    }
    entry["roles"]["firewall"].update(overrides)
    return [
        entry,
        {
            "host": "web1",
            "family": "Alpine",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "alpine",
        },
    ]


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock Ubuntu machine with no firewall ever configured.

    Everything empty except the flush record: the role creates the script, the
    unit and the service state itself, and the simulation's mutators carry
    every store from there.
    """
    host.apt_packages = {}
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.links = {}
    host.link_stats = {}
    host.directory_stats = {}
    host.services = {}
    host.enabled_services = set()
    host.shell_results[FLUSH] = {"exit_status": 0, "stdout": "", "stderr": ""}


def make_hosts(
    inventory: list[dict[str, Any]],
    *,
    flush_exit: int = 0,
    flush_stderr: str = "",
) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded target.

    Only the Ubuntu entry is seeded, so the Alpine host keeps its stock empty
    stores and an accidental request on it is caught by the untouched-store
    assertions. The two flush arguments steer the ``act`` barrier the first-run
    path takes -- its exit status is the body's gate (D4).
    """
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host)
            host.shell_results[FLUSH] = {
                "exit_status": flush_exit,
                "stdout": "",
                "stderr": flush_stderr,
            }
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve the role's variables and run one wave pass against persistent hosts.

    The same ``Host`` objects come back for every entry, so state survives from
    one pass to the next -- which is the whole point of the second run.
    """
    v = resolve_vars(FirewallVars, "firewall", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(
        inventory, firewall(v), host_factory=factory, dry=dry
    )
    return report


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def entry_named(report: Any, name: str, host: str = "ubuntu") -> Any:
    """The one log entry with *name* on *host*."""
    return next(
        entry
        for entry in report.merged().log
        if entry.name == name and entry.host == host
    )


def host_records(report: Any, host: str) -> list[Any]:
    """Every record (request step or barrier) attributed to *host*."""
    return [record for record in report.records if record.host == host]


def barriers(report: Any, host: str = "ubuntu") -> list[Any]:
    """The barrier objects *host* parked on, in wave order."""
    return [record.barrier for record in host_records(report, host) if record.barrier]


def waves(report: Any, host: str = "ubuntu") -> list[int]:
    """The distinct wave numbers *host* emitted records in."""
    return sorted({record.wave for record in host_records(report, host)})


def request_kwargs(report: Any, name: str) -> dict:
    """The forwarded keyword arguments of the one planned request called *name*."""
    record = next(
        record
        for record in report.records
        if record.planned is not None and record.planned.request.name == name
    )
    return record.planned.request.kwargs


def script_text(host: SimulatedHost) -> str:
    """What the role rendered into ``/etc/firewall.bash``."""
    return host.remote_files[FIREWALL_SCRIPT].decode("utf-8")


# --- the first pass really deploys the role -----------------------------------


async def test_first_pass_installs_flushes_and_configures():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The package is installed and the first-run flush ran, once.
    assert "iptables" in host.apt_packages
    assert host.ran_shells == [FLUSH]

    # The rendered script, with the role's ownership and mode.
    content = script_text(host)
    assert content.startswith("#!/bin/bash\n")
    assert "iptables -A INPUT -p tcp -m tcp --dport 22 -j ACCEPT" in content
    assert "iptables -A INPUT -j DROP" in content
    assert host.file_stats[FIREWALL_SCRIPT] == {
        "user": "root",
        "group": "root",
        "mode": 744,
        "size": len(content.encode()),
    }

    # The static unit file, and the service it names is running and enabled.
    assert host.remote_files[FIREWALL_UNIT].decode("utf-8") == UNIT_FILE
    assert host.file_stats[FIREWALL_UNIT]["mode"] == 644
    assert host.services["firewall.service"] == "active"
    assert "firewall.service" in host.enabled_services

    # Every notifying task changed, so the handler fired.
    assert entry_named(report, HANDLER_NAME).changed
    assert [e.name for e in changed_entries(report)] == [
        *INSTALL_NAMES,
        *[name for name in CONFIGURE_NAMES if name not in UNCHANGED_FIRST_PASS],
        HANDLER_NAME,
    ]


async def test_one_stream_replaces_the_gated_command():
    """Install, gate, flush, configure, checkpoint and handler share one pass.

    The role needed a ``creates=``-gated command, five tasks and a handler
    flushed at play end. Here they are one stream: a fact barrier for the gate,
    an action barrier for the flush, and the checkpoint that reads the
    configure wave back -- four waves on a first run.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert [type(b).__name__ for b in barriers(report)] == [
        "FactRequest",
        "ActionRequest",
        "CheckpointRequest",
    ]
    assert waves(report) == [0, 1, 2, 3]

    ordered = [entry.name for entry in report.merged().log if entry.host == "ubuntu"]
    assert ordered == [*INSTALL_NAMES, *CONFIGURE_NAMES, HANDLER_NAME]


async def test_the_gate_barrier_closes_the_install_wave():
    """The barrier makes the ``creates=`` read happen after the package applied.

    The fact answer the body received is what decided the flush, and the flush
    sits in its own wave between the gate and the configure requests -- the
    ordering the role got from task order now comes from wave order.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    records = host_records(report, "ubuntu")
    gate = next(record for record in records if record.barrier is not None)
    install_waves = {
        record.wave
        for record in records
        if record.planned is not None
        and record.planned.request.name in INSTALL_NAMES
    }
    assert install_waves == {0}
    assert gate.wave == 0
    assert gate.outcome is False
    # The configure requests are two waves later: gate, then flush, then them.
    configure_waves = {
        record.wave
        for record in records
        if record.planned is not None
        and record.planned.request.name in CONFIGURE_NAMES
    }
    assert configure_waves == {2}


async def test_flush_never_refires_after_the_script_exists():
    """The role's ``creates=`` semantics, proven across passes.

    The flush fired on the first pass because the gate answered ``False``; on
    the second the script exists, so the body skips the ``act`` entirely --
    ``ran_shells`` still holds exactly one command.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)
    host = hosts["ubuntu"]
    assert FIREWALL_SCRIPT in host.existing_files

    second = await run_role(inventory, hosts)

    assert host.ran_shells == [FLUSH]
    assert [type(b).__name__ for b in barriers(second)] == [
        "FactRequest",
        "CheckpointRequest",
    ]
    second_gate = next(r for r in host_records(second, "ubuntu") if r.barrier)
    assert second_gate.outcome is True


async def test_flush_failure_raises_before_configuring():
    """A non-zero barrier exit is data, so the body turns it into the error.

    The role's ``command:`` task would have failed the play; the body raises
    ``CommandError`` naming the host and the command, and no config request is
    built -- the script and the unit are still absent.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory, flush_exit=1, flush_stderr="iptables: Failed to lock the")
    with pytest.raises(CommandError, match="iptables -F"):
        await run_role(inventory, hosts)

    host = hosts["ubuntu"]
    assert host.ran_shells == [FLUSH]
    assert FIREWALL_SCRIPT not in host.remote_files
    assert FIREWALL_UNIT not in host.remote_files
    assert host.services == {}
    # The install wave had already applied when the body gave up.
    assert "iptables" in host.apt_packages


# --- the family guard ---------------------------------------------------------


async def test_alpine_host_emits_nothing():
    """The family guard is the body's opening ``if``, not a ``when`` on each op.

    A host outside the target families (Ubuntu and RedHat -- the RedHat
    section below proves the family *is* a target) returns before it yields
    anything, so it records no request and no barrier at all.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert host_records(report, "web1") == []
    assert [e for e in report.merged().log if e.host == "web1"] == []
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.services == {}
    assert skipped.ran_shells == []


# --- the RedHat family --------------------------------------------------------


def make_redhat_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A one-host RedHat inventory mirroring the Ubuntu entry's branches.

    Upstream tests this family (molecule's default instance is rockylinux9
    and the CI matrix runs it), and only the generic ``package:`` task
    differs: it becomes ``dnf.packages``, every other request is
    family-agnostic.
    """
    entry: dict[str, Any] = {
        "host": "rocky",
        "family": "RedHat",
        "connection": {
            "address": "192.0.2.12",
            "username": "supervisor",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "rhel",
        "roles": {"firewall": {}},
    }
    entry["roles"]["firewall"].update(overrides)
    return [entry]


def make_redhat_hosts(inventory: list[dict[str, Any]]) -> dict[str, SimulatedHost]:
    """Materialize the RedHat entry as a stock RHEL 9 machine, iptables absent.

    The same seeding as the Ubuntu host plus the dnf-side store (``packages``;
    the apt-side ``apt_packages`` stays empty and is asserted so -- the family
    switch must not leak between the two package managers).
    """
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        seed(host)
        host.packages = {}
        hosts[host.name] = host
    return hosts


async def test_redhat_first_pass_installs_the_package_with_dnf():
    """The same stream and request names -- only the package module differs."""
    inventory = make_redhat_inventory()
    hosts = make_redhat_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["rocky"]

    # The package lands in the dnf store; the apt store is never touched.
    assert "iptables" in host.packages
    assert host.apt_packages == {}
    assert host.ran_shells == [FLUSH]
    assert request_kwargs(report, "Ensure iptables is present")["packages"] == (
        "iptables",
    )

    # Script, unit and service state converge exactly as on Ubuntu.
    content = script_text(host)
    assert content.startswith("#!/bin/bash\n")
    assert host.file_stats[FIREWALL_SCRIPT]["mode"] == 744
    assert host.remote_files[FIREWALL_UNIT].decode("utf-8") == UNIT_FILE
    assert host.services["firewall.service"] == "active"
    assert "firewall.service" in host.enabled_services
    assert entry_named(report, HANDLER_NAME, "rocky").changed

    ordered = [entry.name for entry in report.merged().log if entry.host == "rocky"]
    assert ordered == [*INSTALL_NAMES, *CONFIGURE_NAMES, HANDLER_NAME]


async def test_redhat_wave_shape_matches_the_ubuntu_stream():
    """Gate, flush, configure, checkpoint: the same four waves as on Ubuntu."""
    inventory = make_redhat_inventory()
    hosts = make_redhat_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert [type(b).__name__ for b in barriers(report, "rocky")] == [
        "FactRequest",
        "ActionRequest",
        "CheckpointRequest",
    ]
    assert waves(report, "rocky") == [0, 1, 2, 3]


async def test_redhat_second_pass_changes_nothing():
    """Convergence rides the dnf package store, and the flush stays once-only."""
    inventory = make_redhat_inventory()
    hosts = make_redhat_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the RedHat first pass must deploy something too"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert hosts["rocky"].ran_shells == [FLUSH]


# --- the handler --------------------------------------------------------------


async def test_only_a_changed_notifying_request_queues_the_handler():
    """The checkpoint's gate: content equal, mode drifted, one task changes.

    ``files.install`` is content-idempotent, so the script task no-ops and only
    the companion ``files.file`` chmod reports a change -- yet that one name is
    in :data:`NOTIFYING`, so the handler still fires. It is the smallest
    possible proof of the notification gate, and the unit-file and service
    requests changing would *not* have fired it.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    # Converge first, then drift the mode alone.
    await run_role(inventory, hosts)
    host = hosts["ubuntu"]
    host.file_stats[FIREWALL_SCRIPT]["mode"] = 600

    report = await run_role(inventory, hosts)

    assert not entry_named(report, "Copy firewall script into place").changed
    assert entry_named(report, "Set firewall script mode").changed
    assert host.file_stats[FIREWALL_SCRIPT]["mode"] == 744
    assert entry_named(report, HANDLER_NAME).changed
    assert "configure done → 1 host notified" in report.tree()

    third = await run_role(inventory, hosts)
    assert changed_entries(third) == []


async def test_the_unit_file_change_restarts_only_via_daemon_reload():
    """The unit task notifies nothing, but the service request reloads anyway.

    The role's handler fan-in is the script alone; the unit file reaches the
    daemon through the service task's state-gated ``daemon_reload`` (D5), which
    is why no handler line appears for a unit-only change.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)
    host = hosts["ubuntu"]
    # Hand-edit the unit: content drift, and the reload goes pending again.
    host.remote_files[FIREWALL_UNIT] = b"[Unit]\nDescription=Edited\n"

    report = await run_role(inventory, hosts)

    assert entry_named(report, "Copy firewall systemd unit file into place").changed
    assert [e.name for e in report.merged().log if e.name.endswith("(handler)")] == []
    service_reason = entry_named(report, "Configure the firewall service").reason
    assert "daemon-reload" in service_reason
    assert "sudo systemctl daemon-reload" in report.tree()

    third = await run_role(inventory, hosts)
    assert changed_entries(third) == []


async def test_converged_host_gets_no_handler_record():
    """No changed notifying entry means the handler wave holds nothing.

    ``service_handler`` yields no request for a host outside the notified set,
    so the wave is not even appended, and the conditional flush wave is gone
    too: the converged stream is two waves, not four.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    second = await run_role(inventory, hosts)

    assert [type(b).__name__ for b in barriers(second)] == [
        "FactRequest",
        "CheckpointRequest",
    ]
    assert waves(second) == [0, 1]
    assert [e.name for e in second.merged().log if e.name.endswith("(handler)")] == []
    assert "configure done → 0 hosts notified" in second.tree()


# --- convergence --------------------------------------------------------------


async def test_second_pass_changes_nothing():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert second.tree() == CONVERGED_TREE
    # The gate fact is re-read every pass (a barrier is a real read), but the
    # flush it gates ran exactly once across both passes.
    assert hosts["ubuntu"].ran_shells == [FLUSH]


async def test_dry_preview_stops_at_the_gate_barrier():
    """``dry`` is a one-wave preview: it plans, parks, and never runs a barrier.

    The package request gets a plan line, the ``FileExists`` gate is
    ``unknown until run``, and the body stops there -- no log entry, no
    command, nothing applied. A converged rerun is the honest preview (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    report = await run_role(inventory, hosts, dry=True)

    assert report.merged().log == []
    assert host.ran_shells == []
    assert host.apt_packages == {}
    assert host.remote_files == {}
    assert host.services == {}
    assert report.tree() == DRY_PREVIEW_TREE


# --- the variables ------------------------------------------------------------


async def test_state_and_enabled_reach_the_service_task():
    inventory = make_inventory(state="stopped", enabled=False)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    kwargs = request_kwargs(report, "Configure the firewall service")
    assert kwargs["running"] is False
    assert kwargs["enabled"] is False
    assert kwargs["restarted"] is False
    assert kwargs["reloaded"] is False
    assert kwargs["daemon_reload"] is True
    assert kwargs["service"] == "firewall"
    # The stop request ran in the configure wave, and the handler's restart
    # followed it in the next one -- the role's own ordering, in which a
    # notifying change queues ``restart firewall`` after ``firewall_state`` has
    # had its say, so the unit ends up active on a host that changed
    # something. The boot setting is the service task's own, untouched by the
    # handler: still disabled.
    assert hosts["ubuntu"].services["firewall.service"] == "active"
    assert hosts["ubuntu"].enabled_services == set()
    assert entry_named(report, HANDLER_NAME).changed


async def test_restarted_state_always_reports_change():
    """``firewall_state: restarted`` is stateless, faithful to Ansible's service.

    Every pass restarts -- and every pass's changed service entry still queues
    nobody, because the service task does not notify in the role.
    """
    inventory = make_inventory(state="restarted")
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert entry_named(first, "Configure the firewall service").changed

    second = await run_role(inventory, hosts)
    assert entry_named(second, "Configure the firewall service").changed
    assert [e.name for e in changed_entries(second)] == ["Configure the firewall service"]
    assert [e.name for e in second.merged().log if e.name.endswith("(handler)")] == []


async def test_inert_variables_still_bind():
    """``disable_firewalld``/``disable_ufw`` are carried and do nothing (D3).

    Pinned as equality against the default tree: an inventory written for the
    role keeps working, and the two knobs provably change no request.
    """
    inventory = make_inventory(disable_firewalld=True, disable_ufw=True)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert report.tree() == FIRST_PASS_TREE


async def test_custom_template_is_read_controller_side(tmp_path):
    """``firewall.template`` names a file on the controller, not the host (D6)."""
    template = tmp_path / "custom.bash.j2"
    template.write_text(
        "#!/bin/bash\n"
        "{% for port in firewall_allowed_tcp_ports %}\n"
        "iptables -A INPUT -p tcp --dport {{ port }} -j ACCEPT\n"
        "{% endfor %}\n",
        encoding="utf-8",
    )
    inventory = make_inventory(template=str(template), allowed_tcp_ports=["22"])
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    assert script_text(hosts["ubuntu"]) == (
        "#!/bin/bash\niptables -A INPUT -p tcp --dport 22 -j ACCEPT\n"
    )


# --- the script contents ------------------------------------------------------


async def test_additional_rules_are_spliced_verbatim():
    """The role's raw shell lines land in the script untouched (D7)."""
    rule = "iptables -A INPUT -s 192.0.2.0/24 -p tcp --dport 8080 -j ACCEPT"
    ip6_rule = "ip6tables -A INPUT -p tcp --dport 8443 -j ACCEPT"
    inventory = make_inventory(additional_rules=[rule], ip6_additional_rules=[ip6_rule])
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    content = script_text(hosts["ubuntu"])
    assert f"\n{rule}\n" in content
    assert f"  {ip6_rule}\n" in content  # the IPv6 block's two-space indent


async def test_forwarded_ports_render_the_redirect_pair():
    inventory = make_inventory(
        forwarded_tcp_ports=[{"src": 8080, "dest": 80}],
        forwarded_udp_ports=[{"src": "5353", "dest": "5354"}],
    )
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    content = script_text(hosts["ubuntu"])
    assert (
        "iptables -t nat -I PREROUTING -p tcp --dport 8080 -j REDIRECT --to-port 80"
        in content
    )
    assert (
        "iptables -t nat -I OUTPUT -p tcp -o lo --dport 8080 -j REDIRECT --to-port 80"
        in content
    )
    assert (
        "iptables -t nat -I PREROUTING -p udp --dport 5353 -j REDIRECT --to-port 5354"
        in content
    )


async def test_flags_shape_the_script():
    """``flush_rules_and_chains``, ``enable_ipv6`` and ``log_dropped_packets``."""
    full = render_firewall_script(FirewallVars())
    assert "# Remove all rules and chains." in full
    assert "ip6tables -A INPUT -j DROP" in full
    assert "--log-prefix" in full

    lean = render_firewall_script(
        FirewallVars(
            flush_rules_and_chains=False,
            enable_ipv6=False,
            log_dropped_packets=False,
        )
    )
    assert "# Remove all rules and chains." not in lean
    assert "ip6tables" not in lean
    assert "LOG" not in lean.replace("# iptables -A INPUT -j LOG", "")
    # The structural rules the flags do not touch stay in place.
    assert "iptables -A INPUT -j DROP" in lean
    assert "iptables -A INPUT -i lo -j ACCEPT" in lean


# --- the pure half, unit-tested directly --------------------------------------


def test_render_is_a_fixpoint_and_matches_the_bundled_template_shape():
    v = FirewallVars(allowed_tcp_ports=("22", "993"))
    once = render_firewall_script(v)
    assert render_firewall_script(v) == once
    # The template constant is the role's file verbatim: the shebang leads and
    # the IPv6 guard closes.
    assert SCRIPT_TEMPLATE.startswith("#!/bin/bash\n")
    assert SCRIPT_TEMPLATE.rstrip().endswith("{% endif %}")
    # An empty UDP list renders no udp accept lines.
    assert "-p udp -m udp --dport" not in once


def test_unit_file_is_the_role_template_verbatim():
    assert UNIT_FILE.startswith("[Unit]\nDescription=Firewall\n")
    assert "ExecStart=/etc/firewall.bash" in UNIT_FILE
    assert "RemainAfterExit=yes" in UNIT_FILE
    assert "{" not in UNIT_FILE  # the template carries no Jinja to render


def test_notifying_names_match_the_requests():
    # The unit-file and service requests must stay out of the channel set.
    assert NOTIFYING == (
        "Copy firewall script into place",
        "Set firewall script mode",
    )


# --- the variables model ------------------------------------------------------


def test_forwarded_port_binds_ints_and_rejects_blanks():
    port = ForwardedPort.model_validate({"src": 8080, "dest": 80})
    assert port.src == "8080"
    assert port.dest == "80"
    # extra="allow" keeps a key the template does not read.
    named = ForwardedPort.model_validate({"src": "80", "dest": "8080", "comment": "web"})
    assert named.comment == "web"  # type: ignore[attr-defined]
    with pytest.raises(ValidationError, match="src"):
        ForwardedPort.model_validate({"dest": "80"})
    with pytest.raises(ValueError, match="needs both src and dest"):
        ForwardedPort(src="", dest="80")


def test_ports_sequence_binds_from_lists_and_ints():
    v = resolve_vars(
        FirewallVars,
        "firewall",
        make_inventory(allowed_tcp_ports=[22, "8080"], forwarded_tcp_ports=[{"src": 80, "dest": 8080}]),
    )["ubuntu"]

    assert v.allowed_tcp_ports == ("22", "8080")
    assert v.forwarded_tcp_ports == (ForwardedPort(src="80", dest="8080"),)
    assert hash(v)


def test_vars_reject_bad_enum_values():
    with pytest.raises(ValidationError, match="state"):
        FirewallVars(state="enabled")
    with pytest.raises(ValidationError, match="state"):
        FirewallVars(state="rebooted")


def test_vars_are_frozen_and_reject_unknown_fields():
    v = FirewallVars()
    with pytest.raises(ValidationError):
        v.state = "stopped"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        # The prefixed inventory spelling is not a field name.
        FirewallVars(firewall_state="stopped")


def test_inventory_binds_the_prefixed_variables():
    v = resolve_vars(FirewallVars, "firewall", make_inventory(log_dropped_packets=False))

    assert v["ubuntu"].log_dropped_packets is False
    # The other role's host keeps the defaults: no firewall_ keys, nothing bound.
    assert v["web1"] == FirewallVars()


def test_typoed_role_variable_is_reported_not_ignored():
    inventory = make_inventory(alowed_tcp_ports=["22"])
    hosts = make_hosts(inventory)

    with pytest.raises(RoleVarsError, match="alowed_tcp_ports"):
        resolve_vars(FirewallVars, "firewall", inventory)
    assert hosts["ubuntu"].apt_packages == {}


# --- composition --------------------------------------------------------------


async def test_composing_role_captures_no_outputs():
    """``yield from scoped(...)`` returns ``None`` on both paths.

    The role publishes no outputs -- but it still has to *return* on every
    path, the skipped host included, because a composing role reads a return
    value, not a log (design doc section 18.2).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    v = resolve_vars(FirewallVars, "firewall", inventory)
    captured: dict[str, Any] = {}

    def parent(host):
        outputs = yield from scoped("firewall", firewall(v))(host)
        captured[host.name] = outputs

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(inventory, parent, host_factory=factory)

    assert captured == {"ubuntu": None, "web1": None}
    # The scope label reaches the report tree, the log names do not change.
    assert "firewall" in report.tree()
    assert entry_named(report, HANDLER_NAME).changed


# --- the wave tree (design doc section 7.2) -----------------------------------

# Pinned from a real simulated run (``pytest tests/roles/test_firewall.py -k
# snapshot`` prints the diff against them). The install spool directories are
# content-addressed, so the hashes are stable; the two barrier lines carry the
# gate fact's bare answer and the flush barrier's raw ``Result`` dict.
FIRST_PASS_TREE = """(root)
  wave 0
    ubuntu: Ensure iptables is present [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install iptables
    barrier: FileExists → False
  wave 1
    barrier: Flush iptables the first time playbook runs → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 2
    ubuntu: Copy firewall script into place [changed: content /etc/firewall.bash is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-185d7d3425a1c734/content
        $ sudo sh -c 'mkdir -p /etc && cp /tmp/reemote-install-185d7d3425a1c734/content /etc/firewall.bash && chown root:root /etc/firewall.bash && chmod 744 /etc/firewall.bash && rm -rf /tmp/reemote-install-185d7d3425a1c734'
    ubuntu: Set firewall script mode [no change: file /etc/firewall.bash already exists]
    ubuntu: Copy firewall systemd unit file into place [changed: content /etc/systemd/system/firewall.service is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-e7dfe5bc35f4c495/content
        $ sudo sh -c 'mkdir -p /etc/systemd/system && cp /tmp/reemote-install-e7dfe5bc35f4c495/content /etc/systemd/system/firewall.service && chown root:root /etc/systemd/system/firewall.service && chmod 644 /etc/systemd/system/firewall.service && rm -rf /tmp/reemote-install-e7dfe5bc35f4c495'
    ubuntu: Configure the firewall service [changed: daemon-reload; firewall.service not running (None), will start; firewall.service not enabled, will enable]
        $ sudo systemctl daemon-reload
        $ sudo systemctl start firewall.service
        $ sudo systemctl enable firewall.service
    barrier: configure done → 1 host notified
  wave 3
    ubuntu: Restart firewall (handler) [changed: firewall.service already running; firewall.service will restart]
        $ sudo systemctl restart firewall.service"""

CONVERGED_TREE = """(root)
  wave 0
    ubuntu: Ensure iptables is present [no change: all packages present]
    barrier: FileExists → True
  wave 1
    ubuntu: Copy firewall script into place [no change: content already up to date (/etc/firewall.bash)]
    ubuntu: Set firewall script mode [no change: file /etc/firewall.bash already exists]
    ubuntu: Copy firewall systemd unit file into place [no change: content already up to date (/etc/systemd/system/firewall.service)]
    ubuntu: Configure the firewall service [no change: firewall.service already running; firewall.service already enabled]
    barrier: configure done → 0 hosts notified"""

DRY_PREVIEW_TREE = """(root)
  wave 0
    ubuntu: Ensure iptables is present [changed: 1 package(s) to install]
    barrier: FileExists → unknown until run"""


async def test_tree_snapshot():
    """The whole run as one tree: install, gate, flush, configure, checkpoint, handler.

    The second tree is what a rerun prints -- two waves fewer, the flush wave
    and the handler wave both gone -- and it is the preview ``dry``
    deliberately refuses to fake (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == FIRST_PASS_TREE

    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE
