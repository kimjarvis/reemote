# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The EPEL repository role (migrated from ``geerlingguy.repo-epel``).

Installs the EPEL (Extra Packages for Enterprise Linux) repository on
RHEL-family hosts: import the EPEL GPG key, install the ``epel-release`` RPM,
then set ``enabled=`` in the ``[epel]`` section of ``/etc/yum.repos.d/epel.repo``.
The Ansible role's whole task list is one deploy here --
:func:`repo_epel` -- and its four role defaults are :class:`EpelVars`, bound
from the inventory's ``epel_``-prefixed keys by
:func:`~reemote.rolevars.resolve_vars`.

The stream carries three or four waves -- one per barrier, then one holding the
requests (design doc section 5.7):

0. ``fact(LinuxDistribution)`` -- the major version the URLs interpolate, and
   the re-check that the declared family matches the machine.
1. ``fact(FileExists, path=repofile)`` -- the old ``stat`` register.
2. ``control(files.fetch_url, key_url)`` -- only when the repo file is absent.
3. the requests: :func:`install` then :func:`configure`, in one stream.

There is **no barrier between install and configure**: the driver applies a
request before it pulls the next one, so ``epel-release`` is installed (and
ships ``/etc/yum.repos.d/epel.repo``) before ``configure``'s read. That is what
collapses the example's old two-deploy split into a single pass.

Deviations from the Ansible role, all deliberate:

* the key and the RPM are read **control-side** (``files.fetch_url`` /
  ``files.download``); ``rpm_key`` and ``yum`` download on the target. The
  control machine needs egress to ``dl.fedoraproject.org``; the host downloads
  nothing.
* ``yum: name=<URL>.rpm`` becomes a staged download plus ``dnf.rpm(src=spool)``
  plus a spool cleanup -- ``dnf.rpm`` installs an ``.rpm`` already on the host
  and rejects a URL. The cleanup has no Ansible counterpart.
* the GPG key id is **pinned** per EPEL major (:data:`EPEL_KEY_IDS`) so
  ``dnf.key`` can gate the import on the ``RpmGpgKeys`` fact. A custom
  ``repo_gpg_key_url`` still imports: its id simply matches nothing.
* ``until``/``retries``/``delay`` are dropped -- reemote is fail-fast, so a
  transient fetch aborts the run and the operator re-runs it.
* the host-family guard is the body's opening ``if``, not a ``when=`` on every
  request: a host outside the ``"RedHat"`` family returns before it yields
  anything, so it emits no requests and no log entries at all.
"""

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

#: The role's URL templates. ``{major}`` replaces the Ansible
#: ``ansible_facts.distribution_major_version`` interpolation, resolved from
#: the gathered ``LinuxDistribution`` fact instead of an ``include_vars`` table.
RPM_URL_TEMPLATE = (
    "https://dl.fedoraproject.org/pub/epel/epel-release-latest-{major}.noarch.rpm"
)
KEY_URL_TEMPLATE = "https://dl.fedoraproject.org/pub/epel/RPM-GPG-KEY-EPEL-{major}"

#: The EPEL signing keys' rpm short ids (the last 8 hex digits of the GPG
#: fingerprint), pinned so ``dnf.key`` gates the import on ``RpmGpgKeys``.
#: Verified against the published armored keys; EPEL 9's also matches a live
#: CentOS Stream 9 host's ``gpg-pubkey-3228467c-613798eb``.
EPEL_KEY_IDS: dict[int, str] = {
    7: "352c64e5",
    8: "2f86d6a1",
    9: "3228467c",
    10: "e37ed158",
}


class EpelVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names carry no ``epel_`` prefix -- the prefix passed to
    :func:`~reemote.rolevars.resolve_vars` binds ``epel_repo_disable`` in the
    inventory to :attr:`repo_disable`. ``repo_url`` and ``repo_gpg_key_url``
    stay ``None`` by default so the version-derived templates apply, which is
    how the role interpolates them from the distribution major version.

    The model is frozen, so it stays hashable through the deploy body
    (:class:`~reemote.rolevars.VarsSet` is closed over by the factory).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    repofile_path: str = "/etc/yum.repos.d/epel.repo"
    repo_disable: bool = False
    repo_url: str | None = None
    repo_gpg_key_url: str | None = None

    @field_validator("repofile_path")
    @classmethod
    def _must_be_absolute(cls, value: str) -> str:
        """Reject a relative repo path: it would silently write elsewhere."""
        if not value.startswith("/"):
            raise ValueError(f"repofile_path must be an absolute path, got {value!r}")
        return value

    @field_validator("repo_url", "repo_gpg_key_url")
    @classmethod
    def _none_or_non_empty(cls, value: str | None) -> str | None:
        """An empty override is a mistake, not "use the template"."""
        if value is not None and not value:
            raise ValueError("must be a non-empty string or null")
        return value


@dataclass(frozen=True)
class EpelPlan:
    """One host's resolved EPEL targets: the pure half of the old plan freeze.

    Built by :func:`resolve_epel` from the resolved variables and the gathered
    major version, so every value is decided before a request is built. Frozen
    and hashable, as a deploy body's local must be.
    """

    repofile: str
    enabled: str
    rpm_url: str
    key_url: str
    rpm_spool: str


def resolve_epel(v: EpelVars, major: int) -> EpelPlan:
    """Resolve the URLs, the ``enabled`` value and the spool path for one host.

    Pure: no host read, no IO. An explicit URL from the inventory wins over the
    template (Ansible's variable precedence), and the
    ``repo_disable | ternary(0, 1)`` filter becomes the option's value text. The
    spool path is deterministic -- the URL's basename under
    :data:`SPOOL_DIR` -- so it is the same string in every phase.
    """
    rpm_url = v.repo_url or RPM_URL_TEMPLATE.format(major=major)
    return EpelPlan(
        repofile=v.repofile_path,
        enabled="0" if v.repo_disable else "1",
        rpm_url=rpm_url,
        key_url=v.repo_gpg_key_url or KEY_URL_TEMPLATE.format(major=major),
        rpm_spool=posixpath.join(SPOOL_DIR, posixpath.basename(rpm_url)),
    )


def install(
    plan: EpelPlan,
    key_id: str,
    key_stream: Any,
    repo_exists: bool,
) -> Deploy:
    """The role's guarded key-import and RPM-install tasks (requests 1--4).

    A host whose repo file already exists is the role's
    ``when: not epel_repofile_result.stat.exists`` case: the body yields
    nothing, which is what makes a converged host's run a no-op.

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
            name="Import EPEL GPG key.",
            src=key_stream,
            keyid=key_id,
            sudo=True,
        )
        # The 0644 spool mode keeps the file login-readable for the binary
        # sha1 read-back and for the RpmPackage fact's ``rpm -qp`` query.
        yield op(
            files.download,
            name="Stage the EPEL release RPM.",
            url=plan.rpm_url,
            dest=plan.rpm_spool,
            mode="0644",
            sudo=True,
        )
        yield op(
            dnf.rpm,
            name="Install the EPEL release RPM.",
            src=plan.rpm_spool,
            sudo=True,
        )
        # No Ansible counterpart: yum leaves no local file. Removing the spool
        # keeps a later pass from installing from a staged RPM that has aged.
        yield op(
            files.file,
            name="Remove the staged EPEL release RPM.",
            path=plan.rpm_spool,
            present=False,
            sudo=True,
        )

    return deploy


def configure(plan: EpelPlan) -> Deploy:
    """The role's unconditional ``ini_file`` enabled toggle (requests 5--6).

    ``files.ini_option`` edits one option in one section and preserves every
    other section, comment and blank line; ``no_extra_spaces=True`` renders
    ``enabled=<value>`` and ``create=False`` mirrors the role's ``create: no``
    (the file exists by now -- shipped by ``epel-release`` in this same wave,
    or already present). The trailing ``files.file`` converges a mode-only
    drift: install-family idempotence is content-only, while the role's
    ``ini_file: mode=0644`` re-applies the mode on every run.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            files.ini_option,
            name="Ensure EPEL repo is enabled / disabled.",
            path=plan.repofile,
            section="epel",
            option="enabled",
            value=plan.enabled,
            no_extra_spaces=True,
            create=False,
            mode="0644",
            sudo=True,
        )
        yield op(
            files.file,
            name="Ensure EPEL repo file mode is 0644.",
            path=plan.repofile,
            mode="0644",
            sudo=True,
        )

    return deploy


def repo_epel(v: VarsSet[EpelVars]) -> Deploy:
    """Install and configure the EPEL repository on RHEL-family hosts.

    Args:
        v: The role's resolved variables, one :class:`EpelVars` per host
            (``resolve_vars(EpelVars, "epel_", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. Publishes nothing, so the
        generator returns ``None``; a composing role sequences it with
        ``yield from scoped("epel", repo_epel(v))(host)``.

    Raises:
        ValueError: The distribution is not RHEL-family, or its major version
            has no pinned key id in :data:`EPEL_KEY_IDS`. Both are raised after
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
                "the EPEL role targets the RHEL family"
            )
        major = distro["major"]
        key_id = EPEL_KEY_IDS.get(major)
        if key_id is None:
            raise ValueError(
                f"{host.name}: no pinned EPEL key id for major version {major}; "
                f"supported: {tuple(sorted(EPEL_KEY_IDS))}"
            )

        plan = resolve_epel(hv, major)

        # Wave 1: the ``stat`` register the install tasks guard on.
        repo_exists = bool((yield fact(FileExists, path=plan.repofile)))

        key_stream = None
        if not repo_exists:
            # Wave 2: control-side read whose URL comes from a per-host fact,
            # so it cannot move into an ``async def`` factory. The barrier
            # returns the text, never the stream, so a journal could serialise
            # it; the stream is built here, once, in this single pass.
            key_text = yield control(files.fetch_url, plan.key_url)
            key_stream = io.BytesIO(key_text.encode("utf-8"))

        # One stream, no barrier between the two stages: install's requests are
        # applied before configure's first one is pulled.
        yield from install(plan, key_id, key_stream, repo_exists)(host)
        yield from configure(plan)(host)

    return deploy


__all__ = [
    "EpelVars",
    "repo_epel",
]
