# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Migration of the PHP Ansible role (``geerlingguy.php``) to reemote.

The role at ``https://github.com/geerlingguy/ansible-role-php`` installs and
configures PHP on RHEL/CentOS and Debian/Ubuntu. This program is its
**Ubuntu default (package-manager) path** expressed as a reemote deploy: the
apt cache refresh and the version-specific PHP packages, the packaged
APCu/OpCache ini cleanup, the configuration directories and a rendered
``php.ini`` in every conf path, the APCu/OpCache stray-config sweep and the
role's own extension ini files, the optional PHP-FPM pools and service, and the
role's two chained handlers (``restart webserver`` -> ``restart php-fpm``).
Every role variable is read from the inventory entry's extra keys (reemote's
``host_vars`` mechanism), so ``php_memory_limit`` and friends live beside
``host``/``connection`` in ``inventory.json``.

Target: hosts in the ``"Ubuntu"`` OS family. Every request carries
``when=is_ubuntu``, and a skipped request is never materialized -- the other
hosts in the shared inventory are not even contacted. The role's RHEL/CentOS
branch and the opt-in ``php_install_from_source`` path are out of scope (a
Phase 0 single-OS, default-path decision), so the ``include_vars`` OS dispatch
collapses to a frozen Ubuntu version table below.

Why the program is a *sequence* of deploys rather than one
-------------------------------------------------------

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role
reads the distribution major version (``include_vars`` dispatch), registers the
package-install outcome (``php_package_install.changed``), ``find``s stray
extension configs and ``stat``s the FPM pool directory -- mutable reads a single
deploy may not branch on. Each becomes a **gather -> snapshot -> factory** step:
read between two complete runs, freeze the answer into a per-host dict, close a
deploy factory over it. The barriers below are load-bearing, not stylistic:

1. ``gather_fact(LinuxDistribution)`` -- the major version selects the
   ``vars/Ubuntu-{20,22,24}.yml`` PHP version (7.4 / 8.1 / 8.3, else the
   ``vars/Debian.yml`` 8.2 fallback), which fixes the package list and paths
2. ``build_install``        -- apt cache refresh + the PHP packages
3. ``changed_hosts``        -- the package-install outcome (``register`` +
   ``when`` recipe) gates the packaged-ini cleanup
4. ``build_packaged_cleanup`` -- delete the packaged APCu/OpCache ini files,
   only on hosts whose package install changed
5. ``gather_fact(FindInFiles)`` -- the stray APCu/OpCache config sweep, plus
   ``gather_fact(FileExists)`` for the FPM pool directory (after the cleanup)
6. ``build_configure``      -- directories, php.ini, stray removals, the role
   extension ini files, FPM pools and service
7. ``build_handler``        -- ``changed_hosts`` over the notifying names,
   ``restart webserver`` then the chained ``restart php-fpm``

Implementation notes
--------------------

No implementation gap blocks this migration. Every task on the Ubuntu default
path maps to a covered operation or a documented recipe; the one test-framework
gap found during the sibling nginx migration (``SimulatedHost.remove_file``
leaving ``remote_files``, issue #18) is **closed** on this branch, so the offline
test asserts removals against the content store directly. The deliberate
deviations from Ansible semantics are recorded at the point of use and in
``ansible-role-php.md``:

* ``find: {paths, contains}`` maps to ``files.FindInFiles`` (a recursive
  ``grep -rlE``). Ansible's ``find`` is shallow by default, skips dotfiles and
  anchors ``contains`` at the line start; the grep recurses, includes dotfiles
  and matches anywhere in a line. The shallowness and dotfile differences are
  recovered control-side (``stray_configs`` keeps only direct, non-hidden
  children); the broader line match is kept -- it only ever widens the cleanup
  of stray files, and on a stock Ubuntu ``conf.d`` (whose packaged entries are
  symlinks both searches skip) the result is identical.
* ``files.directory`` has no ``follow`` parameter; the role's ``follow: true``
  is inert on stock Ubuntu, where every conf path is a real directory.
* the packaged ``conf.d`` ini files are symlinks, so removals use
  ``files.file(present=False, force=True)`` (the ``File`` fact reads a symlink
  as a non-file).
* ``service_handler`` offers ``restarted``/``reloaded``/``running``; the role's
  ``php_fpm_handler_state`` accepts those three (``started`` -> ``running``).

No template engine is needed: every default-path template (``php.ini.j2``,
``apc.ini.j2``, ``opcache.ini.j2``, ``www.conf.j2``) is pure variable
substitution plus one ``join`` and two ``{% if %}`` blocks, so the renders below
are plain Python f-strings. reemote is the only control-side dependency.

Run it with::

    uv run python examples/roles/ansible-role-php/ansible-role-php.py
"""

import asyncio
import json
import posixpath
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    FileExists,
    FindInFiles,
    Host,
    HostConnectionError,
    InventoryError,
    LinuxDistribution,
    OperationRequest,
    PrivilegeError,
    apt,
    changed_hosts,
    compose,
    files,
    format_log,
    format_plan,
    gather_fact,
    op,
    run,
    service_handler,
    systemd,
    validate_inventory,
)

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path only) ---

#: examples/roles/ansible-role-php/ansible-role-php.py -> the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

#: ``setup-Debian.yml``'s ``apt: update_cache=yes cache_valid_time=86400``.
CACHE_VALID_TIME = 86400

#: ``vars/Ubuntu-{20,22,24}.yml`` -- each sets ``__php_default_version_debian``;
#: the ``with_first_found`` ``skip: true`` means any other major falls back to
#: ``vars/Debian.yml``'s 8.2. Keyed on ``LinuxDistribution.major``.
UBUNTU_PHP_VERSIONS = {20: "7.4", 22: "8.1", 24: "8.3"}
DEBIAN_DEFAULT_PHP_VERSION = "8.2"

#: ``ansible_managed``'s ansible.cfg default, rendered by www.conf.j2's
#: ``{{ ansible_managed | comment(decoration='; ') }}``. Frozen to the default
#: (a configurable ansible.cfg value has no analogue here).
ANSIBLE_MANAGED = "Ansible managed"

#: ``configure-apcu.yml`` / ``configure-opcache.yml`` ``find: contains`` regexes,
#: verbatim. GNU grep -E understands ``\s`` and ``\.`` as written.
APCU_PATTERN = r"extension(\s+)?=(\s+)?apc[u]?\.so"
OPCACHE_PATTERN = r"zend_extension(\s+)?=(\s+)?opcache\.so"

#: ``php_fpm_handler_state`` -> the ``service_handler`` action. Ansible's
#: ``stopped`` is not a handler verb here (service_handler offers only these
#: three); the role documents ``restarted``/``reloaded``.
FPM_HANDLER_ACTIONS = {"restarted": "restarted", "reloaded": "reloaded", "started": "running"}

#: The notifying request names -- the role's task names, verbatim. Every one of
#: these queues ``restart webserver`` (handlers/main.yml). They are module
#: constants so ``changed_hosts`` can select them across the install, cleanup
#: and configure reports.
WEBSERVER_NOTIFY_NAMES = frozenset(
    {
        "Ensure PHP packages are installed",
        "Delete APCu configuration file if this role will provide one",
        "Delete OpCache configuration file if this role will provide one",
        "Place PHP configuration file in place",
        "Remove any non-role-supplied APCu config files",
        "Remove any non-role-supplied OpCache config files",
        "Ensure APCu config file is present",
        "Remove APCu config file if APC is disabled",
        "Ensure OpCache config file is present",
        "Remove OpCache config file if OpCache is disabled",
    }
)
#: The pool template task queues ``restart php-fpm`` directly.
FPM_NOTIFY_NAMES = frozenset({"Create fpm pools"})

#: The role defaults this migration honours (``defaults/main.yml``). The
#: ``php_`` prefix is kept so an inventory entry reads like the role's
#: ``host_vars``. Version-dependent values default to None -- the role's
#: ``set_fact`` falls back to the ``vars/Debian.yml`` ``__php_*`` value when
#: undefined; ``resolved_vars`` applies that fallback after the version is
#: known. ``php_enablerepo`` (RHEL-only) and ``php_executable`` (source path)
#: are accepted for parity but inert on this path.
ROLE_DEFAULTS: dict[str, Any] = {
    "php_enablerepo": "",
    "php_packages_extra": [],
    "php_default_version_debian": None,
    "php_packages_state": "present",
    "php_install_recommends": True,
    "php_enable_webserver": True,
    "php_enable_php_fpm": False,
    "php_fpm_state": "started",
    "php_fpm_handler_state": "restarted",
    "php_fpm_enabled_on_boot": True,
    "php_fpm_listen": "127.0.0.1:9000",
    "php_fpm_listen_allowed_clients": "127.0.0.1",
    "php_fpm_pm_max_children": 50,
    "php_fpm_pm_start_servers": 5,
    "php_fpm_pm_min_spare_servers": 5,
    "php_fpm_pm_max_spare_servers": 5,
    "php_fpm_pm_max_requests": 0,
    "php_fpm_pm_status_path": "",
    "php_fpm_pools": None,
    "php_executable": "php",
    "php_opcache_zend_extension": "opcache.so",
    "php_opcache_enable": "1",
    "php_opcache_enable_cli": "0",
    "php_opcache_memory_consumption": "96",
    "php_opcache_interned_strings_buffer": "16",
    "php_opcache_max_accelerated_files": "4096",
    "php_opcache_max_wasted_percentage": "5",
    "php_opcache_validate_timestamps": "1",
    "php_opcache_revalidate_path": "0",
    "php_opcache_revalidate_freq": "2",
    "php_opcache_max_file_size": "0",
    "php_opcache_blacklist_filename": "",
    "php_enable_apc": True,
    "php_apc_shm_size": "96M",
    "php_apc_enable_cli": "0",
    "php_use_managed_ini": True,
    "php_expose_php": "On",
    "php_memory_limit": "256M",
    "php_max_execution_time": "60",
    "php_max_input_time": "60",
    "php_max_input_vars": "1000",
    "php_realpath_cache_size": "32K",
    "php_file_uploads": "On",
    "php_upload_max_filesize": "64M",
    "php_max_file_uploads": "20",
    "php_post_max_size": "32M",
    "php_date_timezone": "America/Chicago",
    "php_allow_url_fopen": "On",
    "php_sendmail_path": "/usr/sbin/sendmail -t -i",
    "php_output_buffering": "4096",
    "php_short_open_tag": "Off",
    "php_disable_functions": [],
    "php_precision": 14,
    "php_serialize_precision": "-1",
    "php_session_cookie_lifetime": 0,
    "php_session_gc_probability": 1,
    "php_session_gc_divisor": 1000,
    "php_session_gc_maxlifetime": 1440,
    "php_session_save_handler": "files",
    "php_session_save_path": "",
    "php_error_reporting": "E_ALL & ~E_DEPRECATED & ~E_STRICT",
    "php_display_errors": "Off",
    "php_display_startup_errors": "Off",
    "php_packages": None,
    "php_webserver_daemon": None,
    "php_conf_paths": None,
    "php_extension_conf_paths": None,
    "php_apc_conf_filename": None,
    "php_opcache_conf_filename": None,
    "php_fpm_daemon": None,
    "php_fpm_conf_path": None,
    "php_fpm_pool_conf_path": None,
    "php_fpm_pool_user": None,
    "php_fpm_pool_group": None,
}


# --- pure helpers ------------------------------------------------------------


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of entries, each carrying at least
    ``host`` and ``connection``; every other key is preserved and reaches
    the deploy as ``host.extra`` -- the role variables live there.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data and stays a stable module-level
    function, so both deploy phases reach the same eligibility decision.
    A skipped request is never materialized, so the Alpine and CentOS
    hosts in the shared inventory are not contacted at all.
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
    raise ValueError(f"{key} must be a boolean (yes/no/true/false/on/off/1/0), got {value!r}")


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


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = version.split(".")
    if not parts or not all(part.isdigit() for part in parts):
        raise ValueError(f"php_default_version_debian must be dotted digits, got {version!r}")
    return tuple(int(part) for part in parts)


def _default_pools(settings: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """The ``defaults/main.yml`` www pool, its ``{{ php_fpm_* }}`` refs resolved."""
    return (
        {
            "pool_name": "www",
            "pool_template": "www.conf.j2",
            "pool_listen": settings["php_fpm_listen"],
            "pool_listen_allowed_clients": settings["php_fpm_listen_allowed_clients"],
            "pool_pm": "dynamic",
            "pool_pm_max_children": settings["php_fpm_pm_max_children"],
            "pool_pm_start_servers": settings["php_fpm_pm_start_servers"],
            "pool_pm_min_spare_servers": settings["php_fpm_pm_min_spare_servers"],
            "pool_pm_max_spare_servers": settings["php_fpm_pm_max_spare_servers"],
            "pool_pm_max_requests": settings["php_fpm_pm_max_requests"],
            "pool_pm_status_path": settings["php_fpm_pm_status_path"],
        },
    )


def _pools(value: Any, settings: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Validate ``php_fpm_pools``; None resolves to the default www pool.

    Each pool needs ``pool_name`` and ``pool_listen`` (www.conf.j2's
    ``| mandatory`` filters). Only the bundled ``www.conf.j2`` is renderable
    without a template engine, so any other ``pool_template`` is rejected.
    """
    if value is None:
        return _default_pools(settings)
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"php_fpm_pools must be a list of pools, got {type(value).__name__}")
    pools = []
    for index, pool in enumerate(value):
        if not isinstance(pool, dict):
            raise TypeError(f"php_fpm_pools[{index}] must be a mapping")
        for required in ("pool_name", "pool_listen"):
            if not isinstance(pool.get(required), str) or not pool[required]:
                raise ValueError(f"php_fpm_pools[{index}] needs a non-empty {required}")
        template = pool.get("pool_template", "www.conf.j2")
        if template != "www.conf.j2":
            raise ValueError(
                f"php_fpm_pools[{index}]: only the bundled www.conf.j2 is supported "
                f"(got {template!r}); a custom pool template needs a renderer"
            )
        pools.append(pool)
    return tuple(pools)


def role_settings(host: Host) -> dict[str, Any]:
    """Resolve a host's role variables: defaults overlaid with ``host.extra``.

    Validates enums, booleans and list shapes before any host is contacted, so
    a bad value fails fast with a clear message rather than mid-deploy. The
    version-dependent ``None`` values are left for ``resolved_vars`` (they need
    the gathered distribution major).
    """
    settings = {**ROLE_DEFAULTS, **host.extra}
    settings["php_packages_state"] = _enum(
        settings["php_packages_state"], ("present", "latest", "absent"), "php_packages_state"
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
    for key in (
        "php_install_recommends",
        "php_enable_webserver",
        "php_enable_php_fpm",
        "php_fpm_enabled_on_boot",
        "php_enable_apc",
        "php_use_managed_ini",
    ):
        settings[key] = _ansible_bool(settings[key], key)
    # ``php_opcache_enable`` gates tasks (``| bool``) *and* renders verbatim
    # into opcache.ini (``opcache.enable={{ php_opcache_enable }}``), so the
    # raw value stays for the render and a derived bool carries the gate --
    # boolifying in place would render ``True`` where Ansible renders ``1``.
    settings["php_opcache_enabled"] = _ansible_bool(
        settings["php_opcache_enable"], "php_opcache_enable"
    )
    settings["php_packages_extra"] = _string_tuple(settings["php_packages_extra"], "php_packages_extra")
    settings["php_disable_functions"] = _string_tuple(
        settings["php_disable_functions"], "php_disable_functions"
    )
    for key in ("php_packages", "php_conf_paths", "php_extension_conf_paths"):
        settings[key] = _optional_string_tuple(settings[key], key)
    for key in (
        "php_default_version_debian",
        "php_webserver_daemon",
        "php_apc_conf_filename",
        "php_opcache_conf_filename",
        "php_fpm_daemon",
        "php_fpm_conf_path",
        "php_fpm_pool_conf_path",
        "php_fpm_pool_user",
        "php_fpm_pool_group",
    ):
        settings[key] = _optional_str(settings[key], key)
    if settings["php_default_version_debian"] is not None:
        _version_tuple(settings["php_default_version_debian"])
    settings["php_fpm_pools"] = _pools(settings["php_fpm_pools"], settings)
    return settings


def resolved_vars(settings: dict[str, Any], distribution: dict[str, Any]) -> dict[str, Any]:
    """The ``vars/Debian.yml`` ``__php_*`` values, resolved for this host.

    The version comes from the user's ``php_default_version_debian`` if set,
    else the Ubuntu major-version table, else the Debian 8.2 fallback -- the
    role's ``include_vars`` + ``set_fact`` chain. Every ``__php_*`` fallback the
    role's ``set_fact`` tasks apply is reproduced here, and the PHP < 8.0 JSON
    package is appended exactly as ``main.yml`` does.
    """
    version = settings["php_default_version_debian"] or UBUNTU_PHP_VERSIONS.get(
        distribution.get("major"), DEBIAN_DEFAULT_PHP_VERSION
    )
    _version_tuple(version)
    base = [
        f"php{version}-common",
        f"php{version}-cli",
        f"php{version}-dev",
        f"php{version}-fpm",
        "libpcre3-dev",
        f"php{version}-gd",
        f"php{version}-curl",
        f"php{version}-imap",
        f"php{version}-opcache",
        f"php{version}-xml",
        f"php{version}-mbstring",
        f"php{version}-apcu",
        f"php{version}-sqlite3",
    ]
    if _version_tuple(version) < (8, 0):
        base.append(f"php{version}-json")
    packages = settings["php_packages"] if settings["php_packages"] is not None else tuple(base)
    packages = tuple(packages) + settings["php_packages_extra"]
    default_conf = (
        f"/etc/php/{version}/fpm",
        f"/etc/php/{version}/apache2",
        f"/etc/php/{version}/cli",
    )
    conf_paths = settings["php_conf_paths"] or default_conf
    ext_paths = settings["php_extension_conf_paths"] or tuple(f"{path}/conf.d" for path in default_conf)
    fpm_conf_path = settings["php_fpm_conf_path"] or f"/etc/php/{version}/fpm"
    pool_conf_path = settings["php_fpm_pool_conf_path"] or f"{fpm_conf_path}/pool.d/www.conf"
    return {
        "version": version,
        "distribution_name": distribution.get("name"),
        "packages": packages,
        "conf_paths": tuple(conf_paths),
        "ext_paths": tuple(ext_paths),
        "apc_filename": settings["php_apc_conf_filename"] or "20-apcu.ini",
        "opcache_filename": settings["php_opcache_conf_filename"] or "10-opcache.ini",
        "webserver_daemon": settings["php_webserver_daemon"] or "apache2",
        "fpm_daemon": settings["php_fpm_daemon"] or f"php{version}-fpm",
        "fpm_conf_path": fpm_conf_path,
        "pool_conf_path": pool_conf_path,
        "pool_user": settings["php_fpm_pool_user"] or "www-data",
        "pool_group": settings["php_fpm_pool_group"] or "www-data",
    }


# --- control-side template renders (pure Python, no Jinja2) ------------------


def render_php_ini(s: dict[str, Any]) -> str:
    """``templates/php.ini.j2`` -- pure substitution plus one ``join(",")``.

    Reproduced verbatim (Ansible's ``trim_blocks`` default has no effect: the
    template has no block tags). ``php_disable_functions`` is the only filter.
    """
    disable_functions = ",".join(s["php_disable_functions"])
    return f"""[PHP]

;;;;;;;;;;;;;;;;;;;;
; Language Options ;
;;;;;;;;;;;;;;;;;;;;

engine = On
short_open_tag = {s['php_short_open_tag']}
precision = {s['php_precision']}
output_buffering = {s['php_output_buffering']}

zlib.output_compression = Off

implicit_flush = Off
unserialize_callback_func =
serialize_precision = {s['php_serialize_precision']}
disable_functions = {disable_functions}
disable_classes =

zend.enable_gc = On

;;;;;;;;;;;;;;;;;
; Miscellaneous ;
;;;;;;;;;;;;;;;;;

expose_php = {s['php_expose_php']}

;;;;;;;;;;;;;;;;;;;
; Resource Limits ;
;;;;;;;;;;;;;;;;;;;

max_execution_time = {s['php_max_execution_time']}
max_input_time = {s['php_max_input_time']}
max_input_vars = {s['php_max_input_vars']}
memory_limit = {s['php_memory_limit']}

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
; Error handling and logging ;
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

error_reporting = {s['php_error_reporting']}
display_errors = {s['php_display_errors']}
display_startup_errors = {s['php_display_startup_errors']}
log_errors = On
log_errors_max_len = 1024
ignore_repeated_errors = Off
ignore_repeated_source = Off
report_memleaks = On
track_errors = Off
html_errors = On

;;;;;;;;;;;;;;;;;
; Data Handling ;
;;;;;;;;;;;;;;;;;

variables_order = "GPCS"
request_order = "GP"
register_argc_argv = Off
auto_globals_jit = On

post_max_size = {s['php_post_max_size']}
auto_prepend_file =
auto_append_file =

default_mimetype = "text/html"

;;;;;;;;;;;;;;;;;;;;;;;;;
; Paths and Directories ;
;;;;;;;;;;;;;;;;;;;;;;;;;

doc_root =
user_dir =

enable_dl = Off

realpath_cache_size = {s['php_realpath_cache_size']}

;;;;;;;;;;;;;;;;
; File Uploads ;
;;;;;;;;;;;;;;;;

file_uploads = {s['php_file_uploads']}
upload_max_filesize = {s['php_upload_max_filesize']}
max_file_uploads = {s['php_max_file_uploads']}

;;;;;;;;;;;;;;;;;;
; Fopen wrappers ;
;;;;;;;;;;;;;;;;;;

allow_url_fopen = {s['php_allow_url_fopen']}
allow_url_include = Off

default_socket_timeout = 60

;;;;;;;;;;;;;;;;;;;
; Module Settings ;
;;;;;;;;;;;;;;;;;;;

[CLI Server]
cli_server.color = On

[Date]
date.timezone = {s['php_date_timezone']}

[Pdo_mysql]
pdo_mysql.cache_size = 2000
pdo_mysql.default_socket=

[mail function]
; For Win32 only.
SMTP = localhost
smtp_port = 25

; For Unix only.  You may supply arguments as well (default: "sendmail -t -i").
sendmail_path = {s['php_sendmail_path']}

mail.add_x_header = On

[SQL]
sql.safe_mode = Off

[ODBC]
odbc.allow_persistent = On
odbc.check_persistent = On
odbc.max_persistent = -1
odbc.max_links = -1
odbc.defaultlrl = 4096
odbc.defaultbinmode = 1

[MySQL]
mysql.allow_local_infile = On
mysql.allow_persistent = On
mysql.cache_size = 2000
mysql.max_persistent = -1
mysql.max_links = -1
mysql.default_port =
mysql.default_socket =
mysql.default_host =
mysql.default_user =
mysql.default_password =
mysql.connect_timeout = 60
mysql.trace_mode = Off

[MySQLi]
mysqli.max_persistent = -1
mysqli.allow_persistent = On
mysqli.max_links = -1
mysqli.cache_size = 2000
mysqli.default_port = 3306
mysqli.default_socket =
mysqli.default_host =
mysqli.default_user =
mysqli.default_pw =
mysqli.reconnect = Off

[mysqlnd]
mysqlnd.collect_statistics = On
mysqlnd.collect_memory_statistics = Off

[PostgreSQL]
pgsql.allow_persistent = On
pgsql.auto_reset_persistent = Off
pgsql.max_persistent = -1
pgsql.max_links = -1
pgsql.ignore_notice = 0
pgsql.log_notice = 0

[bcmath]
bcmath.scale = 0

[Session]
session.save_handler = {s['php_session_save_handler']}
session.save_path = {s['php_session_save_path']}
session.use_cookies = 1
session.use_only_cookies = 1
session.name = PHPSESSID
session.auto_start = 0

session.cookie_lifetime = {s['php_session_cookie_lifetime']}
session.cookie_path = /
session.cookie_domain =
session.cookie_httponly =

session.serialize_handler = php

session.gc_probability = {s['php_session_gc_probability']}
session.gc_divisor = {s['php_session_gc_divisor']}
session.gc_maxlifetime = {s['php_session_gc_maxlifetime']}

session.referer_check =

session.cache_limiter = nocache
session.cache_expire = 180

session.use_trans_sid = 0

session.hash_function = 0
session.hash_bits_per_character = 5

url_rewriter.tags = "a=href,area=href,frame=src,input=src,form=fakeentry"

[MSSQL]
mssql.allow_persistent = On
mssql.max_persistent = -1
mssql.max_links = -1
mssql.min_error_severity = 10
mssql.min_message_severity = 10
mssql.compatability_mode = Off
mssql.secure_connection = Off

[Tidy]
tidy.clean_output = Off

[soap]
soap.wsdl_cache_enabled=1
soap.wsdl_cache_dir="/tmp"
soap.wsdl_cache_ttl=86400
soap.wsdl_cache_limit = 5

[ldap]
ldap.max_links = -1
"""


def render_apc_ini(s: dict[str, Any]) -> str:
    """``templates/apc.ini.j2`` -- pure substitution."""
    return (
        "extension=apcu.so\n"
        f"apc.shm_size={s['php_apc_shm_size']}\n"
        f"apc.enable_cli={s['php_apc_enable_cli']}\n"
        "apc.rfc1867=1\n"
    )


def render_opcache_ini(s: dict[str, Any]) -> str:
    """``templates/opcache.ini.j2`` -- substitution plus one ``{% if %}``.

    The blacklist line appears only when ``php_opcache_blacklist_filename`` is
    non-empty (Ansible's ``trim_blocks=True`` drops the block-tag newlines).
    """
    content = (
        f"zend_extension={s['php_opcache_zend_extension']}\n"
        f"opcache.enable={s['php_opcache_enable']}\n"
        f"opcache.enable_cli={s['php_opcache_enable_cli']}\n"
        f"opcache.memory_consumption={s['php_opcache_memory_consumption']}\n"
        f"opcache.interned_strings_buffer={s['php_opcache_interned_strings_buffer']}\n"
        f"opcache.max_accelerated_files={s['php_opcache_max_accelerated_files']}\n"
        f"opcache.max_wasted_percentage={s['php_opcache_max_wasted_percentage']}\n"
        f"opcache.validate_timestamps={s['php_opcache_validate_timestamps']}\n"
        f"opcache.revalidate_path={s['php_opcache_revalidate_path']}\n"
        f"opcache.revalidate_freq={s['php_opcache_revalidate_freq']}\n"
        f"opcache.max_file_size={s['php_opcache_max_file_size']}\n"
    )
    if s["php_opcache_blacklist_filename"] != "":
        content += f"opcache.blacklist_filename={s['php_opcache_blacklist_filename']}\n"
    return content


def _jinja_default(value: Any, default: Any) -> Any:
    """Jinja2's ``default(x, true)``: the default when *value* is falsy.

    www.conf.j2 uses the boolean form, so the role's default
    ``php_fpm_pm_max_requests: 0`` renders as 500 (0 is falsy), faithfully.
    """
    return value if value else default


def render_www_conf(pool: dict[str, Any], s: dict[str, Any], rv: dict[str, Any]) -> str:
    """``templates/www.conf.j2`` -- the bundled FPM pool template.

    ``pool_name``/``pool_listen`` are mandatory (validated in ``role_settings``);
    the rest use Jinja2's falsy-default form. ``ansible_managed`` is frozen to
    its ansible.cfg default and rendered with the ``; `` comment decoration.
    """
    status_path = pool.get("pool_pm_status_path", "")
    content = (
        f"; {ANSIBLE_MANAGED}\n"
        "\n"
        f"[{pool['pool_name']}]\n"
        f"listen = {pool['pool_listen']}\n"
        f"listen.allowed_clients = {_jinja_default(pool.get('pool_listen_allowed_clients'), '127.0.0.1')}\n"
        f"user = {rv['pool_user']}\n"
        f"group = {rv['pool_group']}\n"
        "\n"
        f"listen.owner = {rv['pool_user']}\n"
        f"listen.group = {rv['pool_group']}\n"
        "\n"
        f"pm = {_jinja_default(pool.get('pool_pm'), 'dynamic')}\n"
        f"pm.max_children = {_jinja_default(pool.get('pool_pm_max_children'), 50)}\n"
        f"pm.start_servers = {_jinja_default(pool.get('pool_pm_start_servers'), 5)}\n"
        f"pm.min_spare_servers = {_jinja_default(pool.get('pool_pm_min_spare_servers'), 5)}\n"
        f"pm.max_spare_servers = {_jinja_default(pool.get('pool_pm_max_spare_servers'), 5)}\n"
        f"pm.max_requests = {_jinja_default(pool.get('pool_pm_max_requests'), 500)}\n"
    )
    if len(str(status_path)):
        content += f"pm.status_path = {status_path}\n"
    return content


def stray_configs(
    hits: tuple[str, ...], ext_paths: tuple[str, ...], managed_filename: str
) -> tuple[str, ...]:
    """The non-role-supplied configs to remove, from a ``FindInFiles`` sweep.

    Recovers Ansible ``find``'s default semantics the grep does not share:
    only *direct* children of the searched directories (``find`` is shallow,
    ``grep -r`` recurses) and no dotfiles (``find`` skips hidden files). The
    role's own filename is excluded -- the role manages that file itself. The
    result is sorted so the request sequence is deterministic (grep's traversal
    order is filesystem-dependent). The broader grep line-match is kept: it
    only widens the stray cleanup, never the role's own files.
    """
    directories = frozenset(ext_paths)
    return tuple(
        sorted(
            {
                hit
                for hit in hits
                if posixpath.dirname(hit) in directories
                and not posixpath.basename(hit).startswith(".")
                and posixpath.basename(hit) != managed_filename
            }
        )
    )


# --- frozen per-host plans ---------------------------------------------------


def install_plan(settings: dict[str, Any], distribution: dict[str, Any]) -> dict[str, Any]:
    """Deploy 1 input: the package list and its apt flags."""
    rv = resolved_vars(settings, distribution)
    state = settings["php_packages_state"]
    return {
        "packages": rv["packages"],
        "present": state != "absent",
        "latest": state == "latest",
        "no_recommends": not settings["php_install_recommends"],
    }


def packaged_cleanup_plan(settings: dict[str, Any], distribution: dict[str, Any]) -> dict[str, Any]:
    """Deploy 2 input: the packaged extension ini paths to delete on install.

    ``setup-Debian.yml`` deletes these only when the package install changed
    *and* the extension is enabled -- the changed gate is applied by the
    factory (a frozen ``changed_hosts`` set), the enable gate here.
    """
    rv = resolved_vars(settings, distribution)
    apc = (
        tuple(f"{ext}/{rv['apc_filename']}" for ext in rv["ext_paths"])
        if settings["php_enable_apc"]
        else ()
    )
    opcache = (
        tuple(f"{ext}/{rv['opcache_filename']}" for ext in rv["ext_paths"])
        if settings["php_opcache_enabled"]
        else ()
    )
    return {"apc_removals": apc, "opcache_removals": opcache}


def configure_plan(
    settings: dict[str, Any],
    distribution: dict[str, Any],
    apcu_hits: tuple[str, ...],
    opcache_hits: tuple[str, ...],
    pool_dir_exists: bool,
) -> dict[str, Any]:
    """Deploy 3 input: every configure read frozen control-side.

    The stray-config sweeps (``FindInFiles``) and the pool-directory existence
    (``FileExists``) were gathered between runs; the templates are rendered
    here. The result is a pure function of static input, so the deploy is
    branch-free with respect to facts and run outcomes.
    """
    rv = resolved_vars(settings, distribution)
    php_ini_content = render_php_ini(settings)
    php_ini = (
        tuple((f"{path}/php.ini", php_ini_content) for path in rv["conf_paths"])
        if settings["php_use_managed_ini"]
        else ()
    )
    apc_content = render_apc_ini(settings)
    opcache_content = render_opcache_ini(settings)
    if settings["php_enable_apc"]:
        apc_installs = tuple((f"{ext}/{rv['apc_filename']}", apc_content) for ext in rv["ext_paths"])
        apc_removals: tuple[str, ...] = ()
    else:
        apc_installs = ()
        apc_removals = tuple(f"{ext}/{rv['apc_filename']}" for ext in rv["ext_paths"])
    if settings["php_opcache_enabled"]:
        opcache_installs = tuple(
            (f"{ext}/{rv['opcache_filename']}", opcache_content) for ext in rv["ext_paths"]
        )
        opcache_removals: tuple[str, ...] = ()
    else:
        opcache_installs = ()
        opcache_removals = tuple(f"{ext}/{rv['opcache_filename']}" for ext in rv["ext_paths"])
    pool_dir = posixpath.dirname(rv["pool_conf_path"])
    pools = (
        tuple(
            (f"{pool_dir}/{pool['pool_name']}.conf", render_www_conf(pool, settings, rv))
            for pool in settings["php_fpm_pools"]
        )
        if settings["php_enable_php_fpm"]
        else ()
    )
    fpm_service = None
    # The role's service task is guarded by ``distribution != "Debian"``; on
    # the Ubuntu target that is always true, but the gathered name keeps it
    # faithful for a Debian host that slips into the group.
    if settings["php_enable_php_fpm"] and rv["distribution_name"] != "Debian":
        state = settings["php_fpm_state"]
        fpm_service = {
            "service": rv["fpm_daemon"],
            "running": state != "stopped",
            "restarted": state == "restarted",
            "reloaded": state == "reloaded",
            "enabled": settings["php_fpm_enabled_on_boot"],
        }
    return {
        "directories": rv["conf_paths"] + rv["ext_paths"],
        "php_ini": php_ini,
        "stray_apcu": stray_configs(apcu_hits, rv["ext_paths"], rv["apc_filename"]),
        "stray_opcache": stray_configs(opcache_hits, rv["ext_paths"], rv["opcache_filename"]),
        "apc_installs": apc_installs,
        "apc_removals": apc_removals,
        "opcache_installs": opcache_installs,
        "opcache_removals": opcache_removals,
        # The role creates the pool dir only when the stat found nothing
        # (``islnk is not defined`` == absent); an existing dir -- even a
        # symlink -- is left untouched, so the request is omitted when present.
        "pool_dir": None if pool_dir_exists else pool_dir,
        "pool_user": rv["pool_user"],
        "pool_group": rv["pool_group"],
        "pools": pools,
        "fpm_service": fpm_service,
    }


def handler_plan(settings: dict[str, Any], distribution: dict[str, Any]) -> dict[str, Any]:
    """Deploy 4 input: the per-host service names and the FPM handler verb."""
    rv = resolved_vars(settings, distribution)
    return {
        "webserver_daemon": rv["webserver_daemon"],
        "fpm_daemon": rv["fpm_daemon"],
        "fpm_handler_action": FPM_HANDLER_ACTIONS[settings["php_fpm_handler_state"]],
    }


# --- deploys -----------------------------------------------------------------


def build_install(plans: dict[str, Any]) -> Callable[[Host], Generator[OperationRequest]]:
    """``tasks/setup-Debian.yml``: the apt cache refresh and the PHP packages.

    ``apt.update(cache_valid_time=86400)`` is stamp-gated (the ``AptUpdateStamp``
    fact), so a converged host within the 24h window skips it -- Ansible's
    ``cache_valid_time``. Neither task's name notifies except the package
    install, which queues ``restart webserver``.
    """

    def install(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return
        yield op(
            apt.update,
            name="Update apt cache",
            cache_valid_time=CACHE_VALID_TIME,
            sudo=True,
            when=is_ubuntu,
        )
        yield op(
            apt.packages,
            name="Ensure PHP packages are installed",
            packages=plan["packages"],
            present=plan["present"],
            latest=plan["latest"],
            no_recommends=plan["no_recommends"],
            sudo=True,
            when=is_ubuntu,
        )

    return install


def build_packaged_cleanup(
    plans: dict[str, Any], package_changed: frozenset[str]
) -> Callable[[Host], Generator[OperationRequest]]:
    """``setup-Debian.yml``'s conditional packaged-ini deletion.

    The ``register: php_package_install`` + ``when: ...changed`` recipe: the
    frozen ``changed_hosts`` set gates the whole deploy, so only hosts whose
    package install changed delete the packaged APCu/OpCache ini files. The
    packaged files are symlinks on Ubuntu, so ``force=True`` lets the ``File``
    fact's non-file reading remove the link itself.
    """
    changed = frozenset(package_changed)

    def cleanup(host: Host) -> Generator[OperationRequest]:
        if host.name not in changed:
            return
        plan = plans.get(host.name)
        if plan is None:
            return
        for path in plan["apc_removals"]:
            yield op(
                files.file,
                name="Delete APCu configuration file if this role will provide one",
                path=path,
                present=False,
                force=True,
                sudo=True,
                when=is_ubuntu,
            )
        for path in plan["opcache_removals"]:
            yield op(
                files.file,
                name="Delete OpCache configuration file if this role will provide one",
                path=path,
                present=False,
                force=True,
                sudo=True,
                when=is_ubuntu,
            )

    return cleanup


def build_configure(plans: dict[str, Any]) -> Callable[[Host], Generator[OperationRequest]]:
    """``configure.yml`` + ``configure-apcu/opcache/fpm.yml``.

    Every mid-play read was frozen into the plan control-side, so this deploy
    is a pure function of static input. Each notifying request carries a stable
    ``name=`` drawn from ``WEBSERVER_NOTIFY_NAMES`` / ``FPM_NOTIFY_NAMES``.
    """

    def configure(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return
        # configure.yml: the conf + extension directories (mode 0755). The
        # role's follow:true is inert on stock Ubuntu's real directories;
        # files.directory has no follow parameter (a symlinked conf path would
        # fail fast rather than follow -- recorded deviation).
        for path in plan["directories"]:
            yield op(
                files.directory,
                name="Ensure configuration directories exist",
                path=path,
                mode="0755",
                sudo=True,
                when=is_ubuntu,
            )
        # configure.yml: the managed php.ini in every conf path.
        for path, content in plan["php_ini"]:
            yield op(
                files.install,
                name="Place PHP configuration file in place",
                content=content,
                dest=path,
                mode="0644",
                user="root",
                group="root",
                sudo=True,
                when=is_ubuntu,
            )
        # configure-apcu.yml / configure-opcache.yml: remove the stray configs
        # the FindInFiles sweep found (basename-filtered, shallow, non-hidden).
        for path in plan["stray_apcu"]:
            yield op(
                files.file,
                name="Remove any non-role-supplied APCu config files",
                path=path,
                present=False,
                force=True,
                sudo=True,
                when=is_ubuntu,
            )
        for path in plan["stray_opcache"]:
            yield op(
                files.file,
                name="Remove any non-role-supplied OpCache config files",
                path=path,
                present=False,
                force=True,
                sudo=True,
                when=is_ubuntu,
            )
        # The role's own APCu ini, or its removal when APC is disabled.
        for path, content in plan["apc_installs"]:
            yield op(
                files.install,
                name="Ensure APCu config file is present",
                content=content,
                dest=path,
                mode="0644",
                user="root",
                group="root",
                sudo=True,
                when=is_ubuntu,
            )
        for path in plan["apc_removals"]:
            yield op(
                files.file,
                name="Remove APCu config file if APC is disabled",
                path=path,
                present=False,
                force=True,
                sudo=True,
                when=is_ubuntu,
            )
        # The role's own OpCache ini, or its removal when OpCache is disabled.
        for path, content in plan["opcache_installs"]:
            yield op(
                files.install,
                name="Ensure OpCache config file is present",
                content=content,
                dest=path,
                mode="0644",
                user="root",
                group="root",
                sudo=True,
                when=is_ubuntu,
            )
        for path in plan["opcache_removals"]:
            yield op(
                files.file,
                name="Remove OpCache config file if OpCache is disabled",
                path=path,
                present=False,
                force=True,
                sudo=True,
                when=is_ubuntu,
            )
        # configure-fpm.yml: the pool directory, only when it was absent.
        if plan["pool_dir"] is not None:
            yield op(
                files.directory,
                name="Ensure the default pool directory exists",
                path=plan["pool_dir"],
                mode="0755",
                user="root",
                group="root",
                sudo=True,
                when=is_ubuntu,
            )
        # configure-fpm.yml: the FPM pools (notify restart php-fpm).
        for path, content in plan["pools"]:
            yield op(
                files.install,
                name="Create fpm pools",
                content=content,
                dest=path,
                mode="0644",
                user="root",
                group="root",
                sudo=True,
                when=is_ubuntu,
            )
        # configure-fpm.yml: the FPM service state (no notify). restarted /
        # reloaded are stateless (always changed), faithful to Ansible's
        # service module; the default started is fact-gated and converges.
        service = plan["fpm_service"]
        if service is not None:
            yield op(
                systemd.service,
                name="Ensure php-fpm is started and enabled at boot (if configured)",
                service=service["service"],
                running=service["running"],
                restarted=service["restarted"],
                reloaded=service["reloaded"],
                enabled=service["enabled"],
                sudo=True,
                when=is_ubuntu,
            )

    return configure


def build_handler(
    plans: dict[str, Any], webserver_hosts: frozenset[str], fpm_hosts: frozenset[str]
) -> Callable[[Host], Generator[OperationRequest]]:
    """``handlers/main.yml``: ``restart webserver`` then chained ``restart php-fpm``.

    The chain collapses to an upstream-first service listing (the documented
    handler-chaining recipe): every notified host restarts its webserver daemon
    first, then its FPM daemon. The caller has already applied the handlers'
    ``when`` guards -- ``php_enable_webserver`` for the webserver set, and
    ``php_enable_php_fpm and php_fpm_state == 'started'`` for the FPM set (which
    unions the direct pool notifications with the webserver chain). An empty
    set yields an empty deploy -- the converged no-op Ansible's handler gives.
    """
    deploys = []
    for name in sorted(frozenset(webserver_hosts)):
        deploys.append(
            service_handler(
                (name,), plans[name]["webserver_daemon"], action="restarted", when=is_ubuntu
            )
        )
    for name in sorted(frozenset(fpm_hosts)):
        deploys.append(
            service_handler(
                (name,),
                plans[name]["fpm_daemon"],
                action=plans[name]["fpm_handler_action"],
                when=is_ubuntu,
            )
        )
    return compose(*deploys)


async def execute(inventory: list[dict[str, Any]], deploy: Any, title: str) -> Any:
    """Dry-run then really run one deploy, printing both renderings.

    The dry run is Ansible's ``--check`` as a return value: it stops after
    prepare, so the plan shows what *would* change without touching a host.
    Both runs use the very same deploy object -- for a factory-built deploy
    that means the same frozen snapshot, which is what makes the determinism
    guard hold.
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
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        hosts = [Host.from_inventory(entry) for entry in inventory]
        targets = [host for host in hosts if is_ubuntu(host)]
        skipped = [host.name for host in hosts if not is_ubuntu(host)]
        if skipped:
            print("\n== Skipped (when guard is_ubuntu): " + ", ".join(skipped) + " ==")
        if not targets:
            print("\nNo host in the Ubuntu family; nothing to deploy.")
            return

        # The role's variable resolution and its implicit gates, before any
        # host is contacted.
        settings = {host.name: role_settings(host) for host in targets}

        # The distribution major version selects the PHP version table, which
        # fixes the package list and paths -- so it is gathered BEFORE deploy 1.
        # gather_fact does not close its hosts, so the finally does.
        try:
            distributions = await gather_fact(targets, LinuxDistribution)
        finally:
            for host in targets:
                await host.aclose()
        print("\n== Resolved PHP versions ==")
        for host in targets:
            rv = resolved_vars(settings[host.name], distributions[host.name])
            print(f"{host.name}: {rv['version']} ({distributions[host.name].get('name')})")

        # Deploy 1: the apt cache refresh and the PHP packages.
        install_plans = {
            host.name: install_plan(settings[host.name], distributions[host.name])
            for host in targets
        }
        install_report = await execute(
            inventory, build_install(install_plans), "Deploy 1: install PHP"
        )

        # register: php_package_install -> when: ...changed. The frozen outcome
        # gates the packaged-ini cleanup deploy.
        package_changed = changed_hosts(install_report, ("Ensure PHP packages are installed",))
        cleanup_plans = {
            host.name: packaged_cleanup_plan(settings[host.name], distributions[host.name])
            for host in targets
        }
        cleanup_report = await execute(
            inventory,
            build_packaged_cleanup(cleanup_plans, package_changed),
            "Deploy 2: remove packaged extension ini",
        )

        # The stray-config sweeps and the pool-directory stat, AFTER the
        # cleanup apply (the role's find/stat run after setup-Debian). One
        # FindInFiles per extension conf path per pattern; one FileExists for
        # the pool directory. gather_fact does not close its hosts.
        apcu_hits: dict[str, tuple[str, ...]] = {}
        opcache_hits: dict[str, tuple[str, ...]] = {}
        pool_exists: dict[str, bool] = {}
        try:
            for host in targets:
                rv = resolved_vars(settings[host.name], distributions[host.name])
                apcu: list[str] = []
                opcache: list[str] = []
                for ext in rv["ext_paths"]:
                    apcu += (await gather_fact([host], FindInFiles, ext, APCU_PATTERN))[host.name]
                    opcache += (await gather_fact([host], FindInFiles, ext, OPCACHE_PATTERN))[
                        host.name
                    ]
                apcu_hits[host.name] = tuple(apcu)
                opcache_hits[host.name] = tuple(opcache)
                pool_dir = posixpath.dirname(rv["pool_conf_path"])
                pool_exists[host.name] = bool(
                    (await gather_fact([host], FileExists, pool_dir))[host.name]
                )
        finally:
            for host in targets:
                await host.aclose()

        # Freeze the configure plan and run deploy 3.
        configure_plans = {
            host.name: configure_plan(
                settings[host.name],
                distributions[host.name],
                apcu_hits[host.name],
                opcache_hits[host.name],
                pool_exists[host.name],
            )
            for host in targets
        }
        configure_report = await execute(
            inventory, build_configure(configure_plans), "Deploy 3: configure PHP"
        )

        # The handlers. restart webserver fires when any notifying task changed
        # (across all three deploys) and php_enable_webserver; the chained
        # restart php-fpm fires from that chain plus the direct pool
        # notifications, gated by php_enable_php_fpm and php_fpm_state=='started'.
        webserver_notified = (
            changed_hosts(install_report, WEBSERVER_NOTIFY_NAMES)
            | changed_hosts(cleanup_report, WEBSERVER_NOTIFY_NAMES)
            | changed_hosts(configure_report, WEBSERVER_NOTIFY_NAMES)
        )
        webserver_hosts = frozenset(
            name for name in webserver_notified if settings[name]["php_enable_webserver"]
        )
        fpm_direct = changed_hosts(configure_report, FPM_NOTIFY_NAMES)
        fpm_hosts = frozenset(
            name
            for name in (webserver_hosts | fpm_direct)
            if settings[name]["php_enable_php_fpm"] and settings[name]["php_fpm_state"] == "started"
        )
        print("\n== Handler notification sets ==")
        print(f"restart webserver: {sorted(webserver_hosts) or '(none)'}")
        print(f"restart php-fpm:   {sorted(fpm_hosts) or '(none)'}")
        handler_plans = {
            host.name: handler_plan(settings[host.name], distributions[host.name])
            for host in targets
        }
        handler_report = await execute(
            inventory,
            build_handler(handler_plans, webserver_hosts, fpm_hosts),
            "Deploy 4: restart handlers",
        )

        changed = sum(
            1
            for report in (install_report, cleanup_report, configure_report, handler_report)
            for entry in report.log
            if entry.changed
        )
        print(f"\n== Done: {changed} changed request(s) across the four deploys ==")
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        # The inventory is missing/unreadable/not JSON, or a role variable is
        # invalid. No host mutation is implied by the variable gates, so the
        # message is the whole report.
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