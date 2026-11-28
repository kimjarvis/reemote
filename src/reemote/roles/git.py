# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The Git role (migrated from ``geerlingguy.git``).
#
# Installs Git, the distributed version-control system, from the OS package
# archive: the apt-cache refresh and the ``git_packages`` install on Ubuntu, the
# ``git_packages`` install (with its optional ``enablerepo``) on RedHat -- and
# nothing else; the role has no handlers, no templates and no config files.
# :func:`git` is the whole task list and :class:`GitVars` is
# ``defaults/main.yml``, bound from the inventory entry's ``git`` role mapping by
# :func:`~reemote.rolevars.resolve_vars`. The role *installs* git; cloning
# repositories is the separate :mod:`reemote.modules.git` namespace, reached at
# the top level as ``reemote.git`` -- the role factory lives only under
# ``reemote.roles`` and shadows nothing (the ``postgresql`` rule, §17.1).
#
# Target: hosts in the ``"Ubuntu"`` and ``"RedHat"`` OS families -- the families
# the Ansible role supports (its ``tasks/main.yml`` branches on
# ``ansible_facts.os_family`` between exactly these two, its README installs
# "on any RHEL/CentOS or Debian/Ubuntu" system, and its ``meta/main.yml``
# declares Fedora, Debian and Ubuntu). The role has no Alpine path -- no apk
# task, no ``vars/Alpine.yml``, no Alpine platform in ``meta`` -- so Alpine is
# out of scope here too: a host outside the two families returns before its
# first ``yield`` and emits no requests, no barriers and no log lines at all,
# so the Alpine hosts in a shared inventory are never contacted. The opt-in
# build-from-source branch (``git_install_from_source``, default-false) stays
# scoped out: setting the knob fails before any host is contacted (deviation
# D2).
#
# The stream is **one request per host, no barrier at all** -- the cheapest
# shape in the corpus, one notch below the barrier-free ``nodejs``:
#
# 0. Ubuntu: ``apt.packages(packages, update=True, cache_valid_time=86400)`` --
#    the role's two Debian tasks folded into one request (D1).
#    RedHat: ``dnf.packages(packages, enable_repos=...)`` -- the ``package``
#    task with its ``enablerepo`` mapped onto the additive ``enable_repos``
#    spelling (D3); upstream's RedHat path refreshes no metadata, so neither
#    does this request.
#
# Nothing needs a read of the machine before the request can be built:
# ``git_packages`` is a plain variable, not a version-keyed ``include_vars``
# table (that dispatch belongs to the scoped-out source branch), and the role
# ships no handlers, no ``register``/``when`` outcome reads and no command
# tasks -- so there is no ``fact``, no ``act``, no ``checkpoint`` and no
# ``control``, and the whole role lands in wave 0. Consequences: a ``dry=True``
# preview has no barrier to park on, so it plans the request and applies
# nothing (the ``nodejs`` D9 shape), and the declared inventory ``family`` is
# trusted without a ``LinuxDistribution`` re-check (D4).
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: the role's two Debian tasks -- ``apt: update_cache=true
#   cache_valid_time=86400`` and the ``apt: name={{ git_packages }}`` install --
#   fold into ONE ``apt.packages(update=True, cache_valid_time=86400)`` (the
#   ``java`` precedent). Faithful on the default path, where the refresh exists
#   only to serve the install; the refresh is gated on the ``AptUpdateStamp``
#   fact against the controller's UTC clock, and a stale-cache refresh reports
#   ``changed=True``, as Ansible's separate update task would.
# * D2: ``git_install_from_source: true`` **fails fast at variable
#   resolution** -- the build-from-source branch (dependencies, version probe,
#   kernel.org tarball download, ``make``) is out of the agreed
#   package-install scope on either family; rejecting beats ignoring (the
#   ``drush`` precedent).
#   Its sub-knobs (``git_version``, ``git_install_path``, ``workspace``,
#   ``git_install_from_source_force_update``, ``git_reinstall_from_source``)
#   are not fields either, so ``extra="forbid"`` rejects them the same way.
# * D3: ``git_enablerepo`` is carried as :attr:`GitVars.enablerepo` and is
#   **live only on the RedHat request** -- the additive ``enable_repos``
#   spelling (the ``apache`` precedent), omitted when empty exactly like
#   Ansible's ``default(omit, true)``. apt has no such option, so the Ubuntu
#   branch ignores it, faithfully to the role whose Debian task never reads the
#   variable.
# * D4: the host-family guard is the body's opening ``if`` and the declared
#   inventory ``family`` is trusted: no ``fact(LinuxDistribution)`` barrier
#   exists to re-check it, because the request is a pure function of the
#   variables (the ``nodejs`` precedent; ``java`` reads the distribution only
#   for its version-keyed vars table, which this role does not have).
# * D5: every request carries ``sudo=True`` explicitly (the role relies on
#   play-level ``become``); run-as users/passwords come from the inventory
#   ``privilege`` section, never from an operation. Variable validation becomes
#   pydantic validation: a bad :attr:`GitVars.packages` raises
#   ``ValidationError`` -- a ``ValueError``, so the runner maps it -- before any
#   host is contacted.
# """

from collections.abc import Generator
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ..core import op
from ..hosts import Host
from ..modules import apt, dnf
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem

# --- role constants (defaults/main.yml + tasks/main.yml, both family paths) ---

#: ``tasks/main.yml``'s ``apt: update_cache=true cache_valid_time=86400`` --
#: the cache is considered fresh for a day. Folded into the install request
#: (deviation D1) and gated on the ``AptUpdateStamp`` fact.
APT_CACHE_VALID_TIME = 86400

#: The families ``tasks/main.yml`` branches on -- ``ansible_facts.os_family ==
#: 'Debian'`` (reemote's ``"Ubuntu"``) and ``== 'RedHat'`` (its ``"RedHat"``,
#: which also covers the role's Fedora/CentOS paths). Everything else, Alpine
#: included, has no upstream path and hits the family guard's early return.
TARGET_FAMILIES = ("Ubuntu", "RedHat")


# --- variables ---------------------------------------------------------------


class GitVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``git`` role mapping, so ``packages`` there sets
    :attr:`packages`. The model is frozen, so it stays hashable through the
    deploy body (:class:`~reemote.rolevars.VarsSet` is closed over by the
    factory), and :attr:`packages` is a ``tuple``, never a ``list`` (design doc
    section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``git_packages``: the packages to install. The role's default is the
    #: bare ``git`` package; ``git-svn``-style additions are exactly what the
    #: README recommends carrying here. An empty tuple installs nothing (the
    #: cache refresh still runs), as an empty ``git_packages`` list does.
    packages: tuple[str, ...] = ("git",)
    #: ``git_install_from_source``: scoped out; ``True`` fails fast at
    #: resolution time (deviation D2).
    install_from_source: bool = False
    #: ``git_enablerepo``: the configured-but-disabled repo id the RedHat
    #: install passes as ``enable_repos`` (deviation D3); empty omits the flag
    #: as Ansible's ``default(omit, true)`` does. Inert on Ubuntu, where the
    #: role never reads the variable.
    enablerepo: str = ""

    @field_validator("packages", mode="before")
    @classmethod
    def _normalize_sequence(cls, value: Any) -> Any:
        """A list becomes a tuple; a non-sequence falls through for pydantic to reject."""
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @field_validator("packages")
    @classmethod
    def _entries_non_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Every entry must be a non-empty package name; the empty tuple is valid."""
        if not all(isinstance(item, str) and item for item in value):
            raise ValueError("packages must hold non-empty package names")
        return value

    @model_validator(mode="after")
    def _reject_scoped_out(self) -> "GitVars":
        """Fail fast on the build-from-source branch this migration scopes out.

        Raised while the variables are resolved, so before any host is
        contacted -- the ``drush`` precedent.
        """
        if self.install_from_source:
            raise ValueError(
                "git_install_from_source is scoped out of this migration (the "
                "role's build-from-source branch is opt-in); leave it false -- "
                "the default path installs git from the OS package archive"
            )
        return self


# --- the role ----------------------------------------------------------------


def git(v: VarsSet[GitVars]) -> Deploy:
    """Install Git from the package archive on Ubuntu and RedHat hosts.

    Args:
        v: The role's resolved variables, one :class:`GitVars` per host
            (``resolve_vars(GitVars, "git", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs -- no
        other role consumes them -- so it returns ``None`` on every path,
        including the family guard's early return.

    Raises:
        ValueError: A role variable is invalid -- a bad :attr:`GitVars.packages`
            entry, or ``install_from_source`` set to ``true`` (D2). Each is
            raised by pydantic while the variables are resolved, before any
            host is contacted; the deploy body itself never raises.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family not in TARGET_FAMILIES:
            return

        if host.family == "RedHat":
            # ``Ensure git is installed (RedHat)``: the generic ``package``
            # task with ``enablerepo`` rendered additively as
            # ``enable_repos`` and omitted when empty (deviation D3). The
            # upstream RedHat task refreshes no metadata, so this request
            # carries no cache update either.
            yield op(
                dnf.packages,
                name="Ensure git is installed (RedHat)",
                packages=hv.packages,
                enable_repos=(hv.enablerepo,) if hv.enablerepo else (),
                sudo=True,
            )
            return

        # The role's two Debian tasks as one request (deviation D1): the
        # cache refresh folded into the install, gated on AptUpdateStamp.
        # No barrier precedes it -- the packages are plain variables, so
        # nothing needs a read of the machine first -- and none follows:
        # no handler and no outcome read means a checkpoint would report
        # to nobody (design doc section 4.3).
        yield op(
            apt.packages,
            name="Ensure git is installed",
            packages=hv.packages,
            update=True,
            cache_valid_time=APT_CACHE_VALID_TIME,
            sudo=True,
        )

    return deploy


__all__ = [
    "GitVars",
    "git",
]
