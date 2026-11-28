# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The Remi repository role (migrated from ``geerlingguy.repo-remi``).
#
# Installs Remi's RPM repository on RHEL-family hosts: import the Remi GPG key,
# then install the ``remi-release`` RPM from the vendor URL. The Ansible role's
# whole task list is one deploy here -- :func:`repo_remi` -- and its two role
# defaults are :class:`RemiVars`, bound from the inventory entry's ``remi`` role
# mapping by :func:`~reemote.rolevars.resolve_vars`.
#
# The stream carries three or four waves -- one per barrier, then one holding
# the requests (design doc section 5.7):
#
# 0. ``fact(LinuxDistribution)`` -- the major version the URLs interpolate, and
#    the re-check that the declared family matches the machine.
# 1. ``fact(FileExists, path=/etc/yum.repos.d/remi.repo)`` -- the install guard:
#    the file the ``remi-release`` RPM ships (verified against the published
#    EL9 package).
# 2. ``control(files.fetch_url, key_url)`` -- only when the repo file is absent.
# 3. the requests: :func:`install` alone.
#
# There is **no configure stage**: where EPEL's role also flips ``enabled=`` in
# ``[epel]``, this one never edits a repo file -- Remi ships ``[remi]`` disabled
# and is used per-transaction with ``--enablerepo=remi`` -- so a converged
# host's pass ends at the guard and yields no request at all.
#
# Deviations from the Ansible role, all deliberate:
#
# * the key and the RPM are read **control-side** (``files.fetch_url`` /
#   ``files.download``); ``rpm_key`` and ``yum`` download on the target. The
#   control machine needs egress to ``rpms.remirepo.net``; the host downloads
#   nothing.
# * ``yum: name=<URL>.rpm`` becomes a staged download plus ``dnf.rpm(src=spool,
#   package="remi-release")`` plus a spool cleanup -- ``dnf.rpm`` installs an
#   ``.rpm`` already on the host and rejects a URL. The cleanup has no Ansible
#   counterpart.
# * the GPG key id is **pinned** per enterprise major (:data:`REMI_KEY_IDS`) so
#   ``dnf.key`` can gate the import on the ``RpmGpgKeys`` fact. A custom
#   ``repo_gpg_key_url`` still imports: its id simply matches nothing.
# * ``until``/``retries``/``delay`` are dropped -- reemote is fail-fast, so a
#   transient fetch aborts the run and the operator re-runs it.
# * the host-family guard is the body's opening ``if``, not a ``when=`` on every
#   request: a host outside the ``"RedHat"`` family returns before it yields
#   anything, so it emits no requests and no log entries at all.
# """

import io
import posixpath
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..hosts import Host
from ..modules import dnf, files
from ..modules.files import FileExists
from ..modules.server import LinuxDistribution
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, control, fact

#: Where :func:`~reemote.modules.files.download` stages the release RPM before
#: ``dnf.rpm`` installs it, and what the cleanup request afterwards removes.
SPOOL_DIR = "/tmp"

#: The repo file whose presence marks an installed ``remi-release``: the guard
#: that makes a converged host's run a no-op. The RPM ships it (plus
#: ``remi-safe.repo`` and the test/debuginfo sections inside it); the role
#: manages no file contents, so one path is the whole guard.
REPO_FILE = "/etc/yum.repos.d/remi.repo"

#: The role's URL templates. ``{major}`` replaces the Ansible
#: ``ansible_facts.distribution_major_version`` interpolation, resolved from
#: the gathered ``LinuxDistribution`` fact instead of an ``include_vars`` table.
RPM_URL_TEMPLATE = "https://rpms.remirepo.net/enterprise/remi-release-{major}.rpm"
KEY_URL_TEMPLATE = "https://rpms.remirepo.net/enterprise/{major}/RPM-GPG-KEY-remi"

#: The Remi enterprise signing keys' rpm short ids (the last 8 hex digits of
#: the fingerprint), pinned so ``dnf.key`` gates the import on ``RpmGpgKeys``.
#: Verified against the published keys at :data:`KEY_URL_TEMPLATE`; the EL9
#: entry also matches the ``478f8947`` signature of the published
#: ``remi-release-9.rpm``.
REMI_KEY_IDS: dict[int, str] = {
    7: "00f97f56",
    8: "5f11735a",
    9: "478f8947",
    10: "2a0948e4",
}


class RemiVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``remi`` role mapping, so ``repo_url`` there sets
    :attr:`repo_url`. Both stay ``None`` by default so the version-derived
    templates apply, which is how the role interpolates them from the
    distribution major version.

    The model is frozen, so it stays hashable through the deploy body
    (:class:`~reemote.rolevars.VarsSet` is closed over by the factory).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo_url: str | None = None
    repo_gpg_key_url: str | None = None

    @field_validator("repo_url", "repo_gpg_key_url")
    @classmethod
    def _none_or_non_empty(cls, value: str | None) -> str | None:
        """An empty override is a mistake, not "use the template"."""
        if value is not None and not value:
            raise ValueError("must be a non-empty string or null")
        return value


@dataclass(frozen=True)
class RemiPlan:
    """One host's resolved Remi targets: the pure half of the plan freeze.

    Built by :func:`resolve_remi` from the resolved variables and the gathered
    major version, so every value is decided before a request is built. Frozen
    and hashable, as a deploy body's local must be.
    """

    rpm_url: str
    key_url: str
    rpm_spool: str


def resolve_remi(v: RemiVars, major: int) -> RemiPlan:
    """Resolve the URLs and the spool path for one host.

    Pure: no host read, no IO. An explicit URL from the inventory wins over the
    template (Ansible's variable precedence). The spool path is deterministic --
    the URL's basename under :data:`SPOOL_DIR` -- so it is the same string in
    every phase.
    """
    rpm_url = v.repo_url or RPM_URL_TEMPLATE.format(major=major)
    return RemiPlan(
        rpm_url=rpm_url,
        key_url=v.repo_gpg_key_url or KEY_URL_TEMPLATE.format(major=major),
        rpm_spool=posixpath.join(SPOOL_DIR, posixpath.basename(rpm_url)),
    )


def install(
    plan: RemiPlan,
    key_id: str,
    key_stream: Any,
    repo_exists: bool,
) -> Deploy:
    """The role's guarded key-import and RPM-install tasks (requests 1--4).

    A host whose repo file already exists is the role's converged case: the
    body yields nothing, which is what makes a converged host's run a no-op.

    ``key_stream`` is the ``io.BytesIO`` the deploy body built after the
    ``control`` barrier. Under ``run_waves`` there is one pass, so building it
    in the body is correct -- hoisting it to module level would share one
    consumed stream across hosts. ``key_id`` gates the import on
    ``RpmGpgKeys``, reproducing ``rpm_key: state=present`` idempotence.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if repo_exists:
            return
        yield op(
            dnf.key,
            name="Import remi GPG key.",
            src=key_stream,
            keyid=key_id,
            sudo=True,
        )
        # The 0644 spool mode keeps the file login-readable for the binary
        # sha1 read-back and for the RpmPackage fact's ``rpm -qp`` query.
        yield op(
            files.download,
            name="Stage the remi release RPM.",
            url=plan.rpm_url,
            dest=plan.rpm_spool,
            mode="0644",
            sudo=True,
        )
        # ``package`` is the identity hint: the staged file stays authoritative
        # while it exists, and once the cleanup below (or tmpreaper) removes
        # it, presence falls back to the installed rpm database -- Ansible's
        # ``yum state=present`` test, which the bare file path cannot make
        # after its own artifact is gone.
        yield op(
            dnf.rpm,
            name="Install the remi release RPM.",
            src=plan.rpm_spool,
            package="remi-release",
            sudo=True,
        )
        # No Ansible counterpart: yum leaves no local file. Removing the spool
        # keeps a later pass from installing from a staged RPM that has aged.
        yield op(
            files.file,
            name="Remove the staged remi release RPM.",
            path=plan.rpm_spool,
            present=False,
            sudo=True,
        )

    return deploy


def repo_remi(v: VarsSet[RemiVars]) -> Deploy:
    """Install the Remi repository on RHEL-family hosts.

    Args:
        v: The role's resolved variables, one :class:`RemiVars` per host
            (``resolve_vars(RemiVars, "remi", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. Publishes nothing, so the
        generator returns ``None``; a composing role sequences it with
        ``yield from scoped("remi", repo_remi(v))(host)``.

    Raises:
        ValueError: The distribution is not RHEL-family, or its major version
            has no pinned key id in :data:`REMI_KEY_IDS`. Both are raised after
            the ``LinuxDistribution`` barrier and before any request runs, so an
            unsupported host fails without mutating anything.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "RedHat":
            return

        # Wave 0: the role's URL interpolation source, and the only proof that
        # the declared family matches the machine.
        distro = yield fact(LinuxDistribution)
        if distro["family"] != "RedHat":
            raise ValueError(
                f"{host.name}: declared RedHat family but the distribution is "
                f"{distro['name']!r} (family {distro['family']!r}); "
                "the Remi role targets the RHEL family"
            )
        major = distro["major"]
        key_id = REMI_KEY_IDS.get(major)
        if key_id is None:
            raise ValueError(
                f"{host.name}: no pinned Remi key id for major version {major}; "
                f"supported: {tuple(sorted(REMI_KEY_IDS))}"
            )

        plan = resolve_remi(hv, major)

        # Wave 1: the repo file the release RPM ships -- the install guard.
        repo_exists = bool((yield fact(FileExists, path=REPO_FILE)))

        key_stream = None
        if not repo_exists:
            # Wave 2: control-side read whose URL comes from a per-host fact,
            # so it cannot move into an ``async def`` factory. The barrier
            # returns the text, never the stream, so a journal could serialise
            # it; the stream is built here, once, in this single pass.
            key_text = yield control(files.fetch_url, plan.key_url)
            key_stream = io.BytesIO(key_text.encode("utf-8"))

        yield from install(plan, key_id, key_stream, repo_exists)(host)

    return deploy


__all__ = [
    "RemiVars",
    "repo_remi",
]
