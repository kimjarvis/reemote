# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end test for the nginx Ansible-role migration example.

``examples/roles/ansible-role-nginx/ansible-role-nginx.py`` connects for real
when run as a script, so the suite never executes its ``main()``. What it
*can* do offline is replay the same orchestration against ``SimulatedHost``:
the example is imported as a module, its factories are fed snapshots gathered
from simulated state, and every deploy goes through the real driver (prepare
+ apply, plus a ``dry=True`` preview).

The load-bearing assertion is the migration's idempotence claim: run the
whole setup -> install -> Cpus gather -> configure -> handler sequence, then
run it *again* against the same persistent hosts, and the second pass must
report ``changed=False`` everywhere and queue no handler. No between-pass
bridge is needed: every subsystem the deploys touch (apt's update stamp and
PPA sources, the file/link stores, systemd's service state) is maintained by
the simulation's own mutators.

Seeding choices, not bridges: the simulated apt install does not materialize
a package's own files, so ``nginx.conf``, the default-vhost symlink, the
legacy ``vhosts.conf``, a retired vhost and the ``/etc/nginx`` directory tree
are seeded as the package's post-install state -- the configure deploy runs
after the install apply, when a real machine has them. The default vhost is
seeded as a *symlink* (``file_stats`` False plus a ``links`` row): reemote's
``File`` fact reads a non-regular file as False, which is exactly the shape
the example's ``files.file(present=False, force=True)`` removal must handle.
The Cpus gather reads the ``cpus`` attribute seam; the ``nginx -t`` validator
passes unless a test seeds the spool into ``fail_validate``.
"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from reemote import changed_hosts, gather_fact, hardware, run
from reemote.hosts import CommandError
from reemote.modules.files.files import _install_spool_path
from tests.simulated_host import SimulatedHost

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = (
    REPO_ROOT / "examples" / "roles" / "ansible-role-nginx" / "ansible-role-nginx.py"
)


def load_example() -> Any:
    """Import the migration example by path (its name is not a valid identifier)."""
    spec = importlib.util.spec_from_file_location("ansible_role_nginx_example", EXAMPLE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


nginx = load_example()

NGINX_CONF = "/etc/nginx/nginx.conf"
SITES_ENABLED = "/etc/nginx/sites-enabled"
SITES_AVAILABLE = "/etc/nginx/sites-available"
CONF_D = "/etc/nginx/conf.d"
DEFAULT_VHOST = f"{SITES_ENABLED}/default"
DEFAULT_VHOST_TARGET = f"{SITES_AVAILABLE}/default"
LEGACY_VHOSTS = f"{SITES_ENABLED}/vhosts.conf"
RETIRED_VHOST = f"{SITES_ENABLED}/retired.example.com.conf"
EXAMPLE_VHOST = f"{SITES_ENABLED}/example.com.conf"
CONF_SPOOL = _install_spool_path(NGINX_CONF)

STOCK_CONF = b"# stock nginx.conf shipped by the package\n"


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride as extra keys -- reemote's ``host_vars`` -- so
    ``role_settings`` reads them straight off ``host.extra``. The overrides
    exercise the non-default branches: the PPA (and its reinstall split), the
    default-vhost removal (the restart handler), a present vhost with the
    redirect/extra-parameters fields, an absent vhost, an upstream and the
    extra conf/http option blocks.
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
        "tags": ["ubuntu_host", "apt_package_manager"],
        "nginx_ppa_use": True,
        "nginx_remove_default_vhost": True,
        "nginx_extra_conf_options": "worker_rlimit_nofile 8192;",
        "nginx_extra_http_options": "proxy_buffering off;",
        "nginx_vhosts": [
            {
                "server_name": "example.com",
                "server_name_redirect": "www.example.com",
                "root": "/var/www/example",
                "index": "index.php index.html index.htm",
                "extra_parameters": "location / {\n    try_files $uri $uri/ =404;\n}",
            },
            {"server_name": "retired.example.com", "state": "absent"},
        ],
        "nginx_upstreams": [
            {
                "name": "myapp",
                "strategy": "ip_hash",
                "servers": ["srv1.example.com", "srv2.example.com weight=3"],
                "keepalive": 16,
            }
        ],
    }
    entry.update(overrides)
    return [
        entry,
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock Ubuntu machine with nginx not yet installed.

    The package's own files (``nginx.conf``, the default-vhost symlink, the
    ``/etc/nginx`` tree) are seeded as its post-install state: the
    simulation's apt install records package names only, and the configure
    deploy runs after the install apply, when a real machine has them. The
    legacy ``vhosts.conf`` and the retired vhost are seeded so their removal
    tasks have something to remove.
    """
    host.apt_packages = {}
    host.apt_update_stamp = None
    host.apt_sources = []
    host.cpus = 4
    host.lsb_release = {"Codename": "noble"}
    host.remote_files = {
        NGINX_CONF: STOCK_CONF,
        DEFAULT_VHOST_TARGET: b"server { listen 80 default_server; }\n",
        LEGACY_VHOSTS: b"# legacy monolithic vhosts file\n",
        RETIRED_VHOST: b"server { server_name retired.example.com; }\n",
    }
    host.existing_files = set(host.remote_files)
    host.file_stats = {
        path: {"user": "root", "group": "root", "mode": 644, "size": len(data)}
        for path, data in host.remote_files.items()
    }
    # The default vhost is a symlink: the File fact reads False for it (the
    # path exists but is not a regular file), which is the shape the
    # force=True removal branch consumes.
    host.file_stats[DEFAULT_VHOST] = False
    host.links = {DEFAULT_VHOST: DEFAULT_VHOST_TARGET}
    host.link_stats = {
        DEFAULT_VHOST: {
            "mode": 777,
            "user": "root",
            "group": "root",
            "link_target": DEFAULT_VHOST_TARGET,
        }
    }
    host.directory_stats = {
        path: {"mode": 755, "user": "root", "group": "root"}
        for path in ("/etc/nginx", SITES_ENABLED, SITES_AVAILABLE, CONF_D)
    }
    host.services = {}
    host.enabled_services = set()


def make_hosts(inventory: list[dict[str, Any]]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if nginx.is_ubuntu(host):
            seed(host)
        hosts[host.name] = host
    return hosts


async def migrate(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = True,
) -> dict[str, Any]:
    """Replay the example's whole orchestration once, against persistent hosts.

    The sequence mirrors ``main()`` barrier for barrier -- the PPA outcome
    read and the Cpus gather sit in the same gaps between the same runs --
    but reads the simulated hosts instead of opening SSH connections.
    """
    targets = [host for host in hosts.values() if nginx.is_ubuntu(host)]
    settings = {host.name: nginx.role_settings(host) for host in targets}
    reports: list[Any] = []

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        # The *same* host object every run, so state survives the barriers.
        return hosts[entry["host"]]

    async def execute(deploy: Any) -> Any:
        if dry:
            await run(inventory, deploy, host_factory=factory, dry=True)
        _plans, report, _hosts = await run(inventory, deploy, host_factory=factory)
        assert report is not None
        reports.append(report)
        return report

    # Deploy 1: dirmngr + the optional PPA; then the register+when split.
    setup_report = await execute(nginx.deploy_setup)
    ppa_added = changed_hosts(setup_report, (nginx.PPA_NAME,))

    # Deploy 2: the conditional removal, the cache refresh, the package.
    await execute(nginx.build_install(ppa_added))

    # The Cpus gather, after the install apply (the cpus seam answers it).
    # gather_fact does not close its hosts, so the finally does.
    try:
        gathered = await gather_fact(targets, hardware.Cpus)
    finally:
        for host in targets:
            await host.aclose()
    cpus = {host.name: gathered[host.name] for host in targets}

    # Freeze the configure plans and run deploy 3.
    plans = {
        host.name: nginx.configure_plan(settings[host.name], cpus[host.name])
        for host in targets
    }
    configure_report = await execute(nginx.build_configure(plans))

    # The handlers: changed_hosts over the two notifying name sets, then
    # deploy 4 (restart first, reload second -- definition order).
    restart_names = tuple(
        sorted(
            {name for plan in plans.values() for name in nginx.restart_notifying_names(plan)}
        )
    )
    reload_names = tuple(
        sorted(
            {name for plan in plans.values() for name in nginx.reload_notifying_names(plan)}
        )
    )
    restart_notified = changed_hosts(configure_report, restart_names)
    reload_notified = changed_hosts(configure_report, reload_names)
    handler_report = await execute(
        nginx.build_handlers(plans, restart_notified, reload_notified)
    )
    return {
        "reports": reports,
        "plans": plans,
        "ppa_added": ppa_added,
        "cpus": cpus,
        "restart_notified": restart_notified,
        "reload_notified": reload_notified,
        "handler_report": handler_report,
    }


@pytest.fixture
def rig() -> tuple[list[dict[str, Any]], dict[str, SimulatedHost]]:
    """An inventory plus the persistent simulated hosts it materializes into."""
    inventory = make_inventory()
    return inventory, make_hosts(inventory)


def changed_entries(result: dict[str, Any]) -> list[Any]:
    """Every changed log entry across the whole sequence, handler deploy included."""
    return [entry for report in result["reports"] for entry in report.log if entry.changed]


# --- the first pass really deploys the role ----------------------------------


async def test_first_pass_installs_and_configures_nginx(rig):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]

    # dirmngr + nginx installed, the PPA added and the cache refreshed.
    assert "dirmngr" in host.apt_packages
    assert "nginx" in host.apt_packages
    assert host.apt_update_stamp is not None
    assert result["ppa_added"] == frozenset({"ubuntu"})
    assert any(
        source["url"] == "http://ppa.launchpad.net/nginx/stable/ubuntu"
        for source in host.apt_sources
    )
    # The reinstall split ran: the removal request is in the install report
    # (a no-op here -- nginx was not installed yet -- but materialized).
    install_report = result["reports"][1]
    assert any(
        entry.name == "Ensure nginx will reinstall if the PPA was just added"
        for entry in install_report.log
    )

    # The rendered nginx.conf landed, with the fact-fed worker_processes
    # (quoted, as the role's folded scalar spells it), the upstream and the
    # extra option blocks.
    conf = host.remote_files[NGINX_CONF].decode()
    assert conf == result["plans"]["ubuntu"]["conf_content"]
    assert "user  www-data;" in conf
    assert 'worker_processes  "4";' in conf
    assert "worker_rlimit_nofile 8192;" in conf
    assert "proxy_buffering off;" in conf
    assert "upstream myapp {" in conf
    assert "ip_hash;" in conf
    assert "server srv2.example.com weight=3;" in conf
    assert "keepalive 16;" in conf
    assert "include /etc/nginx/conf.d/*.conf;" in conf
    assert "include /etc/nginx/sites-enabled/*;" in conf

    # The present vhost landed with its redirect block and extra parameters.
    vhost = host.remote_files[EXAMPLE_VHOST].decode()
    assert "server_name  www.example.com;" in vhost
    assert "return       301 $scheme://example.com$request_uri;" in vhost
    assert "listen 80;" in vhost
    assert "listen       [::]:80;" in vhost
    assert "root /var/www/example;" in vhost
    assert "try_files $uri $uri/ =404;" in vhost

    # The default vhost (symlink), the legacy file and the retired vhost are
    # gone -- content bytes included (#18: the simulated rm -f clears
    # remote_files too, so FileContents stops answering for the removed
    # paths) -- and the sites-enabled directory survived. The symlink's
    # target keeps its content: rm -f drops the link, never its target.
    assert DEFAULT_VHOST not in host.links
    assert DEFAULT_VHOST not in host.file_stats
    assert DEFAULT_VHOST_TARGET in host.remote_files
    assert LEGACY_VHOSTS not in host.remote_files
    assert LEGACY_VHOSTS not in host.file_stats
    assert LEGACY_VHOSTS not in host.existing_files
    assert RETIRED_VHOST not in host.remote_files
    assert RETIRED_VHOST not in host.file_stats
    assert RETIRED_VHOST not in host.existing_files
    assert SITES_ENABLED in host.directory_stats

    # The service is running and enabled.
    assert host.services["nginx.service"] == "active"
    assert "nginx.service" in host.enabled_services

    # Both handlers fired, in definition order: restart then reload.
    assert result["restart_notified"] == frozenset({"ubuntu"})
    assert result["reload_notified"] == frozenset({"ubuntu"})
    assert [entry.name for entry in result["handler_report"].log] == [
        "Restart nginx (handler)",
        "Reload nginx (handler)",
    ]
    assert all(entry.changed for entry in result["handler_report"].log)


async def test_second_pass_is_a_noop(rig):
    inventory, hosts = rig
    await migrate(inventory, hosts)
    second = await migrate(inventory, hosts)

    # The migration's idempotence claim: nothing changed anywhere, and no
    # handler was queued -- the PPA is present, the stamp fresh, the files
    # content-equal, the service running/enabled.
    assert changed_entries(second) == []
    assert second["ppa_added"] == frozenset()
    assert second["restart_notified"] == frozenset()
    assert second["reload_notified"] == frozenset()
    assert second["handler_report"].log == []


async def test_non_ubuntu_hosts_are_untouched(rig):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)
    skipped = hosts["web1"]

    # Every request naming web1 was skipped by the is_ubuntu guard; the host
    # was never contacted, so none of its stores moved.
    for report in result["reports"]:
        for entry in report.log:
            if entry.host == "web1":
                assert entry.skipped
    assert skipped.apt_packages == {}
    assert skipped.apt_sources == []
    assert skipped.remote_files == {}
    assert skipped.services == {}


async def test_dry_preview_mutates_nothing(rig):
    inventory, hosts = rig
    host = hosts["ubuntu"]
    targets = [host]
    settings = nginx.role_settings(host)
    plans = {host.name: nginx.configure_plan(settings, 4)}

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    before_files = dict(host.remote_files)
    before_links = dict(host.links)
    await run(inventory, nginx.deploy_setup, host_factory=factory, dry=True)
    await run(inventory, nginx.build_install(frozenset()), host_factory=factory, dry=True)
    await run(inventory, nginx.build_configure(plans), host_factory=factory, dry=True)

    assert host.apt_packages == {}
    assert host.apt_sources == []
    assert host.apt_update_stamp is None
    assert host.remote_files == before_files
    assert host.links == before_links
    assert host.services == {}
    assert targets == [host]


# --- the default path (no opt-in toggles) -------------------------------------


async def test_default_path_skips_the_ppa_and_the_restart_handler():
    inventory = make_inventory(
        nginx_ppa_use=False,
        nginx_remove_default_vhost=False,
        nginx_vhosts=[],
        nginx_upstreams=[],
        nginx_extra_conf_options="",
        nginx_extra_http_options="",
    )
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]

    # No PPA request, no reinstall removal, no restart notification; the
    # reload handler still fires because nginx.conf changed.
    setup_report = result["reports"][0]
    assert all(entry.name != nginx.PPA_NAME for entry in setup_report.log)
    assert result["ppa_added"] == frozenset()
    install_report = result["reports"][1]
    assert all(
        entry.name != "Ensure nginx will reinstall if the PPA was just added"
        for entry in install_report.log
    )
    assert result["restart_notified"] == frozenset()
    assert result["reload_notified"] == frozenset({"ubuntu"})
    assert [entry.name for entry in result["handler_report"].log] == [
        "Reload nginx (handler)"
    ]
    # The default vhost survived (removal is opt-in).
    assert DEFAULT_VHOST in host.links
    assert "nginx" in host.apt_packages

    # And the default path converges too.
    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []
    assert second["handler_report"].log == []


# --- the handler's static when: nginx_service_state == "started" --------------


async def test_stopped_service_suppresses_the_reload_handler():
    inventory = make_inventory(nginx_service_state="stopped")
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]

    # The service was stopped by the final task; the reload notification is
    # filtered out by the handler's static when. The restart handler
    # (notified by the default-vhost removal) still fires and -- exactly like
    # Ansible's `service: state=restarted` -- starts the stopped unit:
    # service_handler keeps running=True, so "restarted" means
    # restart-if-running, start-if-stopped.
    assert result["reload_notified"] == frozenset({"ubuntu"})
    assert [entry.name for entry in result["handler_report"].log] == [
        "Restart nginx (handler)"
    ]
    assert result["handler_report"].log[0].changed
    assert host.services["nginx.service"] == "active"


# --- the validate hook: a rejected config never lands -------------------------


async def test_failed_validation_preserves_the_running_config(rig):
    inventory, hosts = rig
    host = hosts["ubuntu"]
    # The simulated nginx -t fails for this spool: the apply must abort
    # before the InstallCommand, leaving the stock config untouched.
    host.fail_validate.add(CONF_SPOOL)

    with pytest.raises(CommandError):
        await migrate(inventory, hosts)

    assert host.remote_files[NGINX_CONF] == STOCK_CONF
    # The bad config was only staged in the spool, never installed.
    assert CONF_SPOOL in host.remote_files


# --- variable resolution and template inheritance -----------------------------


def test_worker_processes_override_renders_verbatim():
    inventory = make_inventory(nginx_worker_processes="auto")
    hosts = make_hosts(inventory)
    settings = nginx.role_settings(hosts["ubuntu"])
    plan = nginx.configure_plan(settings, 4)
    assert "worker_processes  auto;" in plan["conf_content"]
    assert 'worker_processes  "4";' not in plan["conf_content"]


def test_custom_vhost_template_can_extend_the_bundled_one(tmp_path):
    child = tmp_path / "custom.vhost.j2"
    child.write_text(
        '{% extends "vhost.j2" %}\n'
        "{% block server_begin %}# managed by the custom template{% endblock %}\n",
        encoding="utf-8",
    )
    inventory = make_inventory(
        nginx_vhosts=[{"server_name": "custom.example.com", "template": str(child)}]
    )
    hosts = make_hosts(inventory)
    settings = nginx.role_settings(hosts["ubuntu"])
    content = nginx.render_vhost(settings, settings["vhosts"][0])
    assert "# managed by the custom template" in content
    assert "server_name custom.example.com;" in content


def test_vhost_filename_defaults_to_the_first_server_name():
    assert (
        nginx.vhost_filename({"server_name": "a.example.com b.example.com"})
        == "a.example.com.conf"
    )
    assert nginx.vhost_filename({"filename": "x.conf"}) == "x.conf"
    with pytest.raises(ValueError, match="server_name or a filename"):
        nginx.vhost_filename({})


def test_role_settings_gates_fail_before_any_host_is_contacted():
    def settings_with(**extra: Any) -> Any:
        entry = make_inventory()[0]
        entry.update(extra)
        return nginx.role_settings(SimulatedHost.from_inventory(entry))

    with pytest.raises(ValueError, match="nginx_service_state"):
        settings_with(nginx_service_state="bogus")
    with pytest.raises(TypeError, match="nginx_vhosts"):
        settings_with(nginx_vhosts="not-a-list")
    with pytest.raises(ValueError, match="server_name or filename"):
        settings_with(nginx_vhosts=[{}])
    with pytest.raises(ValueError, match="servers"):
        settings_with(nginx_upstreams=[{"name": "myapp"}])
    # The nginx_user fallback reproduces the role's set_fact.
    assert settings_with()["user"] == "www-data"
    assert settings_with(nginx_user="nginx")["user"] == "nginx"