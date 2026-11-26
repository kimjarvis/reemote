# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``nginx`` role (design doc section 17.7, T8).

The role is library code now, so it is imported (``from reemote.roles import
nginx``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, nginx(v), host_factory=...)``. The old example's harness
-- import the script by path, replay its four deploys by hand, gather the PPA
outcome and the CPU count between them with ``changed_hosts``/``gather_fact`` --
is gone: the barriers inside the body are those snapshot points and the driver
runs them, so a test only runs the role.

Two assertions carry the migration's claims, exactly as in the
``tests/roles/test_apache.py`` precedent:

* **convergence** -- run the role, then run it again against the same persistent
  hosts, and the second pass reports no change anywhere and queues no handler.
  Because the rerun re-derives the PPA gate and the CPU count through
  ``run_waves``, this proves the barriers are honest rather than that a frozen
  plan was reused.
* **handler gating** -- the ``Restart nginx (handler)`` / ``Reload nginx
  (handler)`` lines exist only on the pass whose ``checkpoint("configure done")``
  found a changed notifying request. The converged pass has no handler record at
  all, where the old example still ran a fourth deploy that happened to hold no
  request.

The structural claim of §18.3 is pinned too: **three ``changed_hosts`` reads over
two checkpoints**. The PPA gate reads the *setup* report; restart and reload both
read the *configure* report. So the barrier sequence is exactly
``[CheckpointRequest, FactRequest, CheckpointRequest]`` -- a third checkpoint is
deliberately absent -- and the run is four waves, three on a converged rerun.

Seeding choices, none of them a bridge:

* the simulated apt install records package names only, so ``nginx.conf``, the
  default-vhost symlink, the legacy ``vhosts.conf``, a retired vhost and the
  ``/etc/nginx`` tree are seeded as the package's post-install state: in a real
  run the configure wave follows the install wave, when a machine has them.
* the default vhost is seeded as a *symlink* (``file_stats`` False plus a
  ``links`` row): reemote's ``File`` fact reads a non-regular file as False,
  which is exactly the shape the role's ``files.file(present=False, force=True)``
  removal must handle.
* the CPU count rides the first-class ``cpus`` seam (default 1, seeded 4 here)
  and the ``nginx -t`` validator passes unless a test seeds the spool into
  ``fail_validate``.
* no between-pass bridge is needed at all. Every store the role touches (apt's
  update stamp and PPA sources, ``remote_files``/``file_stats``, the link stores,
  the systemd unit state) is maintained by the simulation's own mutators, which
  is why the second pass converges instead of churning.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.hosts import CommandError
from reemote.modules.files.files import _install_spool_path
from reemote.roles import NginxVars, nginx
from reemote.roles.nginx import (
    PPA_NAME,
    Upstream,
    VHost,
    configure_plan,
    reload_notifying_names,
    render_nginx_conf,
    render_vhost,
    restart_notifying_names,
    vhost_filename,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from tests.simulated_host import SimulatedHost

NGINX_CONF = "/etc/nginx/nginx.conf"
SITES_ENABLED = "/etc/nginx/sites-enabled"
SITES_AVAILABLE = "/etc/nginx/sites-available"
CONF_D = "/etc/nginx/conf.d"
DEFAULT_VHOST = f"{SITES_ENABLED}/default"
DEFAULT_VHOST_TARGET = f"{SITES_AVAILABLE}/default"
LEGACY_VHOSTS = f"{SITES_ENABLED}/vhosts.conf"
RETIRED_VHOST = f"{SITES_ENABLED}/retired.example.com.conf"
EXAMPLE_VHOST = f"{SITES_ENABLED}/example.com.conf"
#: The deterministic ``/tmp`` spool ``files.install`` stages ``nginx.conf`` at,
#: so a test can seed it into ``fail_validate`` to drive the ``nginx -t`` abort.
CONF_SPOOL = _install_spool_path(NGINX_CONF)

STOCK_CONF = b"# stock nginx.conf shipped by the package\n"

SETUP_NAMES = ("Ensure dirmngr is installed", PPA_NAME)
REINSTALL_NAME = "Ensure nginx will reinstall if the PPA was just added"
INSTALL_NAMES = ("Update apt cache", "Ensure nginx is installed")
#: The configure wave of the default inventory: the default-vhost removal, the
#: vhost directory, one present vhost, one absent vhost, the legacy cleanup, the
#: validated ``nginx.conf``, then the (non-notifying) service task.
CONFIGURE_NAMES = (
    "Remove default nginx vhost config file",
    "Ensure nginx_vhost_path exists",
    "Add managed vhost config file example.com.conf",
    "Remove managed vhost config file retired.example.com.conf",
    "Remove legacy vhosts.conf file",
    "Copy nginx configuration in place",
    "Ensure nginx service is running as configured",
)
RESTART_HANDLER = "Restart nginx (handler)"
RELOAD_HANDLER = "Reload nginx (handler)"


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host to be skipped.

    The role variables ride on the entry's ``roles`` mapping under ``"nginx"``,
    written with the model's bare field names, which is what
    ``resolve_vars(NginxVars, "nginx", inventory)`` binds, so a test
    changes a variable exactly the way an operator does. The base entry exercises
    the non-default branches: the PPA (and its reinstall split), the
    default-vhost removal (the restart handler), a present vhost with the
    redirect/extra-parameters fields, an absent vhost, an upstream and the extra
    conf/http option blocks.
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
        "roles": {
            "nginx": {
                "ppa_use": True,
                "remove_default_vhost": True,
                "extra_conf_options": "worker_rlimit_nofile 8192;",
                "extra_http_options": "proxy_buffering off;",
                "vhosts": [
                        {
                            "server_name": "example.com",
                            "server_name_redirect": "www.example.com",
                            "root": "/var/www/example",
                            "index": "index.php index.html index.htm",
                            "extra_parameters": "location / {\n    try_files $uri $uri/ =404;\n}",
                        },
                        {"server_name": "retired.example.com", "state": "absent"},
                    ],
                "upstreams": [
                        {
                            "name": "myapp",
                            "strategy": "ip_hash",
                            "servers": ["srv1.example.com", "srv2.example.com weight=3"],
                            "keepalive": 16,
                        }
                    ],
            },
        },
    }
    entry["roles"]["nginx"].update(overrides)
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

    See the module docstring: the package's own files are seeded as its
    post-install state, because the simulation installs names, not payloads. The
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
    # The default vhost is a symlink: the File fact reads False for it (the path
    # exists but is not a regular file), the shape the force=True removal eats.
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
    """Materialize the inventory into persistent simulated hosts, seeded target.

    Only the Ubuntu entry is seeded, so the RedHat host keeps its stock empty
    stores and an accidental request on it is caught by the untouched-store
    assertions.
    """
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host)
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
    v = resolve_vars(NginxVars, "nginx", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(inventory, nginx(v), host_factory=factory, dry=dry)
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


def has_entry(report: Any, name: str, host: str = "ubuntu") -> bool:
    """Whether any log entry with *name* exists on *host*."""
    return any(
        entry.name == name and entry.host == host for entry in report.merged().log
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


def conf_text(host: SimulatedHost) -> str:
    """What the role rendered into ``/etc/nginx/nginx.conf``."""
    return host.remote_files[NGINX_CONF].decode("utf-8")


# --- the first pass really deploys the role -----------------------------------


async def test_first_pass_installs_and_configures_nginx():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # dirmngr + nginx installed, the PPA added and the cache refreshed.
    assert "dirmngr" in host.apt_packages
    assert "nginx" in host.apt_packages
    assert host.apt_update_stamp is not None
    assert any(
        source["url"] == "http://ppa.launchpad.net/nginx/stable/ubuntu"
        for source in host.apt_sources
    )
    # The reinstall split materialized (a no-op here -- nginx was not installed
    # yet -- but the request exists because the setup checkpoint named the host).
    assert has_entry(report, REINSTALL_NAME)

    # The rendered nginx.conf landed, with the fact-fed worker_processes (quoted,
    # as the role's folded scalar spells it), the upstream and the option blocks.
    v = resolve_vars(NginxVars, "nginx", inventory)["ubuntu"]
    plan = configure_plan(v, 4)
    conf = conf_text(host)
    assert conf == plan.conf_content
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
    assert host.file_stats[NGINX_CONF]["mode"] == 644

    # The present vhost landed with its redirect block and extra parameters.
    vhost = host.remote_files[EXAMPLE_VHOST].decode()
    assert "server_name  www.example.com;" in vhost
    assert "return       301 $scheme://example.com$request_uri;" in vhost
    assert "listen 80;" in vhost
    assert "listen       [::]:80;" in vhost
    assert "root /var/www/example;" in vhost
    assert "try_files $uri $uri/ =404;" in vhost

    # The default vhost (symlink), the legacy file and the retired vhost are
    # gone; the symlink's target keeps its content (rm drops the link, not the
    # target) and the sites-enabled directory survived.
    assert DEFAULT_VHOST not in host.links
    assert DEFAULT_VHOST not in host.file_stats
    assert DEFAULT_VHOST_TARGET in host.remote_files
    assert LEGACY_VHOSTS not in host.remote_files
    assert RETIRED_VHOST not in host.remote_files
    assert SITES_ENABLED in host.directory_stats

    # The service is running and enabled.
    assert host.services["nginx.service"] == "active"
    assert "nginx.service" in host.enabled_services

    # Both handlers fired, in definition order: restart then reload.
    handler_names = [
        entry.name
        for entry in report.merged().log
        if entry.host == "ubuntu" and entry.name.endswith("(handler)")
    ]
    assert handler_names == [RESTART_HANDLER, RELOAD_HANDLER]
    assert entry_named(report, RESTART_HANDLER).changed
    assert entry_named(report, RELOAD_HANDLER).changed

    # The whole changed sequence, in wave order. Only the vhost directory is a
    # no-op here (it already exists at 0755); the service task changes because it
    # starts and enables a stopped, disabled unit.
    assert [e.name for e in changed_entries(report)] == [
        *SETUP_NAMES,
        "Ensure nginx is installed",
        *[
            name
            for name in CONFIGURE_NAMES
            if name != "Ensure nginx_vhost_path exists"
        ],
        RESTART_HANDLER,
        RELOAD_HANDLER,
    ]


async def test_two_checkpoints_three_changed_hosts_reads():
    """§18.3: three ``changed_hosts`` reads over exactly two checkpoints.

    The PPA gate reads the setup report; restart and reload both read the
    configure report. So the barrier sequence is two checkpoints with the CPU
    fact between them -- never a third checkpoint -- and the run is four waves.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert [type(b).__name__ for b in barriers(report)] == [
        "CheckpointRequest",
        "FactRequest",
        "CheckpointRequest",
    ]
    assert waves(report) == [0, 1, 2, 3]

    ordered = [entry.name for entry in report.merged().log if entry.host == "ubuntu"]
    assert ordered == [
        *SETUP_NAMES,
        REINSTALL_NAME,
        *INSTALL_NAMES,
        *CONFIGURE_NAMES,
        RESTART_HANDLER,
        RELOAD_HANDLER,
    ]
    # The setup checkpoint named the host (the PPA changed); the configure
    # checkpoint named it too (config files changed).
    assert "setup done → 1 host notified" in report.tree()
    assert "configure done → 1 host notified" in report.tree()


async def test_the_cpu_fact_closes_the_install_wave():
    """The ``fact(Cpus)`` barrier makes the count read after the package applied.

    Under the old four-deploy split this was true by construction; as a stream it
    is true only because the fact barrier ends the install wave. The seeded count
    (4) is what the body received back and what the rendered conf carries.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    records = host_records(report, "ubuntu")
    cpu = next(
        record
        for record in records
        if record.barrier is not None and type(record.barrier).__name__ == "FactRequest"
    )
    assert cpu.outcome == 4
    install_waves = {
        record.wave
        for record in records
        if record.planned is not None
        and record.planned.request.name in INSTALL_NAMES
    }
    assert install_waves == {1}
    assert cpu.wave == 1
    configure_waves = {
        record.wave
        for record in records
        if record.planned is not None
        and record.planned.request.name in CONFIGURE_NAMES
    }
    assert configure_waves == {2}


async def test_validate_hook_guards_the_config_install():
    """The role's ``validate: nginx -t -c %s`` is the install's ``validate`` hook."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert request_kwargs(report, "Copy nginx configuration in place")["validate"] == (
        "nginx -t -c {spool}"
    )
    # The validator runs against the spool before the elevated install lands it.
    assert f"sudo nginx -t -c {CONF_SPOOL}" in report.tree()


# --- the family guard ---------------------------------------------------------


async def test_non_ubuntu_host_emits_nothing():
    """The family guard is the body's opening ``if``, not a ``when`` on each op.

    A host outside the ``"Ubuntu"`` family returns before it yields anything, so
    it records no request and no barrier -- where the old example logged each of
    its requests on that host as ``[skipped: when guard ...]``.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert host_records(report, "web1") == []
    assert [e for e in report.merged().log if e.host == "web1"] == []
    assert skipped.apt_packages == {}
    assert skipped.apt_sources == []
    assert skipped.remote_files == {}
    assert skipped.services == {}


# --- the PPA gate -------------------------------------------------------------


async def test_reinstall_removal_only_rides_the_pass_that_added_the_ppa():
    """The gate is the role's ``register`` + ``when: nginx_ppa_added is changed``.

    On the first pass the PPA is added, so the setup checkpoint names the host and
    the install wave carries the conditional removal. On the converged pass the
    PPA already exists, the checkpoint names nobody, and the removal request is
    not emitted at all.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert has_entry(first, REINSTALL_NAME)

    second = await run_role(inventory, hosts)
    assert not has_entry(second, REINSTALL_NAME)
    assert "setup done → 0 hosts notified" in second.tree()


async def test_default_path_skips_the_ppa_and_the_restart_handler():
    """No opt-in toggles: no PPA, no reinstall, no default-vhost removal.

    The reload handler still fires because ``nginx.conf`` changed, but the restart
    handler -- notified only by the default-vhost removal -- does not.
    """
    inventory = make_inventory(
        ppa_use=False,
        remove_default_vhost=False,
        vhosts=[],
        upstreams=[],
        extra_conf_options="",
        extra_http_options="",
    )
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    assert not has_entry(report, PPA_NAME)
    assert not has_entry(report, REINSTALL_NAME)
    assert not has_entry(report, "Remove default nginx vhost config file")
    # The default vhost survived (removal is opt-in).
    assert DEFAULT_VHOST in host.links
    assert "nginx" in host.apt_packages

    handler_names = [
        entry.name
        for entry in report.merged().log
        if entry.host == "ubuntu" and entry.name.endswith("(handler)")
    ]
    assert handler_names == [RELOAD_HANDLER]

    # And the default path converges too.
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert [
        e.name for e in second.merged().log if e.name.endswith("(handler)")
    ] == []


# --- the handler's static when: nginx_service_state == "started" --------------


async def test_stopped_service_suppresses_the_reload_handler():
    """The reload handler's ``when`` is a static per-host filter.

    With ``nginx_service_state="stopped"`` the reload notified set is narrowed to
    nothing before the ``service_handler`` is built. The restart handler (notified
    by the default-vhost removal) still fires and -- exactly like Ansible's
    ``service: state=restarted`` -- starts the stopped unit.
    """
    inventory = make_inventory(service_state="stopped")
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    handler_names = [
        entry.name
        for entry in report.merged().log
        if entry.host == "ubuntu" and entry.name.endswith("(handler)")
    ]
    assert handler_names == [RESTART_HANDLER]
    assert entry_named(report, RESTART_HANDLER).changed
    assert host.services["nginx.service"] == "active"


# --- convergence --------------------------------------------------------------


async def test_second_pass_changes_nothing():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert second.tree() == CONVERGED_TREE
    # The handler wave disappears entirely: no notified host, no request, no wave.
    assert waves(second) == [0, 1, 2]
    assert [
        e.name for e in second.merged().log if e.name.endswith("(handler)")
    ] == []


async def test_dry_preview_stops_at_the_setup_checkpoint():
    """``dry`` is a one-wave preview: it plans wave 0, parks, and applies nothing.

    The setup requests get plan lines, the checkpoint is ``unknown until run``, and
    the body stops there -- no log entry, no command, nothing mutated. A converged
    rerun is the honest preview (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    report = await run_role(inventory, hosts, dry=True)

    assert report.merged().log == []
    assert host.apt_packages == {}
    assert host.apt_sources == []
    assert host.apt_update_stamp is None
    assert host.remote_files[NGINX_CONF] == STOCK_CONF
    assert EXAMPLE_VHOST not in host.remote_files
    assert DEFAULT_VHOST in host.links
    assert host.services == {}
    assert report.tree() == DRY_PREVIEW_TREE


# --- the validate abort -------------------------------------------------------


async def test_failed_validation_preserves_the_running_config():
    """A rejected config never lands: the install aborts before the ``cp``."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    # The simulated nginx -t fails for this spool, so the apply must abort before
    # the InstallCommand, leaving the stock config untouched.
    host.fail_validate.add(CONF_SPOOL)

    with pytest.raises(CommandError):
        await run_role(inventory, hosts)

    assert host.remote_files[NGINX_CONF] == STOCK_CONF
    # The bad config was only staged in the spool, never installed.
    assert CONF_SPOOL in host.remote_files


# --- the pure helpers ---------------------------------------------------------


def test_fact_fed_worker_processes_is_quoted():
    """``None`` reproduces the role's folded-scalar default, quotes included."""
    assert 'worker_processes  "4";' in configure_plan(NginxVars(), 4).conf_content
    assert 'worker_processes  "1";' in configure_plan(NginxVars(), 1).conf_content


def test_worker_processes_override_renders_verbatim():
    plan = configure_plan(NginxVars(worker_processes="auto"), 4)
    assert "worker_processes  auto;" in plan.conf_content
    assert 'worker_processes  "4";' not in plan.conf_content


def test_user_fallback_reproduces_the_set_fact():
    assert "user  www-data;" in render_nginx_conf(NginxVars(), "4")
    assert "user  nginx;" in render_nginx_conf(NginxVars(user="nginx"), "4")


def test_vhost_filename_defaults_to_the_first_server_name():
    assert vhost_filename(VHost(server_name="a.example.com b.example.com")) == (
        "a.example.com.conf"
    )
    assert vhost_filename(VHost(filename="x.conf")) == "x.conf"
    assert vhost_filename(VHost(server_name="a.example.com", filename="x.conf")) == (
        "x.conf"
    )


def test_return_code_renders_as_the_return_directive():
    """``return`` is a Python keyword, so the field aliases it back for Jinja2."""
    content = render_vhost(NginxVars(), VHost(server_name="a.example.com", **{"return": "404"}))
    assert "return 404;" in content


def test_render_vhost_dumps_optional_keys_as_undefined():
    """An unset optional key stays undefined, not rendered as ``None``."""
    content = render_vhost(NginxVars(), VHost(server_name="bare.example.com"))
    assert "server_name bare.example.com;" in content
    assert "root" not in content
    assert "None" not in content
    assert "error_page" not in content


def test_custom_vhost_template_can_extend_the_bundled_one(tmp_path):
    child = tmp_path / "custom.vhost.j2"
    child.write_text(
        '{% extends "vhost.j2" %}\n'
        "{% block server_begin %}# managed by the custom template{% endblock %}\n",
        encoding="utf-8",
    )
    content = render_vhost(
        NginxVars(),
        VHost(server_name="custom.example.com", template=str(child)),
    )
    assert "# managed by the custom template" in content
    assert "server_name custom.example.com;" in content


def test_restart_and_reload_notifying_names():
    plan = configure_plan(
        NginxVars(
            remove_default_vhost=True,
            vhosts=(
                VHost(server_name="example.com"),
                VHost(server_name="retired.example.com", state="absent"),
            ),
        ),
        4,
    )
    assert restart_notifying_names(plan) == ("Remove default nginx vhost config file",)
    assert reload_notifying_names(plan) == (
        "Ensure nginx_vhost_path exists",
        "Add managed vhost config file example.com.conf",
        "Remove managed vhost config file retired.example.com.conf",
        "Remove legacy vhosts.conf file",
        "Copy nginx configuration in place",
    )
    # The final service task carries no notify in the role.
    assert "Ensure nginx service is running as configured" not in (
        reload_notifying_names(plan)
    )
    # No default-vhost removal: nothing notifies restart.
    plain = configure_plan(NginxVars(), 4)
    assert restart_notifying_names(plain) == ()


# --- the variables model ------------------------------------------------------


def test_vhost_requires_server_name_or_filename():
    with pytest.raises(ValidationError, match="server_name or a filename"):
        VHost()
    with pytest.raises(ValidationError, match="server_name or a filename"):
        VHost.model_validate({"root": "/var/www/html"})


def test_upstream_requires_name_and_servers():
    with pytest.raises(ValidationError, match="an upstream needs a name"):
        Upstream(name="", servers=("srv1.example.com",))
    with pytest.raises(ValidationError, match="non-empty servers list"):
        Upstream(name="myapp", servers=())


def test_vars_reject_bad_service_state():
    with pytest.raises(ValidationError, match="service_state"):
        NginxVars(service_state="bogus")


def test_vars_are_frozen_and_reject_unknown_fields():
    v = NginxVars()
    with pytest.raises(ValidationError):
        v.worker_connections = "2048"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        # The prefixed inventory spelling is not a field name.
        NginxVars(nginx_worker_connections="2048")


def test_vhost_sequence_binds_from_lists():
    v = resolve_vars(
        NginxVars,
        "nginx",
        make_inventory(
            vhosts=[{"server_name": "a.example.com"}],
            upstreams=[{"name": "myapp", "servers": ["srv1.example.com"]}],
        ),
    )["ubuntu"]

    assert v.vhosts == (VHost(server_name="a.example.com"),)
    assert v.upstreams == (Upstream(name="myapp", servers=("srv1.example.com",)),)


def test_inventory_binds_the_prefixed_variables():
    v = resolve_vars(
        NginxVars, "nginx", make_inventory(worker_connections="2048")
    )

    assert v["ubuntu"].worker_connections == "2048"
    # The other role's host keeps the defaults: no nginx_ keys, nothing bound.
    assert v["web1"] == NginxVars()


def test_typoed_role_variable_is_reported_not_ignored():
    inventory = make_inventory(worker_conections="2048")
    hosts = make_hosts(inventory)

    with pytest.raises(RoleVarsError, match="worker_conections"):
        resolve_vars(NginxVars, "nginx", inventory)
    assert hosts["ubuntu"].apt_packages == {}


# --- the wave tree (design doc section 7.2) -----------------------------------

# Pinned from a real simulated run (``pytest tests/roles/test_nginx.py -k
# snapshot`` prints the diff against them). The install spool directories are
# content-addressed, so the hashes are stable; the two checkpoint lines and the
# ``Cpus`` fact line are the three barriers the body parks on.
FIRST_PASS_TREE = """(root)
  wave 0
    ubuntu: Ensure dirmngr is installed [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install dirmngr
    ubuntu: Add PPA for Nginx        [changed: ppa ppa:nginx/stable will be added; apt update]
        $ sudo apt-add-repository -y ppa:nginx/stable
        $ sudo apt-get update
    barrier: setup done → 1 host notified
  wave 1
    ubuntu: Ensure nginx will reinstall if the PPA was just added [no change: no packages to remove]
    ubuntu: Update apt cache         [no change: apt update skipped, cache is fresh]
    ubuntu: Ensure nginx is installed [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install nginx
    barrier: Cpus → 4
  wave 2
    ubuntu: Remove default nginx vhost config file [changed: /etc/nginx/sites-enabled/default is not a file, will remove first; file /etc/nginx/sites-enabled/default does not exist]
        $ sudo rm -rf /etc/nginx/sites-enabled/default
    ubuntu: Ensure nginx_vhost_path exists [no change: directory /etc/nginx/sites-enabled already exists]
    ubuntu: Add managed vhost config file example.com.conf [changed: content /etc/nginx/sites-enabled/example.com.conf is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-acd100c770d86a8c/content
        $ sudo sh -c 'mkdir -p /etc/nginx/sites-enabled && cp /tmp/reemote-install-acd100c770d86a8c/content /etc/nginx/sites-enabled/example.com.conf && chown root:root /etc/nginx/sites-enabled/example.com.conf && chmod 644 /etc/nginx/sites-enabled/example.com.conf && rm -rf /tmp/reemote-install-acd100c770d86a8c'
    ubuntu: Remove managed vhost config file retired.example.com.conf [changed: /etc/nginx/sites-enabled/retired.example.com.conf present, will remove]
        $ sudo rm -f /etc/nginx/sites-enabled/retired.example.com.conf
    ubuntu: Remove legacy vhosts.conf file [changed: /etc/nginx/sites-enabled/vhosts.conf present, will remove]
        $ sudo rm -f /etc/nginx/sites-enabled/vhosts.conf
    ubuntu: Copy nginx configuration in place [changed: content /etc/nginx/nginx.conf differs, will install]
        $ scp <files install> -> /tmp/reemote-install-8df6515b1e83040a/content
        $ sudo nginx -t -c /tmp/reemote-install-8df6515b1e83040a/content
        $ sudo sh -c 'mkdir -p /etc/nginx && cp /tmp/reemote-install-8df6515b1e83040a/content /etc/nginx/nginx.conf && chown root:root /etc/nginx/nginx.conf && chmod 644 /etc/nginx/nginx.conf && rm -rf /tmp/reemote-install-8df6515b1e83040a'
    ubuntu: Ensure nginx service is running as configured [changed: nginx.service not running (None), will start; nginx.service not enabled, will enable]
        $ sudo systemctl start nginx.service
        $ sudo systemctl enable nginx.service
    barrier: configure done → 1 host notified
  wave 3
    ubuntu: Restart nginx (handler)  [changed: nginx.service already running; nginx.service will restart]
        $ sudo systemctl restart nginx.service
    ubuntu: Reload nginx (handler)   [changed: nginx.service already running; nginx.service will reload]
        $ sudo systemctl reload nginx.service"""

CONVERGED_TREE = """(root)
  wave 0
    ubuntu: Ensure dirmngr is installed [no change: all packages present]
    ubuntu: Add PPA for Nginx        [no change: ppa ppa:nginx/stable exists]
    barrier: setup done → 0 hosts notified
  wave 1
    ubuntu: Update apt cache         [no change: apt update skipped, cache is fresh]
    ubuntu: Ensure nginx is installed [no change: all packages present]
    barrier: Cpus → 4
  wave 2
    ubuntu: Remove default nginx vhost config file [no change: file /etc/nginx/sites-enabled/default does not exist]
    ubuntu: Ensure nginx_vhost_path exists [no change: directory /etc/nginx/sites-enabled already exists]
    ubuntu: Add managed vhost config file example.com.conf [no change: content already up to date (/etc/nginx/sites-enabled/example.com.conf)]
    ubuntu: Remove managed vhost config file retired.example.com.conf [no change: file /etc/nginx/sites-enabled/retired.example.com.conf does not exist]
    ubuntu: Remove legacy vhosts.conf file [no change: file /etc/nginx/sites-enabled/vhosts.conf does not exist]
    ubuntu: Copy nginx configuration in place [no change: content already up to date (/etc/nginx/nginx.conf)]
    ubuntu: Ensure nginx service is running as configured [no change: nginx.service already running; nginx.service already enabled]
    barrier: configure done → 0 hosts notified"""

DRY_PREVIEW_TREE = """(root)
  wave 0
    ubuntu: Ensure dirmngr is installed [changed: 1 package(s) to install]
    ubuntu: Add PPA for Nginx        [changed: ppa ppa:nginx/stable will be added; apt update]
    barrier: setup done → unknown until run"""


async def test_tree_snapshot():
    """The whole run as one tree: setup, checkpoint, install, CPU fact, configure,
    checkpoint, handler.

    The second tree is what a rerun prints -- one wave fewer, no handler line --
    and it is the preview ``dry`` deliberately refuses to fake (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == FIRST_PASS_TREE

    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE
