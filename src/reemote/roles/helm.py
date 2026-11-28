# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The helm role (migrated from ``geerlingguy.helm``).
#
# Installs `helm <https://get.helm.sh>`_, the Kubernetes package manager, on
# Ubuntu hosts: it downloads the pinned release tarball, extracts the ``helm``
# binary from it and copies the binary to the managed path, but only when the
# binary is absent or reports a different version. :func:`helm` is the whole
# task list and :class:`HelmVars` is ``defaults/main.yml``, bound from the
# inventory's ``helm`` role mapping by
# :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. A host outside the target family
# returns before its first ``yield`` and emits no requests, no barriers and no
# log lines at all.
#
# The stream is one deploy body that opens with two barriers mirroring the
# role's two pre-tasks: a ``fact(files.FileExists)`` gate (the ``stat`` of the
# binary path) and, when the binary exists, an ``act(Shell)`` version probe (the
# ``{{ helm_bin_path }} version`` command). When either says the wanted release
# is not there yet, the body emits the role's two install tasks -- a
# ``files.unarchive`` of the checksum-pinned tarball into a dedicated staging
# directory and a ``files.copy`` of the extracted binary into place. A fresh
# host runs two waves (the ``FileExists`` gate, then the two requests); a
# converged host runs two waves too (the gate, then the version probe, which
# short-circuits the install); a dry pass parks after the first wave.
#
# Deviations from the Ansible role, all deliberate:
#
# * **D1** the ``helm_platform`` default is frozen to ``linux`` and the family
#   guard drops off-target hosts silently; reemote drives Linux hosts only.
# * **D2** the ``stat`` + ``command ... version`` + ``when`` trio becomes a
#   ``fact(FileExists)`` gate and, only when the binary is present, an
#   ``act(Shell)`` probe whose ``Result`` the body reads (``helm_version in
#   stdout``). Ansible ran the version command unconditionally with
#   ``failed_when: false``; a missing binary short-circuits to the install here,
#   so the guaranteed-to-fail probe is skipped -- the same decision, one fewer
#   barrier.
# * **D3** the tarball is fetched **control-side** by :func:`~reemote.modules.files.unarchive`
#   (checksum-verified, then spooled and extracted); Ansible's ``unarchive``
#   with ``remote_src: true`` downloads on the target. The control machine needs
#   egress to the repository; the host downloads nothing.
# * **D4** the extraction targets a dedicated, version-suffixed staging
#   directory (``<staging_dir>/helm-<version>``) rather than the raw ``/tmp`` the
#   role names, because ``unarchive``'s ``mode`` is a recursive ``chmod -R`` of
#   ``dest`` (the issue #58 shape) and must not touch a shared directory. The
#   staged path is version-scoped so a version change re-extracts.
# * **D5** every ``(<version>, <arch>)`` must have a ``sha256`` digest, from the
#   shipped :data:`HELM_CHECKSUMS` table or a :attr:`HelmVars.checksums`
#   override row; an unshipped, unpinned pair raises ``ValueError`` before any
#   fetch. Ansible's role verified nothing -- the pin is the role's one gap the
#   migration closes rather than reproduces.
# * **D6** ``when: helm_download is changed`` on the copy task collapses into
#   :func:`~reemote.modules.files.copy`'s own sha1 content gate: a staged binary
#   already matching the destination reports no change without consulting the
#   unarchive's result.
# * **D7** the copy task's ``become: true`` maps to ``sudo=`` on that request
#   (the extraction into ``/tmp`` stays unprivileged); reemote has no play-level
#   ``become``, and the run-as identity comes from the inventory ``privilege``
#   section, never from an operation argument.
# * **D8** the default release is modernised from the role's 2020 ``v3.2.1`` to
#   the current ``v4.3.2``, and :data:`HELM_CHECKSUMS` ships the recent
#   releases (both Helm 4 and the last Helm 3 lines) rather than one old pin.
# """

from collections.abc import Generator
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..actions import Shell
from ..core import op
from ..hosts import Host
from ..modules import files
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, act, fact

# --- role constants (defaults/main.yml, frozen for the Ubuntu/linux target) ----

#: The default release to install (``helm_version``); modernised from the
#: role's ``v3.2.1`` (deviation D8).
DEFAULT_VERSION = "v4.3.2"

#: The platform (``helm_platform``), frozen to ``linux`` (deviation D1).
DEFAULT_PLATFORM = "linux"

#: The architecture (``helm_arch``); a plain default, not a fact-derived value.
DEFAULT_ARCH = "amd64"

#: The download repository (``helm_repo_path``).
DEFAULT_REPO_PATH = "https://get.helm.sh"

#: Where the binary lands (``helm_bin_path``).
DEFAULT_BIN_PATH = "/usr/local/bin/helm"

#: The parent of the dedicated extraction directory (deviation D4); the role
#: extracted into ``/tmp`` directly.
DEFAULT_STAGING_DIR = "/tmp"

#: ``sha256`` digests of the published tarballs (deviation D5/D8), version to
#: architecture to the hex digest from ``get.helm.sh``. Only the recent Helm 4
#: and Helm 3 releases ship here; :attr:`HelmVars.checksums` extends or
#: overrides the table per inventory.
HELM_CHECKSUMS: dict[str, dict[str, str]] = {
    "v4.3.2": {
        "amd64": "d1c0278d46beab79582d7e50f5b3dc206944ba8e577cde618513fd07daa53983",
        "arm64": "df31cb61680ef728f82c770e5280dd565617bee6b039d37e85c677288be4bbb0",
        "arm": "4af438abdb38723ab63928dead4740a41241daa46b591d5ab12a53f6828099d7",
        "ppc64le": "c397e126ed115555316bf34ec149b0ea1f99303f4b35201d5c6f070895eaaf09",
        "s390x": "076d3df25b6864a9d26a670fce7eff2cb4fe9e4f312028c56a6ade261ba74050",
    },
    "v4.3.1": {
        "amd64": "0403a88ae20913d11b10a2467b50087721fea7581736da6b9d3992f5357fe54b",
        "arm64": "8cf8f66ecbc252ac8f7f06cca68aba88508ddfde3a6a34feb1678ad1694b4f88",
        "arm": "292588ea9f89705511c93a39687fe5e26767bd85b5085a1be9a2a1563c1e28fa",
        "ppc64le": "11bbb554976ce22aeb21b308bb5683f98941db81725d2b280d8b943b6ada8036",
        "s390x": "dfb031400c1215c0bd673d5e25bcd300a5af903fbfed10b5cb8b3ffc03bcecb6",
    },
    "v4.3.0": {
        "amd64": "86584a54def73570558f66f5111cc53dfed56689637ae32c1201205d494f54fb",
        "arm64": "31c5794dd55c66a51e6b7d2e2ac7a114ae8b1de41ff1d9ba51748ac973b06a08",
        "arm": "467c353e4a1ab57412dd00c6bfb1a6a2ab4a57d775506f8b38c58502c0ba6861",
        "ppc64le": "5ff7d5ea6f396c15f3cfda3fb8ecb0ecb1b11bc283785adeb55eefb2c62e0bda",
        "s390x": "ab637a6a1c3d457b9975726c100d74e484e579d5462c4fd2e3bfcfa97df57cd7",
    },
    "v4.2.4": {
        "amd64": "c306b46f719b0a4da32d0f78ee21bf90ce8d602f15b22ab753f0674d1670a7f3",
        "arm64": "564de2191b881e9f71b5606b25345821ea1682f06ab90499d3ab22b530176da1",
        "arm": "894e901f7daaf9b458baad7b5c685bfeef49070d7d53f99687bd5846a6c13639",
        "ppc64le": "5c00073e9d493de201384bb7eb19d60615bd7c39db52148473e8ce6da84bc70a",
        "s390x": "5396a35fca5fa46e5614140363f389ce66f96886c1b25f256d9e3028299422fa",
    },
    "v3.22.1": {
        "amd64": "896cf63684a42a723d61c980369d6e588330927deb13676ca0d85006dd6da0ea",
        "arm64": "4c29e286b279d170fa048ddfc3e4e83dcd069ca9c52102766ba672d7149e694d",
        "arm": "a1563d16cf9537db798116d5647ad0ef8cf963fa7cbbe24d8db534250273cccc",
        "ppc64le": "48a9711b69f0b63504f710ba18766dd5f5424fe380e67304aa3c89e1c0b97b5c",
        "s390x": "ef039296b433a0a6965fdf55f4ee0ad50e3d94bb70a77e4896309406c9bc8fab",
    },
    "v3.22.0": {
        "amd64": "1e4ab49e429626cf6c6958d914248b78c9730803c2751b87627e171dc800e7bb",
        "arm64": "f14e804dfee240f55525b667488fe9adca349e63e00c9af634c0beb1421ac310",
        "arm": "695793765e2246824a7c859562886f79b1cc89d99420c89f940590440f7129ee",
        "ppc64le": "fa304f47163a19f4e275fe4e890465a8a80a6f26ce4772c6b1649355c995def1",
        "s390x": "3f2cfd73018dcb9c25814e89c5946a362a8b951d8cce03152138d8cb28abcbff",
    },
}


# --- variables ---------------------------------------------------------------


class HelmChecksum(BaseModel):
    """One ``checksums`` override row: version, architecture, digest.

    The inventory form of a checksum pin, mirroring the shipped
    :data:`HELM_CHECKSUMS` cell as a flat, hashable entry
    (:attr:`HelmVars.checksums` is a tuple of these). ``sha256`` is the bare hex
    digest -- no ``sha256:`` prefix.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The release the digest belongs to (``helm_version`` form, ``v4.3.2``).
    version: str
    #: The ``get.helm.sh`` architecture name (eg ``amd64``, ``arm64``).
    arch: str
    #: The published tarball's ``sha256`` hex digest.
    sha256: str

    @field_validator("version")
    @classmethod
    def _version_non_empty(cls, value: str) -> str:
        """An empty version would key a lookup nothing can set."""
        if not isinstance(value, str) or not value:
            raise ValueError(f"helm_checksums version must be non-empty, got {value!r}")
        return value

    @field_validator("sha256")
    @classmethod
    def _sha256_hex(cls, value: str) -> str:
        """A digest that is not 64 hex digits can never verify."""
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdefABCDEF" for c in value)
        ):
            raise ValueError(
                f"helm_checksums sha256 must be a 64-character hex digest, got {value!r}"
            )
        return value


class HelmVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``helm`` role mapping, so ``version`` there sets
    :attr:`version`. The model is frozen, so it stays hashable through the
    deploy body (a :class:`~reemote.rolevars.VarsSet` is closed over by the
    factory). Unlike :mod:`reemote.roles.kubectl`, ``arch`` is a plain default:
    the role reads no ``ansible_architecture``, so the body needs no architecture
    fact barrier.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The release to install (``helm_version``); must be one of
    #: :data:`HELM_CHECKSUMS`' keys or pinned via :attr:`checksums`
    #: (deviation D5/D8).
    version: str = DEFAULT_VERSION
    #: The platform (``helm_platform``), frozen to ``linux`` (deviation D1).
    platform: str = DEFAULT_PLATFORM
    #: The architecture (``helm_arch``) ``get.helm.sh`` publishes under.
    arch: str = DEFAULT_ARCH
    #: The download repository (``helm_repo_path``).
    repo_path: str = DEFAULT_REPO_PATH
    #: Where the binary lands (``helm_bin_path``).
    bin_path: str = DEFAULT_BIN_PATH
    #: The parent of the dedicated, version-suffixed staging directory the
    #: tarball is extracted into (deviation D4).
    staging_dir: str = DEFAULT_STAGING_DIR
    #: Whether the copy into ``bin_path`` elevates (the role's task-level
    #: ``become: true``); maps to ``sudo=`` on that request (deviation D7).
    privilege_escalate: bool = True
    #: Extra ``sha256`` pins merged over :data:`HELM_CHECKSUMS`, overriding a
    #: shipped digest for the same ``(version, arch)`` pair.
    checksums: tuple[HelmChecksum, ...] = ()

    @field_validator("version", "platform", "arch")
    @classmethod
    def _name_plain(cls, value: str) -> str:
        """A path-shaped component would corrupt the URL and the staging path."""
        if not isinstance(value, str) or not value:
            raise ValueError(f"helm name component must be a non-empty string, got {value!r}")
        if "/" in value or value.startswith("."):
            raise ValueError(f"helm name component must be plain, got {value!r}")
        return value

    @field_validator("repo_path")
    @classmethod
    def _repo_path_form(cls, value: str) -> str:
        """Non-empty, no trailing slash (the URL join supplies the separator)."""
        if not isinstance(value, str) or not value:
            raise ValueError(f"helm_repo_path must be a non-empty string, got {value!r}")
        return value.rstrip("/")

    @field_validator("bin_path", "staging_dir")
    @classmethod
    def _path_absolute(cls, value: str) -> str:
        """A relative destination or staging root would land beside the daemon's cwd."""
        if not isinstance(value, str) or not value:
            raise ValueError(f"helm path must be a non-empty string, got {value!r}")
        if not value.startswith("/"):
            raise ValueError(f"helm path must be absolute, got {value!r}")
        return value.rstrip("/")

    @field_validator("bin_path")
    @classmethod
    def _bin_path_not_dir(cls, value: str) -> str:
        """``bin_path`` names a file, so it keeps a basename of its own."""
        if not value.rsplit("/", 1)[-1]:
            raise ValueError(f"helm_bin_path must name a file, got {value!r}")
        return value


# --- pure helpers ------------------------------------------------------------


def helm_url(repo_path: str, version: str, platform: str, arch: str) -> str:
    """The role's ``unarchive`` URL formula (``tasks/main.yml``).

    ``{{ helm_repo_path }}/helm-{{ helm_version }}-{{ helm_platform }}-{{ helm_arch }}.tar.gz``.
    """
    return f"{repo_path}/helm-{version}-{platform}-{arch}.tar.gz"


def staging_for(staging_dir: str, version: str) -> str:
    """The dedicated extraction directory for one release (deviation D4).

    ``<staging_dir>/helm-<version>`` -- version-scoped, so a version change
    extracts into a fresh directory rather than over ``chmod -R``-ing the old
    binary's home.
    """
    return f"{staging_dir}/helm-{version}"


def staged_binary(staging: str, platform: str, arch: str) -> str:
    """The tarball's member path, relative to the extraction directory.

    ``get.helm.sh`` tarballs extract to ``<platform>-<arch>/helm``, so under the
    staging directory the binary lands at ``<staging>/<platform>-<arch>/helm``.
    """
    return f"{staging}/{platform}-{arch}/helm"


def resolve_checksum(hv: HelmVars, version: str, arch: str) -> str:
    """The ``sha256`` for one ``(version, arch)``: overrides over the table.

    :attr:`HelmVars.checksums` rows win over :data:`HELM_CHECKSUMS`, so an
    inventory can pin a release the module does not ship (deviation D5). A
    missing pair fails fast with ``ValueError`` before any fetch -- the role
    verified nothing, and an unverified download is the one outcome this
    migration refuses to reproduce.
    """
    for row in hv.checksums:
        if row.version == version and row.arch == arch:
            return row.sha256
    try:
        return HELM_CHECKSUMS[version][arch]
    except KeyError:
        raise ValueError(
            f"no sha256 checksum for helm {version} {arch}: it is not in the shipped "
            "HELM_CHECKSUMS table; pin it with a helm checksums entry"
        ) from None


# --- the deploy stages (each returns a per-host generator) --------------------


def install(hv: HelmVars) -> Deploy:
    """The role's two install tasks, in task order (``tasks/main.yml``).

    ``files.unarchive`` extracts the checksum-pinned tarball control-side into
    the dedicated staging directory (deviation D3/D4), with ``mode="0755"`` on
    the extracted tree and ``creates`` set to the staged binary so a converged
    host never re-fetches. ``files.copy`` then moves that binary to
    ``bin_path`` (the ``remote_src`` + ``become: true`` task): a file ``dest``
    renames, ``overwrite=True`` is the sha1 content gate standing in for the
    role's ``when: helm_download is changed`` (deviation D6), and ``sudo``
    follows :attr:`HelmVars.privilege_escalate` (deviation D7). The extraction
    into ``/tmp`` stays unprivileged.
    """
    url = helm_url(hv.repo_path, hv.version, hv.platform, hv.arch)
    staging = staging_for(hv.staging_dir, hv.version)
    staged = staged_binary(staging, hv.platform, hv.arch)
    checksum = resolve_checksum(hv, hv.version, hv.arch)

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            files.unarchive,
            name=f"Unpack {url} into {staging}.",
            src=url,
            dest=staging,
            creates=staged,
            mode="0755",
            checksum=f"sha256:{checksum}",
        )
        yield op(
            files.copy,
            name=f"Copy {staged} to {hv.bin_path}.",
            src=staged,
            dest=hv.bin_path,
            overwrite=True,
            mode="0755",
            sudo=hv.privilege_escalate,
        )

    return deploy


# --- the factory --------------------------------------------------------------


def helm(v: VarsSet[HelmVars]) -> Deploy:
    """Install a checksum-pinned helm binary on Ubuntu hosts.

    Args:
        v: The role's resolved variables, one :class:`HelmVars` per host
            (``resolve_vars(HelmVars, "helm", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. Publishes nothing, so the
        generator returns ``None``; a composing role sequences it with
        ``yield from scoped("helm", helm(v))(host)``.

    Raises:
        ValueError: A role variable is invalid (raised by pydantic while the
            variables are resolved), or the configured version/architecture has
            no pinned digest. The latter surfaces at the head of the body,
            before a single byte is fetched -- no unverified download ever
            starts.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        # Fail fast on the checksum before any barrier, so an unpinned release
        # never reaches the fetch (deviation D5).
        resolve_checksum(hv, hv.version, hv.arch)

        # The role's ``stat`` of the binary path, in-stream.
        exists = yield fact(files.FileExists, hv.bin_path)
        if exists:
            # The role's ``{{ helm_bin_path }} version`` command: an ``act``
            # barrier whose ``Result`` is data, so a version-matched host
            # returns without installing (deviation D2).
            result = yield act(
                Shell, command=f"{hv.bin_path} version", name="helm version"
            )
            if hv.version in result["stdout"]:
                return

        yield from install(hv)(host)

    return deploy


__all__ = [
    "HelmChecksum",
    "HelmVars",
    "helm",
]
