# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The PHP role (migrated from ``geerlingguy.php``).

Installs and configures PHP on Ubuntu hosts: the apt cache refresh and the
version-specific PHP packages, the packaged APCu/OpCache ini cleanup, the
configuration directories and a rendered ``php.ini`` in every conf path, the
stray-extension-config sweep and the role's own ini files, the optional PHP-FPM
pools and service, and the role's two chained handlers (``restart webserver`` ->
``restart php-fpm``). :func:`php` is the whole task list and :class:`PhpVars` is
``defaults/main.yml``, bound from the inventory's ``php_``-prefixed keys by
:func:`~reemote.rolevars.resolve_vars`.

Target: hosts in the ``"Ubuntu"`` OS family. The role's RHEL/CentOS branch and
the opt-in ``php_install_from_source`` path are out of scope, so its
``include_vars`` OS dispatch collapses to the frozen Ubuntu version table below.

The stream carries one wave per barrier group (design doc section 5.7): twelve
waves for a fresh FPM-enabled Ubuntu host, eleven once it converges -- the
handler wave is the only thing that disappears.

0. ``fact(LinuxDistribution)`` -- the major version selects the PHP version,
   which fixes the package list and every managed path.
1. :func:`install` -- the apt cache refresh and the packages -- closed by
   ``checkpoint("install done")``.
2. :func:`packaged_cleanup` -- the packaged APCu/OpCache ini files, on the hosts
   whose package install changed -- closed by ``checkpoint("cleanup done")``.
3..*2m+2* one ``fact(FindInFiles, path, pattern)`` per extension conf path, the
   APCu sweep then the OpCache sweep, all six *after* the cleanup applied.
*2m+3* ``fact(FileExists, pool_dir)`` -- the pool directory. The role's ``stat``
   is guarded by nothing at all (only the pool and service tasks check
   ``php_enable_php_fpm``), because its answer decides whether the directory is
   created, so the read runs on every host in the target family.
*n*+1 :func:`configure` -- directories, ``php.ini``, the stray removals, the
   extension ini files, the pools and the FPM service -- closed by
   ``checkpoint("configure done")``.
*n*+2 :func:`handler` -- ``Restart <webserver> (handler)`` and the chained
   ``Restart <fpm> (handler)``, only on the hosts those checkpoints selected.

The checkpoint placement is the whole design of this role (design doc section
16.6), and it is the one thing that must not be tidied up: the five reads the
example took between its four ``run()`` calls become **three** checkpoints, one
directly after each stage. Folding the three stages into one wave and reading one
report would merge the notifications the three channels need to keep apart --
and three checkpoints after a single combined wave would read two empty reports,
losing every cleanup and configure notification *silently*, because a handler
that is never queued does not fail, it just never runs.

The three channels are not symmetric, so they stay three separate reads:
``package_changed`` comes from the install wave alone (it is a gate, not a
notification), ``webserver_notified`` is the **union** of all three waves, and
``fpm_direct`` comes from the configure wave alone.

No template engine is needed -- unlike :mod:`reemote.roles.apache`, this role
needs no ``reemote[templates]`` extra. Every default-path template
(``php.ini.j2``, ``apc.ini.j2``, ``opcache.ini.j2``, ``www.conf.j2``) is pure
variable substitution plus one ``join`` and two ``{% if %}`` blocks, so the
renders below are plain Python f-strings.

Deviations from the Ansible role, all deliberate:

* ``find: {paths, contains}`` maps to the :class:`~reemote.modules.files.
  FindInFiles` fact (a recursive ``grep -rlE``). Ansible's ``find`` is shallow,
  skips dotfiles and anchors ``contains`` at the line start; grep recurses,
  includes dotfiles and matches anywhere in a line. The shallowness and dotfile
  differences are recovered control-side by :func:`stray_configs`, which keeps
  only direct, non-hidden children. The broader line match is kept: it only ever
  widens the cleanup of *stray* files, and on a stock Ubuntu ``conf.d`` -- whose
  packaged entries are symlinks both searches skip -- the result is identical.
* the custom ``pool_template`` rejection is kept, not worked around: only the
  bundled ``www.conf.j2`` has a Python render, so a pool naming another template
  raises instead of silently rendering the bundled one (§16.6).
* the chained handler collapses to an upstream-first service listing (the
  documented handler-chaining recipe): the webserver request is yielded before
  the FPM request on the same host, which is all the chain guarantees. Neither
  the example's ``compose`` nor its whole-inventory name lists survive -- a
  per-host body needs neither.
* the pool-directory read is :class:`~reemote.modules.files.FileExists`
  (``test -e``) rather than the role's ``stat``: a *dangling symlink* at the path
  reads absent here, where ``stat`` reports ``islnk`` defined and the role skips
  creation. On a stock Ubuntu the path is a real directory or absent, so the edge
  is unreachable in practice.
* every request carried ``when=is_ubuntu`` in the example; here the family guard
  returns before the first barrier, so a host outside the target family is never
  contacted and no request needs a guard.
* ``files.directory`` owns no ``follow`` parameter, so the role's ``follow:
  true`` is dropped -- inert on stock Ubuntu, where every conf path is a real
  directory, and a symlinked one fails fast rather than being followed.
* the packaged ``conf.d`` ini files are symlinks, so removals use
  ``files.file(present=False, force=True)``: the ``File`` fact reads a symlink as
  a non-file, and ``force`` is what lets the removal touch it.
* ``php_enablerepo`` (RHEL-only) and ``php_executable`` (source path) are carried
  as variables and do nothing here, documented as inert rather than silently
  omitted, so an existing inventory still binds.
* the role's variable validation becomes pydantic validation: the three enums are
  :data:`~typing.Literal` fields, the bool gates are ``bool`` fields (Jinja's
  truthiness is what pydantic's lax coercion implements), a pool needs
  ``pool_name`` and ``pool_listen``, and a bad value raises ``ValidationError``
  -- a ``ValueError``, so the runner maps it -- before any host is contacted.
"""

import posixpath
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator

from ..core import op
from ..handlers import changed_hosts, service_handler
from ..hosts import Host
from ..modules import apt, files, systemd
from ..modules.files import FileExists, FindInFiles
from ..modules.server import LinuxDistribution
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, checkpoint, fact

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path only) ---

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

#: The only pool template with a render in this module; anything else in a
#: ``php_fpm_pools`` item is rejected (see the deviation list above).
BUNDLED_POOL_TEMPLATE = "www.conf.j2"

#: ``php_fpm_handler_state`` -> the ``service_handler`` action. Ansible's
#: ``stopped`` is not a handler verb here (service_handler offers only these
#: three); the role documents ``restarted``/``reloaded``.
FPM_HANDLER_ACTIONS = {"restarted": "restarted", "reloaded": "reloaded", "started": "running"}

#: ``register: php_package_install`` + ``when: php_package_install.changed``:
#: the gate on the packaged-ini cleanup, read from the **install** wave only.
#: The same task also queues ``restart webserver``, hence the alias.
PACKAGE_GATE_NAMES = frozenset({"Ensure PHP packages are installed"})
INSTALL_HANDLER_NAMES = PACKAGE_GATE_NAMES

#: The cleanup-wave tasks that queue ``restart webserver`` (handlers/main.yml).
CLEANUP_HANDLER_NAMES = frozenset(
    {
        "Delete APCu configuration file if this role will provide one",
        "Delete OpCache configuration file if this role will provide one",
    }
)

#: The configure-wave tasks that queue ``restart webserver``.
CONFIGURE_HANDLER_NAMES = frozenset(
    {
        "Place PHP configuration file in place",
        "Remove any non-role-supplied APCu config files",
        "Remove any non-role-supplied OpCache config files",
        "Ensure APCu config file is present",
        "Remove APCu config file if APC is disabled",
        "Ensure OpCache config file is present",
        "Remove OpCache config file if OpCache is disabled",
    }
)

#: The pool template task queues ``restart php-fpm`` *directly*, which is why
#: the FPM set is not simply the webserver set: a host can reach this handler
#: through the chain without notifying the webserver at all.
FPM_HANDLER_NAMES = frozenset({"Create fpm pools"})

#: Every task that queues ``restart webserver`` -- the set each of the three
#: wave reports is selected against, so that the three answers union (§16.6).
WEBSERVER_HANDLER_NAMES = INSTALL_HANDLER_NAMES | CLEANUP_HANDLER_NAMES | CONFIGURE_HANDLER_NAMES


# --- variables ---------------------------------------------------------------


class PhpFpmPool(BaseModel):
    """One ``php_fpm_pools`` item: the keys ``www.conf.j2`` reads.

    :attr:`pool_name` and :attr:`pool_listen` are the template's two
    ``| mandatory`` filters, so they are required and non-empty; every other key
    is the template's ``| default(x, true)`` form, so ``None`` -- or any falsy
    value -- renders that default instead (:func:`render_www_conf`). Keeping them
    ``None`` rather than value-defaulting is what lets a pool that sets
    ``pool_pm_max_requests: 0`` render the template's 500, faithfully.

    ``extra="allow"`` keeps any further key the template might learn to read, and
    the model is frozen to match the containing :class:`PhpVars`.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    pool_name: str
    pool_listen: str
    pool_template: str = BUNDLED_POOL_TEMPLATE
    pool_listen_allowed_clients: str | int | None = None
    pool_pm: str | int | None = None
    pool_pm_max_children: str | int | None = None
    pool_pm_start_servers: str | int | None = None
    pool_pm_min_spare_servers: str | int | None = None
    pool_pm_max_spare_servers: str | int | None = None
    pool_pm_max_requests: str | int | None = None
    pool_pm_status_path: str | int | None = None

    @field_validator("pool_name", "pool_listen")
    @classmethod
    def _mandatory(cls, value: str, info: ValidationInfo) -> str:
        """``{{ item.pool_name | mandatory }}``: an empty name is a typo."""
        if not value:
            raise ValueError(f"a php_fpm_pools item needs a non-empty {info.field_name}")
        return value

    @field_validator("pool_template")
    @classmethod
    def _bundled_template_only(cls, value: str) -> str:
        """Only the bundled template has a render here; anything else is refused."""
        if value != BUNDLED_POOL_TEMPLATE:
            raise ValueError(
                f"only the bundled {BUNDLED_POOL_TEMPLATE} is supported (got {value!r}); "
                "a custom pool template needs a renderer"
            )
        return value


class PhpVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names carry no ``php_`` prefix -- the prefix passed to
    :func:`~reemote.rolevars.resolve_vars` binds ``php_memory_limit`` in the
    inventory to :attr:`memory_limit`, so an inventory written for the role needs
    no change. Every Ansible default is carried over, including the two inert
    ones (:attr:`enablerepo`, :attr:`executable`) so such a key still binds.

    The values that reach a template verbatim are ``str``, with
    ``coerce_numbers_to_str`` so an inventory writing ``php_memory_limit: 512``
    renders ``512`` exactly as Jinja would render the integer. Sequences are
    ``tuple``, never ``list``: the model is frozen, and a value that reaches
    :func:`~reemote.core.op` must hash (design doc section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", coerce_numbers_to_str=True)

    #: RHEL-only, out of scope: kept so an existing inventory binds and noops.
    enablerepo: str = ""
    #: ``command: which {{ php_executable }}`` belongs to the source path: inert.
    executable: str = "php"

    # --- setup: the packages and the version -------------------------------
    #: ``null`` falls back to the Ubuntu version table, then Debian's 8.2, as the
    #: role's ``include_vars`` + ``set_fact`` chain does.
    default_version_debian: str | None = None
    #: ``__php_packages`` (Debian) when null; the version-specific list built by
    #: :func:`resolve_php`.
    packages: tuple[str, ...] | None = None
    packages_extra: tuple[str, ...] = ()
    packages_state: Literal["present", "latest", "absent"] = "present"
    install_recommends: bool = True

    # --- webserver and PHP-FPM ---------------------------------------------
    #: The gate on the ``restart webserver`` handler, not on any request.
    enable_webserver: bool = True
    webserver_daemon: str | None = None
    enable_php_fpm: bool = False
    fpm_state: Literal["started", "stopped", "restarted", "reloaded"] = "started"
    #: The verb the FPM handler uses when a notifying request changed something.
    fpm_handler_state: Literal["restarted", "reloaded", "started"] = "restarted"
    fpm_enabled_on_boot: bool = True
    fpm_daemon: str | None = None
    fpm_conf_path: str | None = None
    fpm_pool_conf_path: str | None = None
    fpm_pool_user: str | None = None
    fpm_pool_group: str | None = None
    fpm_listen: str = "127.0.0.1:9000"
    fpm_listen_allowed_clients: str = "127.0.0.1"
    fpm_pm_max_children: int = 50
    fpm_pm_start_servers: int = 5
    fpm_pm_min_spare_servers: int = 5
    fpm_pm_max_spare_servers: int = 5
    fpm_pm_max_requests: int = 0
    fpm_pm_status_path: str = ""
    #: ``null`` resolves to the one www pool the defaults describe.
    fpm_pools: tuple[PhpFpmPool, ...] | None = None

    # --- paths -------------------------------------------------------------
    conf_paths: tuple[str, ...] | None = None
    extension_conf_paths: tuple[str, ...] | None = None
    apc_conf_filename: str | None = None
    opcache_conf_filename: str | None = None
    #: ``php_use_managed_ini``: when false, no ``php.ini`` is written at all.
    use_managed_ini: bool = True

    # --- php.ini -----------------------------------------------------------
    short_open_tag: str = "Off"
    precision: str = "14"
    output_buffering: str = "4096"
    serialize_precision: str = "-1"
    disable_functions: tuple[str, ...] = ()
    expose_php: str = "On"
    max_execution_time: str = "60"
    max_input_time: str = "60"
    max_input_vars: str = "1000"
    memory_limit: str = "256M"
    error_reporting: str = "E_ALL & ~E_DEPRECATED & ~E_STRICT"
    display_errors: str = "Off"
    display_startup_errors: str = "Off"
    post_max_size: str = "32M"
    realpath_cache_size: str = "32K"
    file_uploads: str = "On"
    upload_max_filesize: str = "64M"
    max_file_uploads: str = "20"
    allow_url_fopen: str = "On"
    date_timezone: str = "America/Chicago"
    sendmail_path: str = "/usr/sbin/sendmail -t -i"
    session_save_handler: str = "files"
    session_save_path: str = ""
    session_cookie_lifetime: str = "0"
    session_gc_probability: str = "1"
    session_gc_divisor: str = "1000"
    session_gc_maxlifetime: str = "1440"

    # --- APCu and OpCache --------------------------------------------------
    enable_apc: bool = True
    apc_shm_size: str = "96M"
    apc_enable_cli: str = "0"
    opcache_zend_extension: str = "opcache.so"
    #: Renders verbatim into ``opcache.enable=`` *and* gates tasks through
    #: ``| bool``, so the raw value's type is preserved: a declared ``bool``
    #: field would render ``True`` where Ansible renders ``1``.
    opcache_enable: str | int | bool = "1"
    opcache_enable_cli: str = "0"
    opcache_memory_consumption: str = "96"
    opcache_interned_strings_buffer: str = "16"
    opcache_max_accelerated_files: str = "4096"
    opcache_max_wasted_percentage: str = "5"
    opcache_validate_timestamps: str = "1"
    opcache_revalidate_path: str = "0"
    opcache_revalidate_freq: str = "2"
    opcache_max_file_size: str = "0"
    opcache_blacklist_filename: str = ""

    @field_validator("default_version_debian")
    @classmethod
    def _version_shape(cls, value: str | None) -> str | None:
        """``php_default_version_debian`` must be dotted digits if it is set."""
        if value is not None:
            _version_tuple(value)
        return value


@dataclass(frozen=True)
class PhpOutputs:
    """What :func:`php` publishes to a composing role (design doc sections 9, 10.4).

    ``version`` is the resolved PHP version, ``None`` on a host the family guard
    skipped -- php-xdebug keys its own behaviour off it and returns early on
    ``None`` (§18.7). ``extension_conf_paths`` is where an extension ini belongs,
    which is the §18.7 channel that deletes xdebug's own path guess.
    ``webserver_notified`` is the **union** of the three wave sets -- the relay
    value apache consumes (§16.8), never just the last checkpoint's set. A return
    value, not a registry.
    """

    version: str | None
    extension_conf_paths: tuple[str, ...] = ()
    webserver_notified: frozenset[str] = frozenset()


# --- frozen per-host resolution and plans ------------------------------------


@dataclass(frozen=True)
class PhpResolution:
    """The ``vars/Debian.yml`` ``__php_*`` values, resolved for one host.

    Built once, right after the distribution barrier, by :func:`resolve_php`.
    Every path, filename and daemon name the four stages manage to derive lives
    here, so no renderer or deploy ever re-derives one.
    """

    version: str
    distribution_name: str
    packages: tuple[str, ...]
    conf_paths: tuple[str, ...]
    ext_paths: tuple[str, ...]
    apc_filename: str
    opcache_filename: str
    webserver_daemon: str
    fpm_daemon: str
    fpm_conf_path: str
    pool_conf_path: str
    pool_user: str
    pool_group: str


@dataclass(frozen=True)
class PhpInstallPlan:
    """The install stage's frozen input: the package list and its apt flags."""

    packages: tuple[str, ...]
    present: bool
    latest: bool
    no_recommends: bool


@dataclass(frozen=True)
class PhpCleanupPlan:
    """The cleanup stage's frozen input: the packaged extension ini to delete."""

    apc_removals: tuple[str, ...]
    opcache_removals: tuple[str, ...]


@dataclass(frozen=True)
class PhpFpmService:
    """The FPM service task's frozen verbs, absent when the task is guarded off."""

    service: str
    running: bool
    restarted: bool
    reloaded: bool
    enabled: bool


@dataclass(frozen=True)
class PhpConfigurePlan:
    """One host's resolved configure targets, built by :func:`configure_plan`.

    It replaces the example's ``configure_plans`` dict keyed by host name: under
    a per-host body the plan is a local. Every value is decided *before* the first
    request of the configure wave is built -- the sweeps were filtered, the
    templates rendered -- so the deploy is a pure function of this one object and
    both determinism passes see the same sequence.
    """

    directories: tuple[str, ...]
    php_ini: tuple[tuple[str, str], ...]
    stray_apcu: tuple[str, ...]
    stray_opcache: tuple[str, ...]
    apc_installs: tuple[tuple[str, str], ...]
    apc_removals: tuple[str, ...]
    opcache_installs: tuple[tuple[str, str], ...]
    opcache_removals: tuple[str, ...]
    #: ``None`` when the pool directory already exists: the role creates it only
    #: when the stat found nothing, and an existing directory -- even a symlink
    #: -- is left untouched.
    pool_dir: str | None
    pools: tuple[tuple[str, str], ...]
    fpm_service: PhpFpmService | None


# --- pure helpers ------------------------------------------------------------


def _version_tuple(version: str) -> tuple[int, ...]:
    """The role's ``version is match('^[0-9]+\\.[0-9]+$')`` shape check."""
    parts = version.split(".")
    if not parts or not all(part.isdigit() for part in parts):
        raise ValueError(f"php version must be dotted digits, got {version!r}")
    return tuple(int(part) for part in parts)


def _ansible_bool(value: Any, key: str) -> bool:
    """Ansible's ``| bool`` filter: YAML 1.1 booleans, case-insensitive.

    Only one variable needs it: :attr:`PhpVars.opcache_enable`, which renders
    verbatim and therefore keeps its raw type. Every other gate is a ``bool``
    field, where pydantic's lax coercion *is* Jinja's truthiness.
    """
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


def _jinja_default(value: Any, default: Any) -> Any:
    """Jinja2's ``default(x, true)``: the default when *value* is falsy.

    www.conf.j2 uses the boolean form, so the role's default
    ``php_fpm_pm_max_requests: 0`` renders as 500 (0 is falsy), faithfully.
    """
    return value if value else default


def php_version(v: PhpVars, distribution: dict[str, Any]) -> str:
    """The role's ``include_vars`` chain as one lookup.

    The user's ``php_default_version_debian`` wins, else the Ubuntu major-version
    table (20 -> 7.4, 22 -> 8.1, 24 -> 8.3), else ``vars/Debian.yml``'s 8.2.
    """
    version = v.default_version_debian or UBUNTU_PHP_VERSIONS.get(
        distribution.get("major"), DEBIAN_DEFAULT_PHP_VERSION
    )
    _version_tuple(version)
    return version


def resolve_php(v: PhpVars, distribution: dict[str, Any]) -> PhpResolution:
    """Resolve :attr:`PhpVars` and the gathered distribution into one frozen set.

    Every ``__php_*`` fallback the role's ``set_fact`` tasks apply is reproduced
    here, and the PHP < 8.0 JSON package is appended exactly as ``main.yml`` does.
    """
    version = php_version(v, distribution)
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
    packages = v.packages if v.packages is not None else tuple(base)
    packages = tuple(packages) + v.packages_extra
    default_conf = (
        f"/etc/php/{version}/fpm",
        f"/etc/php/{version}/apache2",
        f"/etc/php/{version}/cli",
    )
    conf_paths = v.conf_paths or default_conf
    ext_paths = v.extension_conf_paths or tuple(f"{path}/conf.d" for path in default_conf)
    fpm_conf_path = v.fpm_conf_path or f"/etc/php/{version}/fpm"
    return PhpResolution(
        version=version,
        distribution_name=distribution.get("name") or "",
        packages=packages,
        conf_paths=tuple(conf_paths),
        ext_paths=tuple(ext_paths),
        apc_filename=v.apc_conf_filename or "20-apcu.ini",
        opcache_filename=v.opcache_conf_filename or "10-opcache.ini",
        webserver_daemon=v.webserver_daemon or "apache2",
        fpm_daemon=v.fpm_daemon or f"php{version}-fpm",
        fpm_conf_path=fpm_conf_path,
        pool_conf_path=v.fpm_pool_conf_path or f"{fpm_conf_path}/pool.d/www.conf",
        pool_user=v.fpm_pool_user or "www-data",
        pool_group=v.fpm_pool_group or "www-data",
    )


def default_pools(v: PhpVars) -> tuple[PhpFpmPool, ...]:
    """The ``defaults/main.yml`` www pool, its ``{{ php_fpm_* }}`` refs resolved."""
    return (
        PhpFpmPool(
            pool_name="www",
            pool_template=BUNDLED_POOL_TEMPLATE,
            pool_listen=v.fpm_listen,
            pool_listen_allowed_clients=v.fpm_listen_allowed_clients,
            pool_pm="dynamic",
            pool_pm_max_children=v.fpm_pm_max_children,
            pool_pm_start_servers=v.fpm_pm_start_servers,
            pool_pm_min_spare_servers=v.fpm_pm_min_spare_servers,
            pool_pm_max_spare_servers=v.fpm_pm_max_spare_servers,
            pool_pm_max_requests=v.fpm_pm_max_requests,
            pool_pm_status_path=v.fpm_pm_status_path,
        ),
    )


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


def opcache_enabled(v: PhpVars) -> bool:
    """``php_opcache_enable | bool`` -- the gate the raw render does not carry."""
    return _ansible_bool(v.opcache_enable, "php_opcache_enable")


# --- control-side template renders (pure Python, no Jinja2) ------------------


def render_php_ini(v: PhpVars) -> str:
    """``templates/php.ini.j2`` -- pure substitution plus one ``join(",")``.

    Reproduced verbatim (Ansible's ``trim_blocks`` default has no effect: the
    template has no block tags). :attr:`PhpVars.disable_functions` is the only
    filter.
    """
    disable_functions = ",".join(v.disable_functions)
    return f"""[PHP]

;;;;;;;;;;;;;;;;;;;;
; Language Options ;
;;;;;;;;;;;;;;;;;;;;

engine = On
short_open_tag = {v.short_open_tag}
precision = {v.precision}
output_buffering = {v.output_buffering}

zlib.output_compression = Off

implicit_flush = Off
unserialize_callback_func =
serialize_precision = {v.serialize_precision}
disable_functions = {disable_functions}
disable_classes =

zend.enable_gc = On

;;;;;;;;;;;;;;;;;
; Miscellaneous ;
;;;;;;;;;;;;;;;;;

expose_php = {v.expose_php}

;;;;;;;;;;;;;;;;;;;
; Resource Limits ;
;;;;;;;;;;;;;;;;;;;

max_execution_time = {v.max_execution_time}
max_input_time = {v.max_input_time}
max_input_vars = {v.max_input_vars}
memory_limit = {v.memory_limit}

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
; Error handling and logging ;
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

error_reporting = {v.error_reporting}
display_errors = {v.display_errors}
display_startup_errors = {v.display_startup_errors}
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

post_max_size = {v.post_max_size}
auto_prepend_file =
auto_append_file =

default_mimetype = "text/html"

;;;;;;;;;;;;;;;;;;;;;;;;;
; Paths and Directories ;
;;;;;;;;;;;;;;;;;;;;;;;;;

doc_root =
user_dir =

enable_dl = Off

realpath_cache_size = {v.realpath_cache_size}

;;;;;;;;;;;;;;;;
; File Uploads ;
;;;;;;;;;;;;;;;;

file_uploads = {v.file_uploads}
upload_max_filesize = {v.upload_max_filesize}
max_file_uploads = {v.max_file_uploads}

;;;;;;;;;;;;;;;;;;
; Fopen wrappers ;
;;;;;;;;;;;;;;;;;;

allow_url_fopen = {v.allow_url_fopen}
allow_url_include = Off

default_socket_timeout = 60

;;;;;;;;;;;;;;;;;;;
; Module Settings ;
;;;;;;;;;;;;;;;;;;;

[CLI Server]
cli_server.color = On

[Date]
date.timezone = {v.date_timezone}

[Pdo_mysql]
pdo_mysql.cache_size = 2000
pdo_mysql.default_socket=

[mail function]
; For Win32 only.
SMTP = localhost
smtp_port = 25

; For Unix only.  You may supply arguments as well (default: "sendmail -t -i").
sendmail_path = {v.sendmail_path}

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
session.save_handler = {v.session_save_handler}
session.save_path = {v.session_save_path}
session.use_cookies = 1
session.use_only_cookies = 1
session.name = PHPSESSID
session.auto_start = 0

session.cookie_lifetime = {v.session_cookie_lifetime}
session.cookie_path = /
session.cookie_domain =
session.cookie_httponly =

session.serialize_handler = php

session.gc_probability = {v.session_gc_probability}
session.gc_divisor = {v.session_gc_divisor}
session.gc_maxlifetime = {v.session_gc_maxlifetime}

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


def render_apc_ini(v: PhpVars) -> str:
    """``templates/apc.ini.j2`` -- pure substitution."""
    return (
        "extension=apcu.so\n"
        f"apc.shm_size={v.apc_shm_size}\n"
        f"apc.enable_cli={v.apc_enable_cli}\n"
        "apc.rfc1867=1\n"
    )


def render_opcache_ini(v: PhpVars) -> str:
    """``templates/opcache.ini.j2`` -- substitution plus one ``{% if %}``.

    The blacklist line appears only when ``php_opcache_blacklist_filename`` is
    non-empty (Ansible's ``trim_blocks=True`` drops the block-tag newlines).
    """
    content = (
        f"zend_extension={v.opcache_zend_extension}\n"
        f"opcache.enable={v.opcache_enable}\n"
        f"opcache.enable_cli={v.opcache_enable_cli}\n"
        f"opcache.memory_consumption={v.opcache_memory_consumption}\n"
        f"opcache.interned_strings_buffer={v.opcache_interned_strings_buffer}\n"
        f"opcache.max_accelerated_files={v.opcache_max_accelerated_files}\n"
        f"opcache.max_wasted_percentage={v.opcache_max_wasted_percentage}\n"
        f"opcache.validate_timestamps={v.opcache_validate_timestamps}\n"
        f"opcache.revalidate_path={v.opcache_revalidate_path}\n"
        f"opcache.revalidate_freq={v.opcache_revalidate_freq}\n"
        f"opcache.max_file_size={v.opcache_max_file_size}\n"
    )
    if v.opcache_blacklist_filename != "":
        content += f"opcache.blacklist_filename={v.opcache_blacklist_filename}\n"
    return content


def render_www_conf(pool: PhpFpmPool, rv: PhpResolution) -> str:
    """``templates/www.conf.j2`` -- the bundled FPM pool template.

    ``pool_name``/``pool_listen`` are mandatory (validated on the model); the
    rest use Jinja2's falsy-default form. ``ansible_managed`` is frozen to its
    ansible.cfg default and rendered with the ``; `` comment decoration. The
    pool user and group are the role's ``__php_fpm_pool_*`` values, not the
    pool's own: only :attr:`PhpVars.fpm_pool_user` sets them.
    """
    status_path = pool.pool_pm_status_path or ""
    content = (
        f"; {ANSIBLE_MANAGED}\n"
        "\n"
        f"[{pool.pool_name}]\n"
        f"listen = {pool.pool_listen}\n"
        "listen.allowed_clients = "
        f"{_jinja_default(pool.pool_listen_allowed_clients, '127.0.0.1')}\n"
        f"user = {rv.pool_user}\n"
        f"group = {rv.pool_group}\n"
        "\n"
        f"listen.owner = {rv.pool_user}\n"
        f"listen.group = {rv.pool_group}\n"
        "\n"
        f"pm = {_jinja_default(pool.pool_pm, 'dynamic')}\n"
        f"pm.max_children = {_jinja_default(pool.pool_pm_max_children, 50)}\n"
        f"pm.start_servers = {_jinja_default(pool.pool_pm_start_servers, 5)}\n"
        f"pm.min_spare_servers = {_jinja_default(pool.pool_pm_min_spare_servers, 5)}\n"
        f"pm.max_spare_servers = {_jinja_default(pool.pool_pm_max_spare_servers, 5)}\n"
        f"pm.max_requests = {_jinja_default(pool.pool_pm_max_requests, 500)}\n"
    )
    if len(str(status_path)):
        content += f"pm.status_path = {status_path}\n"
    return content


# --- frozen per-host plans ---------------------------------------------------


def install_plan(v: PhpVars, rv: PhpResolution) -> PhpInstallPlan:
    """The install stage's input: the package list and its apt flags."""
    state = v.packages_state
    return PhpInstallPlan(
        packages=rv.packages,
        present=state != "absent",
        latest=state == "latest",
        no_recommends=not v.install_recommends,
    )


def packaged_cleanup_plan(v: PhpVars, rv: PhpResolution) -> PhpCleanupPlan:
    """The cleanup stage's input: the packaged extension ini to delete on install.

    ``setup-Debian.yml`` deletes these only when the package install changed
    *and* the extension is enabled -- the changed gate is applied by the deploy
    (a frozen ``changed_hosts`` set), the enable gate here.
    """
    apc = (
        tuple(f"{ext}/{rv.apc_filename}" for ext in rv.ext_paths) if v.enable_apc else ()
    )
    opcache = (
        tuple(f"{ext}/{rv.opcache_filename}" for ext in rv.ext_paths)
        if opcache_enabled(v)
        else ()
    )
    return PhpCleanupPlan(apc_removals=apc, opcache_removals=opcache)


def configure_plan(
    v: PhpVars,
    rv: PhpResolution,
    apcu_hits: tuple[str, ...],
    opcache_hits: tuple[str, ...],
    pool_dir_exists: bool,
) -> PhpConfigurePlan:
    """The configure stage's input: every read frozen, every template rendered.

    The stray-config sweeps (``FindInFiles``) and the pool-directory existence
    (``FileExists``) arrived as barriers of their own waves, so the result is a
    pure function of static input plus those answers: the deploy is branch-free
    with respect to run outcomes, which is what makes both passes agree.
    """
    php_ini_content = render_php_ini(v)
    php_ini = (
        tuple((f"{path}/php.ini", php_ini_content) for path in rv.conf_paths)
        if v.use_managed_ini
        else ()
    )
    apc_content = render_apc_ini(v)
    opcache_content = render_opcache_ini(v)
    if v.enable_apc:
        apc_installs = tuple((f"{ext}/{rv.apc_filename}", apc_content) for ext in rv.ext_paths)
        apc_removals: tuple[str, ...] = ()
    else:
        apc_installs = ()
        apc_removals = tuple(f"{ext}/{rv.apc_filename}" for ext in rv.ext_paths)
    if opcache_enabled(v):
        opcache_installs = tuple(
            (f"{ext}/{rv.opcache_filename}", opcache_content) for ext in rv.ext_paths
        )
        opcache_removals: tuple[str, ...] = ()
    else:
        opcache_installs = ()
        opcache_removals = tuple(f"{ext}/{rv.opcache_filename}" for ext in rv.ext_paths)
    pool_dir = posixpath.dirname(rv.pool_conf_path)
    pools = (
        tuple(
            (f"{pool_dir}/{pool.pool_name}.conf", render_www_conf(pool, rv))
            for pool in (v.fpm_pools or default_pools(v))
        )
        if v.enable_php_fpm
        else ()
    )
    fpm_service = None
    # The role's service task is guarded by ``distribution != "Debian"``; on
    # the Ubuntu target that is always true, but the gathered name keeps it
    # faithful for a Debian host that slips into the group.
    if v.enable_php_fpm and rv.distribution_name != "Debian":
        state = v.fpm_state
        fpm_service = PhpFpmService(
            service=rv.fpm_daemon,
            running=state != "stopped",
            restarted=state == "restarted",
            reloaded=state == "reloaded",
            enabled=v.fpm_enabled_on_boot,
        )
    return PhpConfigurePlan(
        directories=rv.conf_paths + rv.ext_paths,
        php_ini=php_ini,
        stray_apcu=stray_configs(apcu_hits, rv.ext_paths, rv.apc_filename),
        stray_opcache=stray_configs(opcache_hits, rv.ext_paths, rv.opcache_filename),
        apc_installs=apc_installs,
        apc_removals=apc_removals,
        opcache_installs=opcache_installs,
        opcache_removals=opcache_removals,
        # The role creates the pool dir only when the stat found nothing
        # (``islnk is not defined`` == absent); an existing dir -- even a
        # symlink -- is left untouched, so the request is omitted when present.
        pool_dir=None if pool_dir_exists else pool_dir,
        pools=pools,
        fpm_service=fpm_service,
    )


# --- helper deploys ----------------------------------------------------------


def install(plan: PhpInstallPlan) -> Deploy:
    """``tasks/setup-Debian.yml``: the apt cache refresh and the PHP packages.

    Branch-free with respect to facts: the package list and the present/latest
    flags come from the resolved variables. The cache refresh is
    ``apt.update(cache_valid_time=86400)`` -- idempotent within the window via
    the ``AptUpdateStamp`` fact, so a converged re-run reports no change
    (Ansible's ``update_cache=yes cache_valid_time=86400``). Only the package
    install notifies, and it is also the gate the cleanup stage reads.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            apt.update,
            name="Update apt cache",
            cache_valid_time=CACHE_VALID_TIME,
            sudo=True,
        )
        yield op(
            apt.packages,
            name="Ensure PHP packages are installed",
            packages=plan.packages,
            present=plan.present,
            latest=plan.latest,
            no_recommends=plan.no_recommends,
            sudo=True,
        )

    return deploy


def packaged_cleanup(plan: PhpCleanupPlan, package_changed: frozenset[str]) -> Deploy:
    """``setup-Debian.yml``'s conditional packaged-ini deletion.

    ``register: php_package_install`` + ``when: ...changed`` as a per-host body:
    the frozen set names the hosts whose install changed, and
    :func:`~reemote.handlers.changed_hosts` built it from the report of the wave
    the *install* checkpoint closed. A host not in it yields nothing at all, so
    the converged second run has an empty cleanup wave -- the no-op Ansible's
    ``when`` gives. The packaged files are symlinks on Ubuntu, so ``force=True``
    lets the ``File`` fact's non-file reading remove the link itself.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if host.name not in package_changed:
            return
        for path in plan.apc_removals:
            yield op(
                files.file,
                name="Delete APCu configuration file if this role will provide one",
                path=path,
                present=False,
                force=True,
                sudo=True,
            )
        for path in plan.opcache_removals:
            yield op(
                files.file,
                name="Delete OpCache configuration file if this role will provide one",
                path=path,
                present=False,
                force=True,
                sudo=True,
            )

    return deploy


def configure(plan: PhpConfigurePlan) -> Deploy:
    """``configure.yml`` plus ``configure-apcu/opcache/fpm.yml``.

    Every mid-play read was frozen into the plan inside the body, so this deploy
    is a pure function of static input. Each notifying request carries a stable
    ``name=`` drawn from :data:`CONFIGURE_HANDLER_NAMES` /
    :data:`FPM_HANDLER_NAMES`.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        # configure.yml: the conf + extension directories (mode 0755). The
        # role's follow:true is inert on stock Ubuntu's real directories;
        # files.directory has no follow parameter (a symlinked conf path would
        # fail fast rather than follow -- recorded deviation).
        for path in plan.directories:
            yield op(
                files.directory,
                name="Ensure configuration directories exist",
                path=path,
                mode="0755",
                sudo=True,
            )
        # configure.yml: the managed php.ini in every conf path.
        for path, content in plan.php_ini:
            yield op(
                files.install,
                name="Place PHP configuration file in place",
                content=content,
                dest=path,
                mode="0644",
                user="root",
                group="root",
                sudo=True,
            )
        # configure-apcu.yml / configure-opcache.yml: remove the stray configs
        # the FindInFiles sweep found (basename-filtered, shallow, non-hidden).
        for path in plan.stray_apcu:
            yield op(
                files.file,
                name="Remove any non-role-supplied APCu config files",
                path=path,
                present=False,
                force=True,
                sudo=True,
            )
        for path in plan.stray_opcache:
            yield op(
                files.file,
                name="Remove any non-role-supplied OpCache config files",
                path=path,
                present=False,
                force=True,
                sudo=True,
            )
        # The role's own APCu ini, or its removal when APC is disabled.
        for path, content in plan.apc_installs:
            yield op(
                files.install,
                name="Ensure APCu config file is present",
                content=content,
                dest=path,
                mode="0644",
                user="root",
                group="root",
                sudo=True,
            )
        for path in plan.apc_removals:
            yield op(
                files.file,
                name="Remove APCu config file if APC is disabled",
                path=path,
                present=False,
                force=True,
                sudo=True,
            )
        # The role's own OpCache ini, or its removal when OpCache is disabled.
        for path, content in plan.opcache_installs:
            yield op(
                files.install,
                name="Ensure OpCache config file is present",
                content=content,
                dest=path,
                mode="0644",
                user="root",
                group="root",
                sudo=True,
            )
        for path in plan.opcache_removals:
            yield op(
                files.file,
                name="Remove OpCache config file if OpCache is disabled",
                path=path,
                present=False,
                force=True,
                sudo=True,
            )
        # configure-fpm.yml: the pool directory, only when it was absent.
        if plan.pool_dir is not None:
            yield op(
                files.directory,
                name="Ensure the default pool directory exists",
                path=plan.pool_dir,
                mode="0755",
                user="root",
                group="root",
                sudo=True,
            )
        # configure-fpm.yml: the FPM pools (notify restart php-fpm).
        for path, content in plan.pools:
            yield op(
                files.install,
                name="Create fpm pools",
                content=content,
                dest=path,
                mode="0644",
                user="root",
                group="root",
                sudo=True,
            )
        # configure-fpm.yml: the FPM service state (no notify). restarted /
        # reloaded are stateless (always changed), faithful to Ansible's service
        # module; the default started is fact-gated and converges.
        if plan.fpm_service is not None:
            service = plan.fpm_service
            yield op(
                systemd.service,
                name="Ensure php-fpm is started and enabled at boot (if configured)",
                service=service.service,
                running=service.running,
                restarted=service.restarted,
                reloaded=service.reloaded,
                enabled=service.enabled,
                sudo=True,
            )

    return deploy


def handler(
    v: PhpVars,
    rv: PhpResolution,
    webserver_hosts: frozenset[str],
    fpm_hosts: frozenset[str],
) -> Deploy:
    """``handlers/main.yml``: ``restart webserver``, then chained ``restart php-fpm``.

    The chain collapses to an upstream-first service listing (the documented
    handler-chaining recipe): this host's webserver request is yielded before its
    FPM request, which is all the notification chain guaranteed. Neither the
    example's ``compose`` nor its whole-inventory ``sorted`` name lists are
    needed -- the caller has already applied the role's own gates
    (``php_enable_webserver`` for the webserver set, ``php_enable_php_fpm`` and
    ``php_fpm_state == 'started'`` for the FPM set, which unions the direct pool
    notifications with the webserver chain), and
    :func:`~reemote.handlers.service_handler` acts only when ``host.name`` is in
    the set it is given. An empty set yields no request -- the converged no-op
    Ansible's handler gives for free.
    """
    webserver = service_handler(webserver_hosts, (rv.webserver_daemon,), action="restarted")
    fpm = service_handler(
        fpm_hosts, (rv.fpm_daemon,), action=FPM_HANDLER_ACTIONS[v.fpm_handler_state]
    )

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield from webserver(host)
        yield from fpm(host)

    return deploy


# --- the role ----------------------------------------------------------------


def php(v: VarsSet[PhpVars]) -> Deploy:
    """Install and configure PHP on Ubuntu hosts, and run its handlers.

    Args:
        v: The role's resolved variables, one :class:`PhpVars` per host
            (``resolve_vars(PhpVars, "php_", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves` that returns :class:`PhpOutputs`
        for every host it runs -- including a host outside the target family,
        whose outputs carry ``version=None``.

    Raises:
        ValueError: ``php_default_version_debian`` or the resolved version is not
            dotted digits. Raised inside the body, right after the distribution
            barrier and before any request is built.
    """

    def deploy(host: Host) -> Generator[StageItem, Any, PhpOutputs]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            # The outputs still have to be published: a composing role captures
            # this return value with `yield from` (and checks for the None).
            return PhpOutputs(version=None)

        distribution = yield fact(LinuxDistribution)
        rv = resolve_php(hv, distribution)

        # Wave: the packages, closed by their own checkpoint. The report it
        # returns is the install wave only, which is what makes both the gate and
        # the first half of the relay read exactly the requests they must.
        yield from install(install_plan(hv, rv))(host)
        install_report = yield checkpoint("install done")
        package_changed = changed_hosts(install_report, PACKAGE_GATE_NAMES)
        webserver_notified = changed_hosts(install_report, INSTALL_HANDLER_NAMES)

        # Wave: the packaged ini cleanup, gated on that install report and closed
        # by its own checkpoint -- its two removals notify too.
        yield from packaged_cleanup(packaged_cleanup_plan(hv, rv), package_changed)(host)
        cleanup_report = yield checkpoint("cleanup done")
        webserver_notified |= changed_hosts(cleanup_report, CLEANUP_HANDLER_NAMES)

        # One wave per content sweep, after the cleanup applied: the role's
        # find/stat run after setup-Debian, so a packaged ini the cleanup deleted
        # must not still show up as a stray. An explicit loop with a named
        # temporary, because `yield` is illegal inside a generator expression and
        # awkward in a compound statement (`hits += yield fact(...)` does not
        # parse).
        apcu_hits: list[str] = []
        opcache_hits: list[str] = []
        for ext_path in rv.ext_paths:
            apcu = yield fact(FindInFiles, ext_path, APCU_PATTERN)
            apcu_hits += apcu
            opcache = yield fact(FindInFiles, ext_path, OPCACHE_PATTERN)
            opcache_hits += opcache

        # The pool directory. The role's stat carries no ``when`` at all -- only
        # the pool and service tasks are gated on php_enable_php_fpm -- because
        # its answer decides whether configure-fpm.yml creates the directory, so
        # a FPM-disabled host pays for this read too.
        pool_dir = yield fact(FileExists, posixpath.dirname(rv.pool_conf_path))
        pool_dir_exists = bool(pool_dir)

        # Wave: the configuration, closed by the third checkpoint.
        plan = configure_plan(hv, rv, tuple(apcu_hits), tuple(opcache_hits), pool_dir_exists)
        yield from configure(plan)(host)
        configure_report = yield checkpoint("configure done")
        webserver_notified |= changed_hosts(configure_report, CONFIGURE_HANDLER_NAMES)
        fpm_direct = changed_hosts(configure_report, FPM_HANDLER_NAMES)

        # Wave: the handlers. The relay set is the union of all three reports;
        # the two per-handler sets are that union narrowed by this host's own
        # gates, exactly as handlers/main.yml's ``when`` conditions do.
        webserver_hosts = webserver_notified if hv.enable_webserver else frozenset()
        fpm_hosts = frozenset()
        if hv.enable_php_fpm and hv.fpm_state == "started":
            fpm_hosts = webserver_hosts | fpm_direct
        yield from handler(hv, rv, webserver_hosts, fpm_hosts)(host)

        return PhpOutputs(
            version=rv.version,
            extension_conf_paths=rv.ext_paths,
            webserver_notified=webserver_notified,
        )

    return deploy


__all__ = ["PhpOutputs", "PhpVars", "php"]
