# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the reusable change-notification (handler) helpers.

Covers ``reemote.handlers``: ``changed_hosts`` (the notification read -- one
pass over an ``ApplyReport`` freezing the hosts whose *named* entries reported
a change) and the handler factories -- ``service_handler`` (the general form,
selecting the ``systemd.service`` verb via ``action``) plus its ``restart_handler``
and ``reload_handler`` wrappers -- that close over that frozen set and return a
deploy yielding one request for those hosts only. The end-to-end tests drive the
sanctioned two-deploy outcome split against ``SimulatedHost``: a notifying deploy,
the report read between the two runs, and a handler run that acts only on the
changed hosts -- plus the converged re-run that queues nothing at all.
"""

import pytest

from reemote import (
    ApplyReport,
    Host,
    LogEntry,
    OperationRequest,
    Privilege,
    changed_hosts,
    format_log,
    format_plan,
    op,
    reload_handler,
    restart_handler,
    run,
    service_handler,
    systemd,
)
from tests.simulated_host import SimulatedHost


# A ``when`` guard used to prove the factory forwards it verbatim: stable,
# module-level, and reading only static inventory data.
def is_redhat(host: Host) -> bool:
    return host.family == "RedHat"


# --- changed_hosts: the notification read ------------------------------------


def test_changed_hosts_selects_named_changed_entries():
    report = ApplyReport(
        log=[
            LogEntry(host="web1", name="Write config", changed=True, reason=""),
            LogEntry(host="web2", name="Write config", changed=False, reason=""),
            LogEntry(host="web3", name="Write config", changed=True, reason=""),
        ]
    )

    assert changed_hosts(report, {"Write config"}) == frozenset({"web1", "web3"})


def test_changed_hosts_ignores_entries_outside_the_notify_set():
    report = ApplyReport(
        log=[
            LogEntry(host="web1", name="Write config", changed=True, reason=""),
            LogEntry(host="web1", name="Install nginx", changed=True, reason=""),
        ]
    )

    # Only the named request queues the handler; the other change is ignored.
    assert changed_hosts(report, {"Write config"}) == frozenset({"web1"})
    assert changed_hosts(report, {"Restart nginx"}) == frozenset()


def test_changed_hosts_never_queues_a_skipped_entry():
    # A skipped entry (a ``when`` guard failed) carries changed=False, so it
    # must not notify even though its name is in the set.
    report = ApplyReport(
        log=[
            LogEntry(
                host="web1",
                name="Write config",
                changed=False,
                reason="when guard is_redhat",
                skipped=True,
            )
        ]
    )

    assert changed_hosts(report, {"Write config"}) == frozenset()


def test_changed_hosts_accepts_any_iterable_of_names():
    report = ApplyReport(
        log=[LogEntry(host="web1", name="Write config", changed=True, reason="")]
    )

    assert changed_hosts(report, ["Write config"]) == frozenset({"web1"})


# --- restart_handler: the factory --------------------------------------------


def _requests(deploy, host: Host) -> list[OperationRequest]:
    return list(deploy(host))


def test_restart_handler_yields_only_for_notified_hosts():
    deploy = restart_handler({"web2"}, "nginx")

    assert _requests(deploy, Host(name="web1")) == []
    (request,) = _requests(deploy, Host(name="web2"))
    assert request.func is systemd.service
    assert request.kwargs["service"] == "nginx"
    assert request.kwargs["restarted"] is True
    assert request.name == "Restart nginx (handler)"


def test_restart_handler_defaults_to_sudo_on():
    deploy = restart_handler({"web1"}, "nginx")
    (request,) = _requests(deploy, Host(name="web1"))

    assert request.sudo is Privilege.ON


def test_restart_handler_forwards_privilege_and_guard():
    deploy = restart_handler({"web1"}, "nginx", sudo=False, when=is_redhat)
    (request,) = _requests(deploy, Host(name="web1"))

    assert request.sudo is Privilege.OFF
    assert request.when == (is_redhat,)


def test_restart_handler_forwards_sleep():
    # Ansible's ``sleep:`` on the handler's service task (issue #44).
    (request,) = _requests(restart_handler({"web1"}, "nginx", sleep=5), Host(name="web1"))

    assert request.kwargs["restarted"] is True
    assert request.kwargs["sleep"] == 5
    # The int keeps the request hashable, so the dedupe key still works.
    hash(request)


def test_restart_handler_without_sleep_omits_the_keyword():
    # A handler built without ``sleep`` yields exactly the request arguments it
    # yielded before the parameter existed, so existing plans are unchanged.
    (request,) = _requests(restart_handler({"web1"}, "nginx"), Host(name="web1"))

    assert "sleep" not in request.kwargs


def test_restart_handler_forwards_daemon_reload():
    # Issue #50: a unit-file-bearing role's handler needs no hand-built
    # deploy -- the factory forwards the gated daemon-reload flag.
    (request,) = _requests(
        restart_handler({"web1"}, "firewall", daemon_reload=True), Host(name="web1")
    )

    assert request.kwargs["restarted"] is True
    assert request.kwargs["daemon_reload"] is True
    # The bool keeps the request hashable, so the dedupe key still works.
    hash(request)


def test_reload_handler_forwards_daemon_reload():
    (request,) = _requests(
        reload_handler({"web1"}, "nginx", daemon_reload=True), Host(name="web1")
    )

    assert request.kwargs["reloaded"] is True
    assert request.kwargs["daemon_reload"] is True


def test_handlers_without_daemon_reload_omit_the_keyword():
    # A handler built without ``daemon_reload`` yields exactly the request
    # arguments it yielded before the parameter existed, so existing plans
    # (and their hashes) are unchanged.
    for deploy in (
        restart_handler({"web1"}, "nginx"),
        reload_handler({"web1"}, "nginx"),
        service_handler({"web1"}, "nginx"),
    ):
        (request,) = _requests(deploy, Host(name="web1"))
        assert "daemon_reload" not in request.kwargs


def test_restart_handler_yields_one_request_per_service():
    deploy = restart_handler({"web1"}, ("nginx", "php-fpm"))
    requests = _requests(deploy, Host(name="web1"))

    assert [r.kwargs["service"] for r in requests] == ["nginx", "php-fpm"]
    assert [r.name for r in requests] == [
        "Restart nginx (handler)",
        "Restart php-fpm (handler)",
    ]


def test_restart_handler_honours_a_custom_name_template():
    deploy = restart_handler({"web1"}, "nginx", name="Handler: {service}")
    (request,) = _requests(deploy, Host(name="web1"))

    assert request.name == "Handler: nginx"


def test_restart_handler_an_empty_host_set_yields_nothing():
    deploy = restart_handler(frozenset(), "nginx")

    assert _requests(deploy, Host(name="web1")) == []


def test_restart_handler_requests_are_hashable():
    # The dedupe key hashes every argument, so the built request must hash.
    deploy = restart_handler({"web1"}, ("nginx", "php-fpm"))
    for request in _requests(deploy, Host(name="web1")):
        hash(request)


# --- reload_handler: the reload-on-config-change wrapper ----------------------


def test_reload_handler_yields_a_reloaded_request():
    deploy = reload_handler({"web2"}, "nginx")

    assert _requests(deploy, Host(name="web1")) == []
    (request,) = _requests(deploy, Host(name="web2"))
    assert request.func is systemd.service
    assert request.kwargs["service"] == "nginx"
    assert request.kwargs["reloaded"] is True
    assert "restarted" not in request.kwargs
    assert request.name == "Reload nginx (handler)"


def test_reload_handler_forwards_privilege_and_guard():
    deploy = reload_handler({"web1"}, "nginx", sudo=False, when=is_redhat)
    (request,) = _requests(deploy, Host(name="web1"))

    assert request.sudo is Privilege.OFF
    assert request.when == (is_redhat,)


def test_reload_handler_yields_one_request_per_service():
    deploy = reload_handler({"web1"}, ("nginx", "php-fpm"))
    requests = _requests(deploy, Host(name="web1"))

    assert [r.kwargs["service"] for r in requests] == ["nginx", "php-fpm"]
    assert [r.name for r in requests] == [
        "Reload nginx (handler)",
        "Reload php-fpm (handler)",
    ]


# --- service_handler: the general factory ------------------------------------


def test_service_handler_defaults_to_the_restart_action():
    # The default action reproduces restart_handler exactly.
    (default,) = _requests(service_handler({"web1"}, "nginx"), Host(name="web1"))
    (wrapper,) = _requests(restart_handler({"web1"}, "nginx"), Host(name="web1"))

    assert default.kwargs["restarted"] is True
    assert default.name == wrapper.name == "Restart nginx (handler)"


def test_service_handler_running_action_omits_restart_and_reload_flags():
    (request,) = _requests(
        service_handler({"web1"}, "nginx", action="running"), Host(name="web1")
    )

    # ``running`` relies on the operation's default running=True: neither the
    # restart nor the reload flag is set, and the name verb is "Start".
    assert "restarted" not in request.kwargs
    assert "reloaded" not in request.kwargs
    assert request.name == "Start nginx (handler)"


def test_service_handler_honours_a_custom_name_template():
    deploy = service_handler({"web1"}, "nginx", action="reloaded", name="H: {service}")
    (request,) = _requests(deploy, Host(name="web1"))

    assert request.name == "H: nginx"


def test_service_handler_an_empty_host_set_yields_nothing():
    deploy = service_handler(frozenset(), "nginx", action="reloaded")

    assert _requests(deploy, Host(name="web1")) == []


def test_service_handler_rejects_an_unknown_action():
    with pytest.raises(ValueError, match="unknown action"):
        service_handler({"web1"}, "nginx", action="stopped")


# --- end to end: the two-deploy outcome split --------------------------------

INVENTORY = [
    {"host": "web1", "family": "RedHat", "connection": {}, "group": "centos"},
    {"host": "web2", "family": "RedHat", "connection": {}, "group": "centos"},
]


def host_factory(entry: dict) -> Host:
    """A centos host with nginx running; web1 is already enabled, web2 is not.

    ``web1`` therefore reports no change for the notifying ``enabled=True``
    request while ``web2`` does -- which is what makes the handler selective.
    Both hosts run nginx, so a restart on ``web2`` really fires.
    """
    host = SimulatedHost.from_inventory(entry)
    host.services = {"nginx.service": "active"}
    if entry["host"] == "web1":
        host.enabled_services = {"nginx.service"}
    return host


def ensure_nginx_enabled(host: Host):
    """The notifying deploy: converge nginx to enabled, named for the handler."""
    yield op(
        systemd.service,
        name="Ensure nginx enabled",
        service="nginx",
        enabled=True,
        sudo=True,
        when=is_redhat,
    )


async def test_restart_fires_only_on_the_changed_host():
    _plans, report, _hosts = await run(
        INVENTORY, ensure_nginx_enabled, host_factory=host_factory
    )
    assert report is not None

    changed = changed_hosts(report, {"Ensure nginx enabled"})
    assert changed == frozenset({"web2"})

    restart = restart_handler(changed, "nginx", when=is_redhat)
    plans, handler_report, _hosts = await run(
        INVENTORY, restart, host_factory=host_factory
    )

    # web1 is not in the frozen set, so it plans nothing; web2 restarts.
    assert plans["web1"] == []
    assert [p.request.name for p in plans["web2"]] == ["Restart nginx (handler)"]
    assert handler_report is not None
    assert [e.host for e in handler_report.log] == ["web2"]
    assert handler_report.log[0].changed


async def test_reload_fires_only_on_the_changed_host():
    # The same two-deploy split, but the handler reloads instead of restarting:
    # web2 is the only host whose notifying request changed, and nginx is active
    # there, so the reload really fires on web2 and nowhere else.
    _plans, report, _hosts = await run(
        INVENTORY, ensure_nginx_enabled, host_factory=host_factory
    )
    assert report is not None

    changed = changed_hosts(report, {"Ensure nginx enabled"})
    assert changed == frozenset({"web2"})

    reload_ = reload_handler(changed, "nginx", when=is_redhat)
    plans, handler_report, _hosts = await run(
        INVENTORY, reload_, host_factory=host_factory
    )

    assert plans["web1"] == []
    assert [p.request.name for p in plans["web2"]] == ["Reload nginx (handler)"]
    assert plans["web2"][0].request.kwargs["reloaded"] is True
    assert handler_report is not None
    assert [e.host for e in handler_report.log] == ["web2"]
    assert handler_report.log[0].changed


async def test_restart_handler_sleep_splits_the_logged_commands():
    # The handler half of issue #44: ``sleep`` reaches the operation, so the
    # logged restart is three elevated commands instead of one.
    _plans, report, _hosts = await run(
        INVENTORY, ensure_nginx_enabled, host_factory=host_factory
    )
    assert report is not None

    changed = changed_hosts(report, {"Ensure nginx enabled"})
    restart = restart_handler(changed, "nginx", sleep=5, when=is_redhat)
    _plans, handler_report, _hosts = await run(
        INVENTORY, restart, host_factory=host_factory
    )

    assert handler_report is not None
    log = format_log(handler_report)
    assert "$ sudo systemctl stop nginx.service" in log
    assert "$ sudo sleep 5" in log
    assert "$ sudo systemctl start nginx.service" in log
    assert "$ sudo systemctl restart nginx.service" not in log


async def test_daemon_reload_handler_reloads_then_converges():
    # Issue #50 end to end: while the unit is reload-pending (what a
    # ``files.install`` of its unit file leaves behind), the factory-built
    # handler logs ``daemon-reload`` then ``restart``; the reload consumes the
    # pending state, so the next run of the same handler deploy logs no
    # reload at all -- no hand-built deploy involved.
    seeded: dict[str, Host] = {}

    def persistent_factory(entry: dict) -> Host:
        host = seeded.get(entry["host"])
        if host is None:
            host = SimulatedHost.from_inventory(entry)
            host.services = {"firewall.service": "active"}
            host.systemd_reload_pending = {"firewall.service"}
            seeded[entry["host"]] = host
        return host

    restart = restart_handler({"web1", "web2"}, "firewall", daemon_reload=True)

    _plans, report, _hosts = await run(
        INVENTORY, restart, host_factory=persistent_factory
    )
    assert report is not None
    log = format_log(report)
    assert log.count("$ sudo systemctl daemon-reload") == 2
    assert log.count("$ sudo systemctl restart firewall.service") == 2

    _plans, second, _hosts = await run(
        INVENTORY, restart, host_factory=persistent_factory
    )
    assert second is not None
    second_log = format_log(second)
    # The gated reload converged; the stateless restart verb still fires.
    assert "daemon-reload" not in second_log
    assert second_log.count("$ sudo systemctl restart firewall.service") == 2
    for host in seeded.values():
        assert host.systemd_reload_pending == set()


async def test_daemon_reload_handler_reloads_for_a_shadowing_override():
    # Issue #75 at the handler layer: the unit file the deploy wrote is a new
    # override of a packaged unit the manager already loaded, so nothing is
    # modelled as stale at all -- and the factory-built handler still reloads
    # before it restarts, because the gate compares the load roots against
    # what was loaded. The reload lands the override, so the next run plans
    # none of it.
    seeded: dict[str, Host] = {}

    def persistent_factory(entry: dict) -> Host:
        host = seeded.get(entry["host"])
        if host is None:
            host = SimulatedHost.from_inventory(entry)
            host.services = {"varnish.service": "active"}
            for path in (
                "/usr/lib/systemd/system/varnish.service",
                "/etc/systemd/system/varnish.service",
            ):
                host.remote_files[path] = b"[Service]\n"
                host.existing_files.add(path)
            # Only the packaged fragment is loaded; the override is unknown.
            host.systemd_loaded_fragments = {
                "varnish.service": "/usr/lib/systemd/system/varnish.service"
            }
            seeded[entry["host"]] = host
        return host

    restart = restart_handler({"web1", "web2"}, "varnish", daemon_reload=True)

    _plans, report, _hosts = await run(
        INVENTORY, restart, host_factory=persistent_factory
    )
    assert report is not None
    log = format_log(report)
    assert log.count("$ sudo systemctl daemon-reload") == 2
    assert log.count("$ sudo systemctl restart varnish.service") == 2
    for host in seeded.values():
        # No staleness was ever recorded -- the shadow comparison planned this.
        assert host.systemd_reload_pending == set()
        assert host.systemd_loaded_fragments["varnish.service"] == (
            "/etc/systemd/system/varnish.service"
        )

    _plans, second, _hosts = await run(
        INVENTORY, restart, host_factory=persistent_factory
    )
    assert second is not None
    assert "daemon-reload" not in format_log(second)


async def test_handler_dry_run_is_an_honest_preview():
    _plans, report, _hosts = await run(
        INVENTORY, ensure_nginx_enabled, host_factory=host_factory
    )
    assert report is not None
    restart = restart_handler(changed_hosts(report, {"Ensure nginx enabled"}), "nginx")

    plans, dry_report, _hosts = await run(
        INVENTORY, restart, host_factory=host_factory, dry=True
    )

    assert dry_report is None
    assert "web2: Restart nginx (handler)" in format_plan(plans)
    assert plans["web1"] == []


async def test_converged_rerun_queues_no_restart():
    # A factory whose hosts are already enabled: the notifying run reports no
    # change, so the frozen set is empty and the handler deploy yields nothing.
    def converged_factory(entry: dict) -> Host:
        host = SimulatedHost.from_inventory(entry)
        host.services = {"nginx.service": "active"}
        host.enabled_services = {"nginx.service"}
        return host

    _plans, report, _hosts = await run(
        INVENTORY, ensure_nginx_enabled, host_factory=converged_factory
    )
    assert report is not None
    changed = changed_hosts(report, {"Ensure nginx enabled"})
    assert changed == frozenset()

    restart = restart_handler(changed, "nginx")
    plans, handler_report, _hosts = await run(
        INVENTORY, restart, host_factory=converged_factory
    )

    assert plans["web1"] == [] and plans["web2"] == []
    assert handler_report is not None
    assert handler_report.log == []
