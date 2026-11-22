# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Migration of the PHP Xdebug Ansible role (``geerlingguy.php-xdebug``) to reemote.

The role at ``https://github.com/geerlingguy/ansible-role-php-xdebug`` builds
Xdebug from source and loads it into PHP on RHEL/CentOS and Debian/Ubuntu. This
program is its **Ubuntu default (build-from-source) path** expressed as reemote
deploys: the build toolchain, the source tarball extraction, the
``phpize && ./configure && make`` build, the versioned module install, the
rendered ``xdebug.ini`` in every extension conf path, the optional CLI disable,
and the two chained handlers inherited from the ``geerlingguy.php`` dependency
(``restart webserver`` -> ``restart php-fpm``). Every role variable is read from
the inventory entry's extra keys (reemote's ``host_vars`` mechanism), so
``php_xdebug_version`` and friends live beside ``host``/``connection`` in
``inventory.json``.

Target: hosts in the ``"Ubuntu"`` OS family. Every request carries
``when=is_ubuntu``, and a skipped request is never materialized -- the other
hosts in the shared inventory are not even contacted. The role's RHEL/CentOS
branch (``yum: '@development'``) is out of scope (a Phase 0 single-OS decision),
so the ``include_vars`` OS dispatch collapses to the frozen ``vars/Debian.yml``
constants below, and the PHP version comes from the sibling ``geerlingguy.php``
migration's Ubuntu version table.

Both implementation gaps this migration originally found are now CLOSED on main
-----------------------------------------------------------------------------

* **issue #21** (``files: no unarchive operation``) -- closed by commit
  ``efd354f``. The role's ``unarchive: src=<url> creates=<src_dir> copy=false``
  task is now ONE in-deploy ``files.unarchive`` request: the URL is fetched
  control-side as binary bytes, spooled to ``/tmp``, extracted and cleaned up,
  with Ansible's ``creates:`` idempotence resolved in-operation through the
  ``FileExists`` fact. The former three-piece workaround (a ``urllib`` fetch, a
  ``files.put`` spool deploy and an out-of-deploy ``tar`` action) is gone.
* **issue #22** (``files: copy/move lack Ansible remote-copy parity``) -- closed
  by commit ``dbfccf0``. The role's ``copy: remote_src=true mode=0644`` task
  (which *renames* ``xdebug.so`` -> ``xdebug-<version>.so``) is now ONE
  ``files.copy`` request with a file ``dest`` (the rename form), ``mode``/
  ``user``/``group`` converged in the same request, and a sha1-gated
  ``overwrite=True`` (Ansible's ``force: yes`` idempotence). The former
  versioned-basename staging ``cp`` and the separate metadata-convergence deploy
  are gone.

Why the program is a *sequence* of deploys rather than one
-------------------------------------------------------

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role
reads the distribution major version (``include_vars`` dispatch) and gates the
build on a ``creates:`` path -- mutable reads a single deploy may not branch on.
Each becomes a **gather -> snapshot -> factory** step, and the build itself is an
``Shell`` action *between* deploys (the build-from-source recipe, issue #8
closed: commands never live inside a deploy). The barriers below are
load-bearing:

1. ``gather_fact(LinuxDistribution)`` -- the major version selects the PHP
   version (7.4 / 8.1 / 8.3, else the Debian 8.2 fallback), which fixes the
   extension conf paths and the handler daemon names.
2. ``build_setup``   -- build-essential, the module directory, and the source
   tarball extraction (``files.unarchive``, ``creates`` gated in-operation).
3. ``gather_fact(FileExists)`` -- the build's ``creates:`` path
   (``<src_dir>/modules/xdebug.so``), read AFTER the extraction. The frozen
   ``need_build`` set gates the action.
4. the build ACTION -- ``phpize && ./configure && make`` on the ``need_build``
   hosts only (``gather_action(Shell)``); a non-zero exit raises ``CommandError``.
5. ``build_install`` -- the versioned module copy (``files.copy`` rename +
   metadata + sha1-gated overwrite), the rendered ``xdebug.ini`` installs and the
   optional CLI disable.
6. ``build_handler`` -- ``changed_hosts`` over the notifying names,
   ``restart webserver`` then the chained ``restart php-fpm``.

The module copy cannot share a deploy with the build that creates its source:
``files.copy`` validates ``src`` in *both* phases, so the build action is a
barrier before ``build_install``. The module directory is created in
``build_setup`` (an earlier barrier) because the copy's rename form validates the
target's *parent* directory in both phases too.

Deliberate deviations from Ansible semantics
--------------------------------------------

Recorded at the point of use and in ``ansible-role-php-xdebug.md``:

* ``unarchive: mode=0755`` is dropped -- ``files.unarchive`` has no ``mode``
  parameter. The extracted *source tree's* modes are not converged to 0755;
  this is inert (the build does not depend on them, and the installed module
  gets 0644 through the copy).
* the build ``command:`` loop's ``creates:`` gate is reconstructed control-side
  from a ``FileExists`` snapshot (``need_build``); a ``Shell`` action has no
  in-operation ``creates``. The three loop items (``phpize``, ``./configure``,
  ``make``) collapse to one ``&&`` chain -- Ansible runs them in order and stops
  on the first failure, exactly what the chain does.
* the ``xdebug.ini.j2`` template uses Xdebug-2 directive names
  (``xdebug.remote_enable`` etc.) even though the default version is 3.3.2
  (Xdebug 3). This is the role's own quirk; the render reproduces the template
  verbatim rather than "fixing" the directive names.
* ``vars/Debian.yml``'s ``__php_xdebug_module_path`` is the legacy
  ``/usr/lib/php7/modules``. It is preserved verbatim (overridable via
  ``php_xdebug_module_path``); on a modern PHP 8.3 host the real extension dir
  differs, so override it in the inventory for a loadable module.
* the handler chain (``restart webserver`` notifies ``restart php-fpm``)
  collapses to an upstream-first ``compose`` of ``service_handler`` deploys (the
  documented handler-chaining recipe, issue #10 closed).
* the ``apt: build-essential`` task has no ``update_cache`` -- neither does this
  migration (no cache refresh is implied).

No template engine is needed: ``xdebug.ini.j2`` is pure variable substitution,
so the render below is a plain Python f-string. reemote is the only control-side
dependency. The control machine needs internet egress to ``xdebug.org`` (the
tarball is fetched control-side); the host needs ``tar``, ``phpize`` (from the
``geerlingguy.php`` dependency's ``php<version>-dev`` package) and a C toolchain.

Run it with::

    uv run python examples/roles/ansible-role-php-xdebug/ansible-role-php-xdebug.py
"""

import asyncio
import json
import shlex
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    FileExists,
    Host,
    HostConnectionError,
    InventoryError,
    LinuxDistribution,
    OperationRequest,
    PrivilegeError,
    Shell,
    apt,
    changed_hosts,
    compose,
    files,
    format_log,
    format_plan,
    gather_action,
    gather_fact,
    op,
    run,
    service_handler,
    validate_inventory,
)

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path only) ---

#: The sibling ``geerlingguy.php`` migration's ``vars/Ubuntu-{20,22,24}.yml``
#: table -- each sets ``__php_default_version_debian``; any other major falls
#: back to ``vars/Debian.yml``'s 8.2. Keyed on ``LinuxDistribution.major``. The
#: Xdebug role inherits ``php_version`` and ``php_extension_conf_paths`` from
#: that dependency, so the same table resolves them here.
UBUNTU_PHP_VERSIONS = {20: "7.4", 22: "8.1", 24: "8.3"}
DEBIAN_DEFAULT_PHP_VERSION = "8.2"

#: ``php_fpm_handler_state`` -> the ``service_handler`` action (the dependency's
#: handlers/main.yml ``state: "{{ php_fpm_handler_state }}"``). ``started`` maps
#: to ``running`` (service_handler offers only restarted/reloaded/running).
FPM_HANDLER_ACTIONS = {
    "restarted": "restarted",
    "reloaded": "reloaded",
    "started": "running",
}

#: The notifying request names -- the role's task names, verbatim (trailing
#: periods included). "Move Xdebug module into place." (tasks/main.yml) queues
#: ``restart webserver``; the two configure.yml tasks queue both handlers. They
#: are module constants so ``changed_hosts`` can select them across the report.
WEBSERVER_NOTIFY_NAMES = frozenset(
    {
        "Move Xdebug module into place.",
        "Copy xdebug ini into main extension config folder.",
        "Disable xdebug for PHP CLI.",
    }
)
FPM_NOTIFY_NAMES = frozenset(
    {
        "Copy xdebug ini into main extension config folder.",
        "Disable xdebug for PHP CLI.",
    }
)

#: The role defaults this migration honours (``defaults/main.yml``), plus the
#: ``geerlingguy.php`` dependency variables the handlers and conf paths need.
#: The ``php_xdebug_`` prefix is kept so an inventory entry reads like the role's
#: ``host_vars``. Version-dependent values default to None -- the role's
#: ``set_fact`` falls back to the ``vars/Debian.yml`` ``__php_xdebug_*`` value
#: when undefined; ``resolved_vars`` applies that fallback. The render values
#: (coverage_enable, remote_enable, ...) stay raw: Ansible interpolates them
#: verbatim into the ini, so boolifying would render ``True`` where the role's
#: string ``"false"`` renders ``false``.
ROLE_DEFAULTS: dict[str, Any] = {
    "workspace": "/root",
    "php_xdebug_version": "3.3.2",
    "php_xdebug_coverage_enable": 1,
    "php_xdebug_default_enable": 1,
    "php_xdebug_remote_enable": "false",
    "php_xdebug_remote_connect_back": "false",
    "php_xdebug_remote_host": "localhost",
    "php_xdebug_remote_port": "9000",
    "php_xdebug_remote_log": "/tmp/xdebug.log",
    "php_xdebug_remote_autostart": "false",
    "php_xdebug_idekey": "sublime.xdebug",
    "php_xdebug_max_nesting_level": 256,
    "php_xdebug_cli_disable": False,
    "php_xdebug_module_path": None,
    "php_xdebug_config_filename": None,
    # geerlingguy.php dependency variables (defaults/main.yml + vars/Debian.yml).
    "php_default_version_debian": None,
    "php_webserver_daemon": None,
    "php_fpm_daemon": None,
    "php_extension_conf_paths": None,
    "php_enable_webserver": True,
    "php_enable_php_fpm": False,
    "php_fpm_state": "started",
    "php_fpm_handler_state": "restarted",
}


# --- pure helpers ------------------------------------------------------------



def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data and stays a stable module-level function,
    so both deploy phases reach the same eligibility decision. A skipped request
    is never materialized, so the Alpine and CentOS hosts in the shared
    inventory are not contacted at all.
    """
    return host.family == "Ubuntu"


def _enum(value: Any, choices: tuple[str, ...], key: str) -> str:
    if value not in choices:
        raise ValueError(f"{key} must be one of {choices}, got {value!r}")
    return value


def _ansible_bool(value: Any, key: str) -> bool:
    """Ansible's ``| bool`` filter: YAML 1.1 booleans, case-insensitive."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)
        raise ValueError(f"{key} must be a boolean, got {value!r}")
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"1", "true", "yes", "on"}:
            return True
        if low in {"0", "false", "no", "off"}:
            return False
    raise ValueError(
        f"{key} must be a boolean (yes/no/true/false/on/off/1/0), got {value!r}"
    )


def _string_tuple(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{key} must be a list of strings, got {type(value).__name__}")
    if not all(isinstance(item, str) for item in value):
        raise TypeError(f"{key} must contain only strings")
    return tuple(value)


def _optional_string_tuple(value: Any, key: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    return _string_tuple(value, key)


def _optional_str(value: Any, key: str) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise TypeError(f"{key} must be a string or null, got {type(value).__name__}")


def _version_tuple(version: str, key: str) -> tuple[int, ...]:
    parts = version.split(".")
    if not parts or not all(part.isdigit() for part in parts):
        raise ValueError(f"{key} must be dotted digits, got {version!r}")
    return tuple(int(part) for part in parts)


def role_settings(host: Host) -> dict[str, Any]:
    """Resolve a host's role variables: defaults overlaid with ``host.extra``.

    Validates enums, booleans and list shapes before any host is contacted, so a
    bad value fails fast with a clear message rather than mid-deploy. The
    version-dependent ``None`` values are left for ``resolved_vars`` (they need
    the gathered distribution major).
    """
    settings = {**ROLE_DEFAULTS, **host.extra}
    for key in ("workspace", "php_xdebug_version"):
        if not isinstance(settings[key], str) or not settings[key]:
            raise ValueError(f"{key} must be a non-empty string, got {settings[key]!r}")
    _version_tuple(settings["php_xdebug_version"], "php_xdebug_version")
    settings["php_xdebug_cli_disable"] = _ansible_bool(
        settings["php_xdebug_cli_disable"], "php_xdebug_cli_disable"
    )
    settings["php_enable_webserver"] = _ansible_bool(
        settings["php_enable_webserver"], "php_enable_webserver"
    )
    settings["php_enable_php_fpm"] = _ansible_bool(
        settings["php_enable_php_fpm"], "php_enable_php_fpm"
    )
    settings["php_fpm_state"] = _enum(
        settings["php_fpm_state"],
        ("started", "stopped", "restarted", "reloaded"),
        "php_fpm_state",
    )
    settings["php_fpm_handler_state"] = _enum(
        settings["php_fpm_handler_state"],
        ("restarted", "reloaded", "started"),
        "php_fpm_handler_state",
    )
    settings["php_extension_conf_paths"] = _optional_string_tuple(
        settings["php_extension_conf_paths"], "php_extension_conf_paths"
    )
    for key in (
        "php_default_version_debian",
        "php_webserver_daemon",
        "php_fpm_daemon",
        "php_xdebug_module_path",
        "php_xdebug_config_filename",
    ):
        settings[key] = _optional_str(settings[key], key)
    if settings["php_default_version_debian"] is not None:
        _version_tuple(
            settings["php_default_version_debian"], "php_default_version_debian"
        )
    return settings


def render_xdebug_ini(s: dict[str, Any], module_path: str, xversion: str) -> str:
    """``templates/xdebug.ini.j2`` -- pure variable substitution, verbatim.

    The template has no block tags or filters, so Ansible's ``trim_blocks`` /
    ``keep_trailing_newline`` defaults leave it a straight interpolation; the
    f-string reproduces it byte-for-byte (including the trailing newline). The
    Xdebug-2 directive names are the role's own (see the module docstring).
    """
    return f"""[XDebug]
zend_extension="{module_path}/xdebug-{xversion}.so"

xdebug.coverage_enable={s["php_xdebug_coverage_enable"]}
xdebug.default_enable={s["php_xdebug_default_enable"]}

xdebug.remote_enable={s["php_xdebug_remote_enable"]}
xdebug.remote_connect_back={s["php_xdebug_remote_connect_back"]}
xdebug.remote_host={s["php_xdebug_remote_host"]}
xdebug.remote_port={s["php_xdebug_remote_port"]}
xdebug.remote_log={s["php_xdebug_remote_log"]}
xdebug.remote_autostart={s["php_xdebug_remote_autostart"]}

xdebug.idekey="{s["php_xdebug_idekey"]}"

xdebug.max_nesting_level={s["php_xdebug_max_nesting_level"]}
"""


def resolved_vars(
    settings: dict[str, Any], distribution: dict[str, Any]
) -> dict[str, Any]:
    """The ``vars/Debian.yml`` ``__php_xdebug_*`` values and derived paths.

    The PHP version comes from the user's ``php_default_version_debian`` if set,
    else the Ubuntu major-version table, else the Debian 8.2 fallback -- the
    dependency's ``include_vars`` + ``set_fact`` chain. The module path and conf
    filename fall back to the role's ``vars/Debian.yml`` constants when the user
    leaves them undefined (the role's two ``set_fact`` tasks). Every path the
    deploys and the build action need is frozen here, control-side, so the
    factories close over hashable scalars and tuples only.
    """
    version = settings["php_default_version_debian"] or UBUNTU_PHP_VERSIONS.get(
        distribution.get("major"), DEBIAN_DEFAULT_PHP_VERSION
    )
    _version_tuple(version, "php_default_version_debian")
    xversion = settings["php_xdebug_version"]
    workspace = settings["workspace"]
    module_path = settings["php_xdebug_module_path"] or "/usr/lib/php7/modules"
    config_filename = settings["php_xdebug_config_filename"] or "20-xdebug.ini"
    default_conf = (
        f"/etc/php/{version}/fpm",
        f"/etc/php/{version}/apache2",
        f"/etc/php/{version}/cli",
    )
    ext_paths = settings["php_extension_conf_paths"] or tuple(
        f"{path}/conf.d" for path in default_conf
    )
    src_dir = f"{workspace}/xdebug-{xversion}"
    cli_disable = settings["php_xdebug_cli_disable"]
    # configure.yml's template ``when``: an ext path is written unless it is the
    # CLI path and cli_disable is set. The CLI removal targets the default CLI
    # conf path (the role hardcodes it from php_version, not from ext_paths).
    ini_dests = tuple(
        f"{ext}/{config_filename}"
        for ext in ext_paths
        if "cli" not in ext or not cli_disable
    )
    return {
        "version": version,
        "xversion": xversion,
        "workspace": workspace,
        "module_path": module_path,
        "config_filename": config_filename,
        "ext_paths": tuple(ext_paths),
        "src_dir": src_dir,
        "built_so": f"{src_dir}/modules/xdebug.so",
        "installed_so": f"{module_path}/xdebug-{xversion}.so",
        "tarball_url": f"https://xdebug.org/files/xdebug-{xversion}.tgz",
        "cli_disable": cli_disable,
        "ini_dests": ini_dests,
        "cli_ini": f"/etc/php/{version}/cli/conf.d/{config_filename}",
        "ini_content": render_xdebug_ini(settings, module_path, xversion),
        "webserver_daemon": settings["php_webserver_daemon"] or "apache2",
        "fpm_daemon": settings["php_fpm_daemon"] or f"php{version}-fpm",
        "fpm_handler_action": FPM_HANDLER_ACTIONS[settings["php_fpm_handler_state"]],
    }


def build_command(rv: dict[str, Any]) -> str:
    """The build action's shell: ``cd <src_dir> && phpize && ./configure && make``.

    The role's ``command:`` loop runs the three items in order with
    ``chdir=src_dir`` and stops on the first failure -- an ``&&`` chain does
    exactly that. Passwordless ``sudo`` renders ``"sudo " + command`` verbatim
    with no ``sh -c`` wrapper, so the chain is wrapped explicitly: the rendered
    (and simulation-seeded) form is ``sudo sh -c 'cd ... && phpize && ...'``.
    """
    inner = f"cd {shlex.quote(rv['src_dir'])} && phpize && ./configure && make"
    return f"sh -c {shlex.quote(inner)}"


# --- deploy factories (close over the frozen per-host resolved vars) ----------


def build_setup(
    resolved: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 1: the toolchain, the module directory and the source extraction.

    ``files.unarchive`` (issue #21, closed) is the role's ``unarchive:`` task as
    one request: the URL is fetched control-side as binary, spooled, extracted
    and cleaned up, with ``creates=src_dir`` resolving Ansible's ``creates:``
    gate in-operation (a converged host is a no-op and never fetches). The
    ``mode=0755`` the role sets on the extraction is dropped (no ``mode``
    parameter; inert -- see the module docstring). The module directory is
    created here, an earlier barrier than the copy that validates its parent.
    """

    def setup(host: Host) -> Generator[OperationRequest]:
        rv = resolved.get(host.name)
        if rv is None:
            return
        yield op(
            apt.packages,
            name="Ensure dependencies for building from source are installed (Debian).",
            packages=("build-essential",),
            present=True,
            sudo=True,
            when=is_ubuntu,
        )
        yield op(
            files.directory,
            name="Ensure Xdebug module path exists.",
            path=rv["module_path"],
            mode="0755",
            user="root",
            group="root",
            sudo=True,
            when=is_ubuntu,
        )
        yield op(
            files.unarchive,
            name="Untar Xdebug.",
            src=rv["tarball_url"],
            dest=rv["workspace"],
            creates=rv["src_dir"],
            sudo=True,
            when=is_ubuntu,
        )

    return setup


def build_install(
    resolved: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 2: install the built module and configure PHP to load it.

    ``files.copy`` (issue #22, closed) is the role's ``copy: remote_src=true``
    task as one request: a file ``dest`` renames ``xdebug.so`` to
    ``xdebug-<version>.so``, ``mode``/``user``/``group`` converge in the same
    request, and ``overwrite=True`` is sha1-gated (Ansible's ``force: yes``: a
    drifted target converges once, a repeat is a no-op). The source must already
    exist -- the build action is the barrier before this deploy. The ini installs
    and the optional CLI disable are configure.yml's two tasks.
    """

    def install(host: Host) -> Generator[OperationRequest]:
        rv = resolved.get(host.name)
        if rv is None:
            return
        yield op(
            files.copy,
            name="Move Xdebug module into place.",
            src=rv["built_so"],
            dest=rv["installed_so"],
            overwrite=True,
            mode="0644",
            user="root",
            group="root",
            sudo=True,
            when=is_ubuntu,
        )
        for dest in rv["ini_dests"]:
            yield op(
                files.install,
                name="Copy xdebug ini into main extension config folder.",
                content=rv["ini_content"],
                dest=dest,
                mode="0644",
                user="root",
                group="root",
                sudo=True,
                when=is_ubuntu,
            )
        if rv["cli_disable"]:
            # The CLI conf.d entry is a symlink on a stock host, so force=True
            # (the File fact reads a symlink as a non-file).
            yield op(
                files.file,
                name="Disable xdebug for PHP CLI.",
                path=rv["cli_ini"],
                present=False,
                force=True,
                sudo=True,
                when=is_ubuntu,
            )

    return install


def build_handler(
    resolved: dict[str, Any],
    webserver_hosts: frozenset[str],
    fpm_hosts: frozenset[str],
) -> Callable[[Host], Generator[OperationRequest]]:
    """``handlers/main.yml``: ``restart webserver`` then chained ``restart php-fpm``.

    The chain collapses to an upstream-first service listing (the documented
    handler-chaining recipe, issue #10 closed): every notified host restarts its
    webserver daemon first, then its FPM daemon. The caller has already applied
    the handlers' ``when`` guards -- ``php_enable_webserver`` for the webserver
    set, and ``php_enable_php_fpm and php_fpm_state == 'started'`` for the FPM set
    (which unions the direct configure notifications with the webserver chain).
    An empty set yields an empty deploy -- the converged no-op Ansible gives.
    """
    deploys = []
    for name in sorted(webserver_hosts):
        deploys.append(
            service_handler(
                (name,),
                resolved[name]["webserver_daemon"],
                action="restarted",
                when=is_ubuntu,
            )
        )
    for name in sorted(fpm_hosts):
        deploys.append(
            service_handler(
                (name,),
                resolved[name]["fpm_daemon"],
                action=resolved[name]["fpm_handler_action"],
                when=is_ubuntu,
            )
        )
    return compose(*deploys)


async def execute(inventory: list[dict[str, Any]], deploy: Any, title: str) -> Any:
    """Dry-run then really run one deploy, printing both renderings.

    The dry run is Ansible's ``--check`` as a return value: it stops after
    prepare, so the plan shows what *would* change without touching a host. Both
    runs use the very same deploy object -- for a factory-built deploy that means
    the same frozen snapshot, which is what makes the determinism guard hold.
    """
    plans, _dry, _hosts = await run(inventory, deploy, dry=True)
    print(f"\n== {title} -- plan (prepare phase, --dry) ==")
    print(format_plan(plans))
    _plans, report, _hosts = await run(inventory, deploy)
    print(f"\n== {title} -- run log (apply phase) ==")
    assert report is not None
    print(format_log(report))
    return report


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        hosts = [Host.from_inventory(entry) for entry in inventory]
        targets = [host for host in hosts if is_ubuntu(host)]
        skipped = [host.name for host in hosts if not is_ubuntu(host)]
        if skipped:
            print("\n== Skipped (when guard is_ubuntu): " + ", ".join(skipped) + " ==")
        if not targets:
            print("\nNo host in the Ubuntu family; nothing to deploy.")
            return

        # The role's variable resolution, before any host is contacted.
        settings = {host.name: role_settings(host) for host in targets}

        # The distribution major selects the PHP version table, which fixes the
        # conf paths and handler daemons -- gathered BEFORE deploy 1.
        # gather_fact does not close its hosts, so the finally does.
        try:
            distributions = await gather_fact(targets, LinuxDistribution)
        finally:
            for host in targets:
                await host.aclose()
        resolved = {
            host.name: resolved_vars(settings[host.name], distributions[host.name])
            for host in targets
        }
        print("\n== Resolved Xdebug builds ==")
        for host in targets:
            rv = resolved[host.name]
            print(
                f"{host.name}: xdebug {rv['xversion']} on PHP {rv['version']} "
                f"({distributions[host.name].get('name')}) -> {rv['installed_so']}"
            )

        # Deploy 1: toolchain + module directory + source extraction.
        setup_report = await execute(
            inventory, build_setup(resolved), "Deploy 1: build setup + untar Xdebug"
        )

        # The build's creates: gate, read AFTER the extraction (the source tree
        # exists now, but modules/xdebug.so does not until the build runs). The
        # frozen need_build set gates the action -- a Shell action has no
        # in-operation creates. gather_fact does not close its hosts.
        need_build: set[str] = set()
        try:
            for host in targets:
                rv = resolved[host.name]
                exists = (await gather_fact([host], FileExists, rv["built_so"]))[
                    host.name
                ]
                if not exists:
                    need_build.add(host.name)
        finally:
            for host in targets:
                await host.aclose()
        need_build_set = frozenset(need_build)

        # The build ACTION between deploys: phpize && ./configure && make on the
        # need_build hosts only. Hosts are grouped by their rendered command (the
        # workspace/version may differ per host). gather_action re-connects and
        # closes its hosts; a non-zero exit is raised as CommandError (the action
        # interprets nothing itself).
        if need_build_set:
            groups: dict[str, list[Host]] = {}
            for host in targets:
                if host.name in need_build_set:
                    cmd = build_command(resolved[host.name])
                    groups.setdefault(cmd, []).append(host)
            print(
                "\n== Between deploys: build Xdebug (phpize && ./configure && make) =="
            )
            for cmd, group in groups.items():
                results = await gather_action(group, Shell, command=cmd, sudo=True)
                for name, result in results.items():
                    if result["exit_status"] != 0:
                        raise CommandError(
                            name, cmd, result["exit_status"], result["stderr"]
                        )
                    print(f"{name}: built (exit 0)")
        else:
            print("\n== Between deploys: Xdebug already built on every host (no-op) ==")

        # Deploy 2: install the module + configure PHP.
        install_report = await execute(
            inventory, build_install(resolved), "Deploy 2: install module + configure"
        )

        # The handlers. restart webserver fires when any notifying task changed
        # and php_enable_webserver; the chained restart php-fpm fires from that
        # chain plus the direct configure notifications, gated by
        # php_enable_php_fpm and php_fpm_state == 'started'. All notifying
        # requests live in build_install, so only its report is consulted.
        webserver_notified = changed_hosts(install_report, WEBSERVER_NOTIFY_NAMES)
        webserver_hosts = frozenset(
            name
            for name in webserver_notified
            if settings[name]["php_enable_webserver"]
        )
        fpm_direct = changed_hosts(install_report, FPM_NOTIFY_NAMES)
        fpm_hosts = frozenset(
            name
            for name in (webserver_hosts | fpm_direct)
            if settings[name]["php_enable_php_fpm"]
            and settings[name]["php_fpm_state"] == "started"
        )
        print("\n== Handler notification sets ==")
        print(f"restart webserver: {sorted(webserver_hosts) or '(none)'}")
        print(f"restart php-fpm:   {sorted(fpm_hosts) or '(none)'}")
        handler_report = await execute(
            inventory,
            build_handler(resolved, webserver_hosts, fpm_hosts),
            "Deploy 3: restart handlers",
        )

        changed = sum(
            1
            for report in (setup_report, install_report, handler_report)
            for entry in report.log
            if entry.changed
        )
        print(f"\n== Done: {changed} changed request(s) across the three deploys ==")
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        # The inventory is missing/unreadable/not JSON, the control-side tarball
        # fetch failed (URLError/HTTPError are OSError subclasses), or a role
        # variable is invalid. The message is the whole report.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema; the
        # message names the offending entry by its list index.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # Every host-level failure is a typed error naming the host and the
        # failing command. Print that, no traceback, and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
