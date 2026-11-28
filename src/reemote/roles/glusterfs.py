# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The GlusterFS role (migrated from ``geerlingguy.glusterfs``).
#
# Installs and starts GlusterFS on Ubuntu hosts: the ``dirmngr`` (and
# ``software-properties-common``) prerequisites, the optional
# ``ppa:gluster/glusterfs-<version>`` with the reinstall-on-PPA-add split, the
# ``glusterfs-server`` / ``glusterfs-client`` packages, and the ``glusterd``
# unit started and enabled at boot. :func:`glusterfs` is the whole task list --
# ``tasks/main.yml`` plus the Ubuntu branch of its ``include`` -- and the role's
# five defaults are :class:`GlusterfsVars`, bound from the inventory entry's
# ``glusterfs`` role mapping by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. The role's RedHat branch
# (``setup-RedHat.yml``, ``centos-release-gluster``) and its Debian branch
# (``setup-Debian.yml``, the manual ``apt_key`` + ``dpkg`` architecture probe +
# ``deb`` line for ``download.gluster.org``) are out of scope, so the
# ``include: setup-{{ ansible_distribution }}.yml`` dispatch collapses to the
# Ubuntu path alone.
#
# The stream carries two waves (design doc section 5.7):
#
# 0. :func:`setup` -- the dependency packages and the optional PPA -- closed by
#    ``checkpoint("setup done")``, which the reinstall gate reads (the role's
#    ``register: glusterfs_ppa_added`` + ``when: glusterfs_ppa_added is changed``).
# 1. :func:`install` then :func:`service` -- the conditional removal, the
#    package install, and the unit's running/enabled state -- in one wave. The
#    driver applies each request before pulling the next, so the unit is
#    started after the packages are installed without an extra barrier.
#
# There is deliberately **no second checkpoint** after the service task: the
# role has no handlers, so nothing would ever read that report.
#
# :func:`glusterfs` returns ``None``: no composing role in the corpus consumes
# an output, so there is no outputs dataclass (§17.2).
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: the host-family guard is the body's opening ``if``, not a ``when=`` on
#   every request: a host outside the ``"Ubuntu"`` family returns before it
#   yields anything, so it emits no requests and no log entries at all. The
#   RedHat and Debian setup files are not migrated (D2, D3).
# * D2: ``tasks/main.yml``'s ``set_fact`` that renames the daemon to
#   ``glusterfs-server`` on Ubuntu 14.04 is dropped: :attr:`GlusterfsVars.daemon`
#   simply defaults to ``glusterd``, and an operator on an old release sets the
#   variable. Pre-systemd (upstart) hosts are in any case unmanageable by
#   ``systemd.service``, so faithfully reproducing the rename would map a
#   service name no systemd unit answers to.
# * D3: Debian's ``glusterfs_gpg_key_version`` / ``glusterfs_deb_version`` are
#   carried as variables and do nothing here: they belong to
#   ``setup-Debian.yml``, which is out of scope. Both are documented as inert
#   rather than silently omitted, so an existing inventory still binds.
# * D4: Ansible's ``apt_repository`` auto-installs ``software-properties-common``
#   when ``add-apt-repository`` is missing; reemote's ``apt.ppa`` raises
#   ``CommandNotFoundError`` instead, so the dependency list is
#   ``dirmngr`` plus ``software-properties-common`` when (and only when)
#   :attr:`GlusterfsVars.ppa_use` is set. The role's own ``dirmngr`` task maps
#   onto the same request.
# * D5: the reinstall split is an in-stream ``checkpoint`` + ``changed_hosts``
#   read, not a ``register`` variable: ``apt.ppa`` reports ``changed`` only on a
#   real add (it reads ``AptSources``), so the conditional removal rides only
#   the pass that actually added the PPA, and converges to nothing after.
# """

from collections.abc import Generator
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..core import op
from ..handlers import changed_hosts
from ..hosts import Host
from ..modules import apt, systemd
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, checkpoint

#: The package pair every install/removal task in the role names verbatim.
GLUSTERFS_PACKAGES = ("glusterfs-server", "glusterfs-client")

#: The PPA request's stable name -- the ``register: glusterfs_ppa_added``
#: channel. Kept unprefixed and stable because the setup checkpoint selects on
#: it: a rename silently empties the reinstall gate.
PPA_NAME = "Add PPA for GlusterFS"


class GlusterfsVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``glusterfs`` role mapping, so ``ppa_version`` there sets
    :attr:`ppa_version`. The two Debian-only knobs (:attr:`gpg_key_version`,
    :attr:`deb_version`) are carried inert so an existing inventory still
    binds; the Ubuntu path never reads them (D3).

    The model is frozen, so it stays hashable through the deploy body
    (:class:`~reemote.rolevars.VarsSet` is closed over by the factory).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``glusterfs_daemon`` -- the unit to start and enable. The role's 14.04
    #: ``set_fact`` rename is dropped (D2); set this to ``glusterfs-server``
    #: for that legacy spelling.
    daemon: str = "glusterd"

    # --- For Ubuntu. ---
    default_release: str = ""
    ppa_use: bool = True
    ppa_version: str = "7"

    # --- For Debian: carried inert, the Debian branch is out of scope (D3). ---
    gpg_key_version: str = "7"
    deb_version: str = "LATEST"


def setup(v: GlusterfsVars) -> Deploy:
    """``tasks/setup-Ubuntu.yml``'s dependencies and the optional GlusterFS PPA.

    The role's ``dirmngr`` task and the ``software-properties-common`` package
    ``apt_repository`` would have installed implicitly (D4) share one
    ``apt.packages`` request. The PPA branch reads only static variables
    (``ppa_use``), so the request sequence is host-determined. ``apt.ppa`` is
    idempotent (it reads ``AptSources`` for the PPA's Launchpad archive) and
    reports ``changed`` only on a real add -- which is what makes the role's
    ``register: glusterfs_ppa_added`` + ``when: glusterfs_ppa_added is changed``
    reinstall split sound when the setup checkpoint reads it.
    ``update_cache=True`` refreshes the index on the add, exactly as the role's
    ``apt_repository`` task's ``update_cache: true`` does.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        dependencies = (
            ("dirmngr", "software-properties-common")
            if v.ppa_use
            else ("dirmngr",)
        )
        yield op(
            apt.packages,
            name="Ensure dependencies are installed",
            packages=dependencies,
            sudo=True,
        )
        if v.ppa_use:
            yield op(
                apt.ppa,
                name=PPA_NAME,
                src=f"ppa:gluster/glusterfs-{v.ppa_version}",
                update_cache=True,
                sudo=True,
            )

    return deploy


def install(v: GlusterfsVars, ppa_added: frozenset[str]) -> Deploy:
    """The role's reinstall trick and the GlusterFS package install.

    ``ppa_added`` is the ``changed_hosts`` read of the setup wave's report --
    the role's ``when: glusterfs_ppa_added is changed`` moved into the body
    (D5). On a host whose PPA was just added, the packages are removed first so
    the subsequent install picks up the PPA's build instead of a
    already-installed distro one; the removal is fact-gated (absent packages
    are a no-op). ``default_release`` -- the role's ``glusterfs_default_release``
    -- pins the install with apt's ``-t <release>``; the role's empty string
    means "no pin", so it maps to ``None``.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if host.name in ppa_added:
            yield op(
                apt.packages,
                name="Ensure GlusterFS will reinstall if the PPA was just added",
                packages=GLUSTERFS_PACKAGES,
                present=False,
                sudo=True,
            )
        yield op(
            apt.packages,
            name="Ensure GlusterFS is installed",
            packages=GLUSTERFS_PACKAGES,
            default_release=v.default_release or None,
            sudo=True,
        )

    return deploy


def service(v: GlusterfsVars) -> Deploy:
    """``tasks/main.yml``'s final ``service`` task: started and enabled.

    ``systemd.service`` is fact-gated on the unit's active/enabled state, so
    the default started/enabled pair converges: a running, enabled
    ``glusterd`` reports no change.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            systemd.service,
            name="Ensure GlusterFS is started and enabled at boot",
            service=v.daemon,
            running=True,
            enabled=True,
            sudo=True,
        )

    return deploy


def glusterfs(v: VarsSet[GlusterfsVars]) -> Deploy:
    """Install GlusterFS and start its daemon on Ubuntu hosts.

    Args:
        v: The role's resolved variables, one :class:`GlusterfsVars` per host
            (``resolve_vars(GlusterfsVars, "glusterfs", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves` that returns ``None`` for every
        host it runs -- no composing role in the corpus consumes an output
        (§17.2).
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return None

        # Wave 0: the dependencies and the optional PPA, closed by the
        # checkpoint the reinstall gate reads. The gate is the role's
        # `register: glusterfs_ppa_added` + `when: glusterfs_ppa_added is changed`.
        yield from setup(hv)(host)
        setup_report = yield checkpoint("setup done")
        ppa_added = changed_hosts(setup_report, (PPA_NAME,))

        # Wave 1: the conditional removal, the packages, then the unit. No
        # barrier between them: the driver applies each request before pulling
        # the next, so glusterd starts only after its packages are installed.
        yield from install(hv, ppa_added)(host)
        yield from service(hv)(host)

        return None

    return deploy


__all__ = ["GlusterfsVars", "glusterfs"]
