# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The PHP Versions role (migrated from ``geerlingguy.php-versions``).
#
# Prepares an Ubuntu host for exactly one PHP version: installs the repository
# dependencies, downloads the Ondrej Sury PPA's binary signing key into a
# repo-named keyring, adds the PPA itself as a deb822 ``.sources`` file
# referring to it (so apt can *see* the other versions), purges every other
# version's ``php<X>-common`` package, and -- only when that purge actually
# removed something -- purges the shared ``php-common`` package too. The
# version-keyed paths and package names the Ansible role published as play
# variables (``php_conf_paths``, ``php_packages``, the FPM and module paths, the
# extension package names) are this role's :class:`PhpVersionsOutputs`: a
# composing deploy captures them with ``yield from`` and feeds them to the
# :func:`~reemote.roles.php` role's own variables. Installing the chosen version
# is, exactly as upstream, the ``php`` role's job, not this one's.
#
# Target: hosts in the ``"Ubuntu"`` OS family. The role's RedHat branch
# (``setup-RedHat.yml``, the remi enablement) is out of scope, so its
# ``include_vars`` OS dispatch collapses to the frozen Debian constants below;
# a host outside the family returns before its first ``yield`` and emits no
# requests, no barriers and no log lines at all.
#
# The stream carries up to three waves -- one ``fact`` barrier, one
# ``checkpoint``, and the request waves they close (design doc section 5.7):
#
# 0. ``fact(LinuxDistribution)`` -- ``release_meta.VERSION_CODENAME`` feeds both
#    the repository's ``Suites`` and its file name (Ansible's
#    ``distribution_release``), so the setup wave is a pure function of it.
# 1. :func:`setup` -- dependencies, the repository keyring, the deb822
#    repository, the purge of the other versions -- closed by
#    ``checkpoint("setup done")``. The report it returns is the Ansible
#    ``register: php_version_purge``.
# 2. :func:`purge_common` -- the ``php-common`` removal, only on the hosts the
#    checkpoint found the purge request changed; on a converged host the wave
#    is empty and vanishes entirely.
#
# There is **no barrier between the repository and the purge**: the driver
# applies each request before pulling the next, so the index refresh happens
# before the purge runs, exactly as the two plays ordered them. The purge and
# the dependency install do not need the repository either (removals resolve
# against the dpkg database), so the three requests share one wave in yield
# order.
#
# No template engine is needed -- the role ships no templates, and everything
# this module derives is an f-string -- so it never imports
# :mod:`reemote.roles._render` and needs no ``reemote[templates]`` extra.
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: the ``apt: update_cache=true`` task (``when: php_ondrej_debian_repo.
#   changed``) folds into ``apt.sources_file(update_cache=True)`` -- the
#   ``apt-get update`` chains onto a *real* sources change (issue #14), the
#   same fire-on-change semantics with no separate request and no barrier.
# * D2: the purge tasks' ``force: true`` is dropped: ``--force-yes`` is
#   deprecated in modern apt (the same boundary ``apt.deb`` records), and
#   :class:`~reemote.modules.apt.AptRemoveCommand` already runs
#   non-interactively, so the flag is inert on the role's supported releases.
# * D3: the thirteen ``set_fact`` aliases ("Define PHP variables") become the
#   :class:`PhpVersionsOutputs` return value. Ansible published them into the
#   play for the soft ``geerlingguy.php`` dependency; here the composition
#   channel is a ``yield from`` return, and each consumer overrides its own
#   role variables instead.
# * D4: the purge gate ``when: php_version_purge.changed | bool`` is the
#   in-stream checkpoint: ``changed_hosts`` reads the setup wave's report
#   (design doc section 4.3), so a second pass that purged nothing queues no
#   ``php-common`` request.
# * D5: the ``with_fileglob`` pair of include targets -- ``vars/Debian.yml``
#   plus the (nonexistent upstream) ``vars/Debian-php<version>.yml`` --
#   collapses to the frozen constants; the second glob arm matches nothing in
#   the role, so there is nothing to reproduce.
# * D6: ``reject('search', 'php' ~ php_version)`` is a regex search over
#   ``php_versions_debian``; the names differ only in digits and a literal
#   dot, so the substring test in :func:`purge_packages` is identical on every
#   entry the list can hold.
# * D7: ``php_versions_install_recommends`` reaches no request here -- the
#   Ansible role only aliases it to ``php_install_recommends`` for the
#   downstream php role -- so it is a :class:`PhpVersionsVars` field carried
#   onto the outputs, where the composer maps it onto
#   :attr:`~reemote.roles.php.PhpVars.install_recommends`.
# * D8: the repository request does **not** take the ``signed_by`` URL form
#   ``apt.sources_file`` supports (issue #17): Sury's ``apt.gpg`` is a *binary*
#   OpenPGP key, and the URL form installs armored text only -- the library's
#   documented boundary. ``files.download`` (whose binary path issue #23 exists
#   for) installs the same key control-side-fetched at
#   ``/etc/apt/keyrings/ondrej-php-<codename>.gpg``, sha1-idempotent on
#   content, and ``signed_by`` gets that path -- Ansible's
#   ``deb822_repository`` did the key install and the write in one task; here
#   they are two requests in one wave, the key first, so the chained index
#   refresh (D1) still runs against a signed repository.
# * the host-family guard is the body's opening ``if``, not a ``when=`` on
#   every request, and the variable validation is pydantic: a bad
#   ``php_version`` raises ``ValidationError`` -- a ``ValueError``, so the
#   runner maps it -- before any host is contacted.
# """

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..handlers import changed_hosts
from ..hosts import Host
from ..modules import apt, files
from ..modules.server import LinuxDistribution
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, checkpoint, fact

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path only) ---

#: ``setup-Debian.yml``'s ``set_fact`` pair, verbatim: the Sury PHP repository
#: and its signing key. The key URL serves a *binary* OpenPGP body, so the
#: keyring rides ``files.download`` and ``apt.sources_file`` takes the
#: installed path (deviation D8), not the URL form of issue #17.
SURY_REPO_URI = "https://packages.sury.org/php"
SURY_KEY_URL = "https://packages.sury.org/php/apt.gpg"

#: ``setup-Debian.yml``'s ``apt: name=[apt-transport-https, ca-certificates,
#: python3-debian]``. reemote generates the deb822 text control-side, so
#: ``python3-debian`` (Ansible's module dependency) is kept only to match the
#: role's post-state, the same call the ``docker`` role records.
DEPENDENCY_PACKAGES = ("apt-transport-https", "ca-certificates", "python3-debian")

#: ``vars/Debian.yml``'s ``__php_packages`` shape: the version interpolated into
#: every name. The ``json`` and ``opcache`` entries carry their own removal
#: gates (:func:`php_packages`), mirroring the two ``set_fact`` filters.
PHP_PACKAGE_NAMES = (
    "php{version}",
    "php{version}-apcu",
    "php{version}-cli",
    "php{version}-common",
    "php{version}-curl",
    "php{version}-dev",
    "php{version}-fpm",
    "php{version}-gd",
    "php{version}-imap",
    "php{version}-json",
    "php{version}-mbstring",
    "php{version}-opcache",
    "php{version}-sqlite3",
    "php{version}-xml",
    "php{version}-yaml",
)

#: ``main.yml``: ``php<version>-json`` is dropped for PHP >= 8.0 (bundled).
JSON_REMOVED_FROM = (8, 0)

#: ``main.yml``: ``php<version>-opcache`` is dropped for PHP >= 8.5 (bundled).
OPCACHE_REMOVED_FROM = (8, 5)

#: ``vars/Debian.yml``'s ``php_versions_debian``: every version whose
#: ``-common`` package the setup purge removes except the chosen one.
OTHER_PHP_COMMON_PACKAGES = (
    "php8.0-common",
    "php8.1-common",
    "php8.2-common",
    "php8.3-common",
    "php8.4-common",
)

#: ``setup-Debian.yml``'s ``set_fact``: the OpCache ini filename on
#: Ubuntu/Debian (``php_opcache_conf_filename``).
OPCACHE_CONF_FILENAME = "10-opcache.ini"

#: The request name the ``php-common`` purge is gated on -- the Ansible
#: ``register: php_version_purge`` channel (deviation D4).
PURGE_GATE_NAMES = frozenset({"Purge other PHP versions."})


class PhpVersionsVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``php_versions`` role mapping, so ``version`` there
    sets :attr:`version` (Ansible's ``php_version``).
    :attr:`install_recommends` is ``php_versions_install_recommends``: it
    reaches no request of this role, only the outputs (deviation D7).

    The model is frozen, so it stays hashable through the deploy body
    (:class:`~reemote.rolevars.VarsSet` is closed over by the factory).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = "8.3"
    install_recommends: bool = False

    @field_validator("version")
    @classmethod
    def _version_shape(cls, value: str) -> str:
        """``php_version`` must be dotted digits, as the role's own assumes."""
        _version_tuple(value)
        return value


@dataclass(frozen=True)
class PhpVersionsOutputs:
    """What :func:`php_versions` publishes to a composing role (design doc
    sections 9, 10.4) -- the ``set_fact`` aliases of deviation D3.

    ``version`` is the chosen PHP version, ``None`` on a host the family guard
    skipped (every other field then carries its empty default). The remaining
    fields are ``vars/Debian.yml``'s ``__php_*`` values resolved for that
    version: the package list (with the D-gated ``json``/``opcache`` entries
    already dropped), the conf and extension-conf path triples, the FPM
    daemon/conf/pool locations, the four extension package names, the four
    shared module paths, and the Ubuntu opcache ini filename.
    """

    version: str | None = None
    packages: tuple[str, ...] = ()
    conf_paths: tuple[str, ...] = ()
    extension_conf_paths: tuple[str, ...] = ()
    fpm_daemon: str = ""
    fpm_conf_path: str = ""
    fpm_pool_conf_path: str = ""
    mysql_package: str = ""
    redis_package: str = ""
    memcached_package: str = ""
    pgsql_package: str = ""
    tideways_module_path: str = ""
    uploadprogress_module_path: str = ""
    xdebug_module_path: str = ""
    xhprof_module_path: str = ""
    opcache_conf_filename: str = ""
    install_recommends: bool = False


# --- frozen per-host resolution ----------------------------------------------


@dataclass(frozen=True)
class PhpVersionsPlan:
    """One host's resolved setup targets, built after the distribution barrier.

    Frozen and hashable, as a deploy body's local must be; every value is
    decided before a request is built, so the whole setup wave is a pure
    function of it. Unexported -- a composer reads the outputs, never a plan.
    """

    version: str
    codename: str
    repo_filename: str
    purge_packages: tuple[str, ...]


# --- pure helpers ------------------------------------------------------------


def _version_tuple(version: str) -> tuple[int, ...]:
    """The role's ``php_version is version(...)`` comparisons' shape check."""
    parts = version.split(".")
    if not parts or not all(part.isdigit() for part in parts):
        raise ValueError(f"php version must be dotted digits, got {version!r}")
    return tuple(int(part) for part in parts)


def php_packages(version: str) -> tuple[str, ...]:
    """``vars/Debian.yml``'s ``__php_packages`` after ``main.yml``'s filters.

    The version is interpolated into every name; ``php<version>-json`` is
    dropped from PHP 8.0 up and ``php<version>-opcache`` from 8.5 up, because
    both are bundled in the interpreter from those releases.
    """
    packages = [name.format(version=version) for name in PHP_PACKAGE_NAMES]
    major_minor = _version_tuple(version)[:2]
    if major_minor >= JSON_REMOVED_FROM:
        packages = [p for p in packages if p != f"php{version}-json"]
    if major_minor >= OPCACHE_REMOVED_FROM:
        packages = [p for p in packages if p != f"php{version}-opcache"]
    return tuple(packages)


def purge_packages(version: str) -> tuple[str, ...]:
    """``php_versions_debian | reject('search', 'php' ~ php_version)``.

    Everything in :data:`OTHER_PHP_COMMON_PACKAGES` that does not name the
    chosen version -- the entries differ only in digits, so the substring
    test is the regex search's exact result here (deviation D6).
    """
    marker = f"php{version}"
    return tuple(name for name in OTHER_PHP_COMMON_PACKAGES if marker not in name)


def distribution_codename(info: dict[str, Any]) -> str:
    """Ansible's ``distribution_release`` from the distribution read.

    Ubuntu's ``/etc/os-release`` carries ``VERSION_CODENAME`` (e.g.
    ``noble``), which the ``distro`` library surfaces in the fact's
    ``release_meta``. A missing codename is a hard error -- the repository's
    name and Suites both interpolate it -- raised before any request runs.
    """
    codename = (info.get("release_meta") or {}).get("VERSION_CODENAME")
    if not codename:
        raise ValueError(
            "cannot read VERSION_CODENAME from the distribution facts "
            "(no /etc/os-release entry); the Sury PHP repository needs the "
            "Ubuntu release codename for its name and Suites"
        )
    return str(codename)


def resolve_plan(hv: PhpVersionsVars, info: dict[str, Any]) -> PhpVersionsPlan:
    """Resolve the variables and the gathered distribution into one plan.

    The repository is named as the role names it --
    ``ondrej-php-<distribution_release>`` -- so the ``.sources`` file and the
    keyring both carry the codename, and a release change adds a file rather
    than editing the old one in place.
    """
    codename = distribution_codename(info)
    return PhpVersionsPlan(
        version=hv.version,
        codename=codename,
        repo_filename=f"ondrej-php-{codename}",
        purge_packages=purge_packages(hv.version),
    )


def published_outputs(hv: PhpVersionsVars, plan: PhpVersionsPlan) -> PhpVersionsOutputs:
    """The thirteen ``set_fact`` aliases, resolved (deviation D3).

    Pure: every value is ``vars/Debian.yml``'s shape interpolated with the
    plan's version, so a composer gets exactly what the Ansible play would
    have carried into the ``geerlingguy.php`` role.
    """
    version = plan.version
    conf_paths = (
        f"/etc/php/{version}/fpm",
        f"/etc/php/{version}/apache2",
        f"/etc/php/{version}/cli",
    )
    module_path = f"/usr/lib/php/{version}/modules"
    return PhpVersionsOutputs(
        version=version,
        packages=php_packages(version),
        conf_paths=conf_paths,
        extension_conf_paths=tuple(f"{path}/conf.d" for path in conf_paths),
        fpm_daemon=f"php{version}-fpm",
        fpm_conf_path=conf_paths[0],
        fpm_pool_conf_path=f"{conf_paths[0]}/pool.d/www.conf",
        mysql_package=f"php{version}-mysql",
        redis_package=f"php{version}-redis",
        memcached_package=f"php{version}-memcached",
        pgsql_package=f"php{version}-pgsql",
        tideways_module_path=module_path,
        uploadprogress_module_path=module_path,
        xdebug_module_path=module_path,
        xhprof_module_path=module_path,
        opcache_conf_filename=OPCACHE_CONF_FILENAME,
        install_recommends=hv.install_recommends,
    )


# --- the deploys --------------------------------------------------------------


def setup(plan: PhpVersionsPlan) -> Deploy:
    """``setup-Debian.yml``'s repository tasks and the version purge.

    The dependencies, the repository keyring downloaded control-side, the
    deb822 repository referencing it, and the purge of the other versions --
    one wave, in the plays' order. The key precedes the sources file so the
    ``update_cache=True`` refresh (D1) runs against a signed repository; the
    key is not fetched through the ``signed_by`` URL form because Sury serves
    a binary key (D8). The purge carries no ``force`` flag (D2), and
    ``apt.packages`` gates it on the ``AptPackages`` fact, so an absent
    version is a no-op rather than an apt run.
    """

    keyring = f"/etc/apt/keyrings/{plan.repo_filename}.gpg"

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            apt.packages,
            name="Add dependencies for PHP versions.",
            packages=DEPENDENCY_PACKAGES,
            sudo=True,
        )
        yield op(
            files.download,
            name="Install Ondrej's PHP repo key.",
            url=SURY_KEY_URL,
            dest=keyring,
            mode="0644",
            sudo=True,
        )
        yield op(
            apt.sources_file,
            name="Add Ondrej's PHP repo.",
            filename=plan.repo_filename,
            types=("deb",),
            uris=(SURY_REPO_URI,),
            suites=(plan.codename,),
            components=("main",),
            signed_by=keyring,
            update_cache=True,
            sudo=True,
        )
        yield op(
            apt.packages,
            name="Purge other PHP versions.",
            packages=plan.purge_packages,
            present=False,
            purge=True,
            sudo=True,
        )

    return deploy


def purge_common(changed: frozenset[str]) -> Deploy:
    """``Also purge php-common package if any versions were just purged``.

    ``register`` + ``when: ...changed`` as a per-host body: the frozen set is
    :func:`~reemote.handlers.changed_hosts` over the setup wave, so a host
    whose purge removed nothing yields nothing at all -- the converged run's
    empty wave that the driver then never appends (D4).
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if host.name not in changed:
            return
        yield op(
            apt.packages,
            name="Also purge php-common package.",
            packages=("php-common",),
            present=False,
            purge=True,
            sudo=True,
        )

    return deploy


# --- the role -----------------------------------------------------------------


def php_versions(v: VarsSet[PhpVersionsVars]) -> Deploy:
    """Prepare an Ubuntu host for exactly one PHP version.

    Adds the Sury PHP repository, purges the other versions' ``-common``
    packages (and the shared ``php-common`` behind the purge gate), and
    publishes the version-keyed paths and package names for the
    :func:`~reemote.roles.php` role to install from.

    Args:
        v: The role's resolved variables, one :class:`PhpVersionsVars` per
            host (``resolve_vars(PhpVersionsVars, "php_versions",
            inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves` that returns
        :class:`PhpVersionsOutputs` for every host it runs -- including a host
        outside the target family, whose outputs carry ``version=None``.

    Raises:
        ValueError: The distribution read carries no ``VERSION_CODENAME``;
            ``php_versions.version`` was already checked during variable
            resolution. The raise is inside the body, right after the
            distribution barrier and before any request is built, so an
            unreadable host fails without mutating anything.
    """

    def deploy(host: Host) -> Generator[StageItem, Any, PhpVersionsOutputs]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            # The outputs still have to be published: a composing role
            # captures this return value with `yield from`.
            return PhpVersionsOutputs(version=None)

        # Wave 0: Ansible's distribution_release -- the repo name and Suites.
        info = yield fact(LinuxDistribution)
        plan = resolve_plan(hv, info)

        # Wave 1: dependencies, repository, purge -- no read sits between the
        # three, so they share one wave in the plays' order.
        yield from setup(plan)(host)

        # Wave 2: the php-common purge, gated on this host's purge request
        # having really removed something (the register + when pair, D4).
        setup_report = yield checkpoint("setup done")
        yield from purge_common(changed_hosts(setup_report, PURGE_GATE_NAMES))(host)

        return published_outputs(hv, plan)

    return deploy


__all__ = [
    "PhpVersionsOutputs",
    "PhpVersionsVars",
    "php_versions",
]
