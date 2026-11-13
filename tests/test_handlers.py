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
def is_centos(host: Host) -> bool:
    return host.group == "centos"


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
                reason="when guard is_centos",
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
    deploy = restart_handler({"web1"}, "nginx", sudo=False, when=is_centos)
    (request,) = _requests(deploy, Host(name="web1"))

    assert request.sudo is Privilege.OFF
    assert request.when == (is_centos,)


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
    deploy = reload_handler({"web1"}, "nginx", sudo=False, when=is_centos)
    (request,) = _requests(deploy, Host(name="web1"))

    assert request.sudo is Privilege.OFF
    assert request.when == (is_centos,)


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
    {"host": "web1", "connection": {}, "group": "centos"},
    {"host": "web2", "connection": {}, "group": "centos"},
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
        when=is_centos,
    )


async def test_restart_fires_only_on_the_changed_host():
    _plans, report, _hosts = await run(
        INVENTORY, ensure_nginx_enabled, host_factory=host_factory
    )
    assert report is not None

    changed = changed_hosts(report, {"Ensure nginx enabled"})
    assert changed == frozenset({"web2"})

    restart = restart_handler(changed, "nginx", when=is_centos)
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

    reload_ = reload_handler(changed, "nginx", when=is_centos)
    plans, handler_report, _hosts = await run(
        INVENTORY, reload_, host_factory=host_factory
    )

    assert plans["web1"] == []
    assert [p.request.name for p in plans["web2"]] == ["Reload nginx (handler)"]
    assert plans["web2"][0].request.kwargs["reloaded"] is True
    assert handler_report is not None
    assert [e.host for e in handler_report.log] == ["web2"]
    assert handler_report.log[0].changed


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
