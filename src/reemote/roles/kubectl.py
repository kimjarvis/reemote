# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The kubectl role (migrated from ``andrewrothstein.kubectl``).
#
# Installs `kubectl <https://dl.k8s.io/>`_, the Kubernetes command-line tool, on
# Ubuntu hosts: the pinned-version binary download with its ``sha256`` checksum
# verification, and the ``kubectl`` symlink beside it. :func:`kubectl` is the
# whole task list and :class:`KubectlVars` is ``defaults/main.yml``, bound from
# the inventory's ``kubectl_``-prefixed keys by
# :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. A host outside the target family
# returns before its first ``yield`` and emits no requests, no barriers and no
# log lines at all.
#
# The stream is one deploy body carrying a single ``fact`` barrier and three
# requests -- no checkpoint, no handler, no ``act``/``control`` read, because
# the role ships no ``handlers/main.yml`` and nothing between its tasks reads
# remote state. The first wave closes the ``fact(server.Arch)`` gate (the
# role's ``ansible_facts.architecture`` lookup), the second carries the three
# requests in the role's task order: ``files.directory`` for the install
# directory, ``files.download`` for the checksum-pinned binary, ``files.link``
# for the symlink. A converged host re-runs the same two waves -- the fact read
# and the requests are idempotent, but the ``Arch`` gate never disappears.
#
# Deviations from the Ansible role, all deliberate:
#
# * **D1** the ``include_vars`` OS dispatch freezes to Ubuntu/linux: the role's
#   ``vars/Windows.yml`` (and its ``.exe`` suffixes) and the darwin table half
#   are statically skipped, and the family guard drops off-target hosts silently.
#   reemote drives Linux hosts only.
# * **D2** the binary is fetched **control-side** by :func:`~reemote.modules.files.download`
#   (checksum-verified, then spool-installed); Ansible's ``get_url`` downloads
#   on the target. The control machine needs egress to ``dl.k8s.io`` (or the
#   configured mirror); the host downloads nothing.
# * **D3** idempotence is content-only: a converged pass still fetches the URL
#   control-side to compare, and a pure permission drift on the installed
#   binary is not re-applied (the install read-back short-circuits on matching
#   content, as ``get_url``'s own checksum comparison does).
# * **D4** the checksum table ships only the recent Linux releases
#   (:data:`LINUX_CHECKSUMS`, v1.36.x and v1.37.x) instead of the role's full
#   four-thousand-line default; :attr:`KubectlVars.checksums` extends or
#   overrides it per inventory, and an unshipped version with no override fails
#   fast with ``ValueError`` -- the waves-model stand-in for Ansible's
#   undefined-key error on ``kubectl_checksums[ver]``.
# * **D5** ``kubectl_arch_map`` freezes to :data:`ARCH_MAP` (it was an
#   overridable default); an ``uname -m`` outside the map fails fast.
# * **D6** ``kubectl_privilege_escalate`` maps to ``sudo=`` on each of the three
#   requests (the role's ``become:`` with ``become_user: root``); the run-as
#   identity comes from the inventory ``privilege`` section, never from an
#   operation argument. The ``test.yml`` smoke command (``kubectl version``) is
#   outside the role body, as it is outside ``tasks/main.yml``.
# """

from collections.abc import Generator
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..hosts import Host
from ..modules import files, server
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, fact

# --- role constants (defaults/main.yml + vars/main.yml, Linux only) -----------

#: The default release to install (``kubectl_ver``).
DEFAULT_VER = "v1.37.2"

#: The download mirror (``kubectl_mirror``); ``kubectl_os`` is frozen to
#: ``linux`` and ``kubectl_bin`` to ``kubectl`` (deviation D1).
DEFAULT_MIRROR = "https://dl.k8s.io/release"

#: ``kubectl_arch_map``, frozen (deviation D5): ``uname -m`` output to the
#: architecture name ``dl.k8s.io`` publishes under.
ARCH_MAP: dict[str, str] = {
    "aarch64": "arm64",
    "aarch32": "arm",
    "armv6l": "arm",
    "armv7l": "arm",
    "arm64": "arm64",
    "ppc64le": "ppc64le",
    "s390x": "s390x",
    "x86_64": "amd64",
}

#: ``kubectl_checksums`` for Linux, recent releases only (deviation D4):
#: version to architecture to the ``sha256`` hex digest of the published
#: binary, as listed in the role's ``defaults/main.yml``.
LINUX_CHECKSUMS: dict[str, dict[str, str]] = {
    "v1.36.0": {
        "amd64": "123d8c8844f46b1244c547fffb3c17180c0c26dac9890589fe7e67763298748e",
        "arm": "bd1ab1f3d3cb7dec7c46799355e82717a6519fa83708752c2be88fe5c378681f",
        "arm64": "9f9d9c44a7b5264515ac9da5991584e2395bd50662e651132337e7b4d0c56f8f",
        "s390x": "06642a2a92a651f5813585ef4296c79ab9707941756947b57ce1e901f201d999",
        "ppc64le": "c0a8afcb7899202969000debabcf98013c902c42765537e96c52da0e31be3032",
    },
    "v1.36.1": {
        "amd64": "629d3f410e09bf49b64ae7079f7f0bda1191efed311f7d37fdbab0ad5b0ec2b7",
        "arm": "0f5f7530e6ac3b887e01c963b5eea477cc6f590ec5e3a4e23a5485e6d9227761",
        "arm64": "59f7ee8e477fae658447607dc3c8790ac17a1b016c01c622c12070e969e2d4e7",
        "s390x": "8e47a105c6d762bbbe6af32d9087232f444d6325c32671c6950404c207abfda2",
        "ppc64le": "b6acc7be1bfd5f9a10cd6b03b8e6c25a96c0f9a127efa31932752d3ffe97d909",
    },
    "v1.36.2": {
        "amd64": "1e9045ec32bea85da43de85f0065358529ea7c7a152eca78154fba5b58c27d82",
        "arm": "ded590966255c7059b9f7aa9b1ee4a16ffba79b6c4e59c464f151442c46bb63c",
        "arm64": "c957eb8c4bea27a3bb35b269edd9082e27f027f7b76b20b5bf4afebc726c6d3e",
        "s390x": "d0e5bddf4d896acccb398f9d458dd4e15b293be8a29f23e7bbb87f8462821c01",
        "ppc64le": "8afb5a81a3dbc7213c4bb36437fde5e3463758f0f18888568cd6cadcb9bdf981",
    },
    "v1.36.3": {
        "amd64": "ebbd080e7c2e275093b55915722043257eb24004363e20acb3c4d71919f88336",
        "arm": "451f3f3805515f3055a4eca918bf89c9aea730bf1a7fc1f969715e5866b0b65b",
        "arm64": "3d86f24401c41ae5a46ac50eef8865fe891d3647d324a0836f6c63757a126e62",
        "s390x": "9fa5963b7275b7447c6a7c1f468b7e5a5164fb6f89e23da99ab42d2ea5b81a50",
        "ppc64le": "aee56eaced85e2eab925e86c05e3c222c4611ce045073d110f930abd3da8b193",
    },
    "v1.36.4": {
        "amd64": "8b8f088da2dab964f853b38464033b1be15ede2839eca751482357c45abdd05a",
        "arm": "a2040179b1df0d2036c45bf03e5731a3709f39e3649f54615dc970cdc695fa36",
        "arm64": "0ecf44450ee6063bf19dd166a103ee6df4a9034455c2abce626e6eea657d73fb",
        "s390x": "e04e4559c59101af732aa65a6157644f4b845bcd10c602e32dfcf79d91d1918e",
        "ppc64le": "5b88a500de9335314dd2634fa7d7a7ca19b19e9b4236f7343060c09fd5d54658",
    },
    "v1.36.5": {
        "amd64": "33bc88a24c3b09cf55bfd59e6ca977881b61fc3f25ab65bc80c65b430248648e",
        "arm": "a0ef004be1ca4eb97697e0192d29fd56cba21a110efa6c2b595c1d1490bde97c",
        "arm64": "88fc1aca8fd0c1b44fe379f67c39bd6858bdfc50241dc55775f84d0f5b31da3a",
        "s390x": "2774bb4c23ffeb4c17e5e77bc968aa8552460cdd6a5af99be31074f4b6d73cd7",
        "ppc64le": "8233ea169dde3c01837da30a9b098b62b3bab2f8be6dcf9993dee74740a7aad0",
    },
    "v1.36.6": {
        "amd64": "29818b6729dc9262b692307470d0558d027725e738be5dd47c760261868a2634",
        "arm": "b4dd13a7a76680af901fe290c901c2b7115c30db37169d6af3aa5d61fc3618fc",
        "arm64": "35507ef408c3d38849e5d3203e18f8e861db822aa664101a9ced0869edd06046",
        "s390x": "e73ef52ce7c925e8f75e07a14095d9d2fef2a620ae8475d0dbc396021cd2b2f6",
        "ppc64le": "ecb6f471f8a7cae0d4317dfd5e82626b35611bb516e91b32d50021182d53935d",
    },
    "v1.37.0": {
        "amd64": "6129359f4e1f3848a5572ccb0b26cf28b8ca08cef38c95a765b2f64a2c961a2f",
        "arm": "ae3f0a64b09196f6c17bb6e60ba026f77eb6f58d13c0d8b77109069bd7e5c499",
        "arm64": "922df28df248cc00a9e025f947704f1d1482de64ece54cfe57e61f19eaf1eef3",
        "s390x": "40536f5ef0e174c244c98da362a0f5c4266443af81605a0ba298d43b57de7047",
        "ppc64le": "fadb00c89cea0b1cc98d3101e854b6872c9b98775e888df6c9847147bbc589b1",
    },
    "v1.37.1": {
        "amd64": "65691ff77eb6fa44c908b77a1082c9f092c3b9733b5cefabec0d1104890e21a8",
        "arm": "8af0113a5b03b13b923f530bc4177479799dee2a0c188d15bef0ebef5d0c6d75",
        "arm64": "ff749f4b78d9c4f1ec87307df9b50119ed819e2094aa9810cb9acffc3286c8c7",
        "s390x": "222798cd808e287cccd427718f9d76f7b3bff4e8601dca96b8bf5c371d3b59f6",
        "ppc64le": "50a33e7a7f0d1125099abbdba3c010a3cb497142f58ed8ea198949f738e79b82",
    },
    "v1.37.2": {
        "amd64": "c04487523e7aa7d1493f7808174c899db93d7a9cf57531420cd76b9bd415f8f4",
        "arm": "c405585c20de2401d9ba6639863e266b236d93d19ef34a38e27507312ef86a96",
        "arm64": "fcc16f63186659d9722014b7e02d5fdd2d4e669cb8c0ab66365d7d3b8ba03509",
        "s390x": "d9bdb97b0b98d5bdacbb70b222ca3dee8b1daec099a76a9ec5426355ff7e95af",
        "ppc64le": "8db0becad37414be960991eb3ba1eaa6b6844d4c7d672bab39276708860575e0",
    },
}


# --- variables ---------------------------------------------------------------


class KubectlChecksum(BaseModel):
    """One ``kubectl_checksums`` override row: version, architecture, digest.

    The inventory form of a checksum pin, mirroring the role's
    ``kubectl_checksums[ver]["linux"][arch]`` lookup as a flat, hashable entry
    (:attr:`KubectlVars.checksums` is a tuple of these). ``sha256`` is the bare
    hex digest -- no ``sha256:`` prefix.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The release the digest belongs to (``kubectl_ver`` form, ``v1.37.2``).
    version: str
    #: The ``dl.k8s.io`` architecture name (a value of :data:`ARCH_MAP`).
    arch: str
    #: The published binary's ``sha256`` hex digest.
    sha256: str

    @field_validator("version")
    @classmethod
    def _version_non_empty(cls, value: str) -> str:
        """An empty version would key a lookup nothing can set."""
        if not isinstance(value, str) or not value:
            raise ValueError(f"kubectl_checksums version must be non-empty, got {value!r}")
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
                f"kubectl_checksums sha256 must be a 64-character hex digest, got {value!r}"
            )
        return value


class KubectlVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``kubectl`` role mapping, so ``ver`` there sets
    :attr:`ver`. The model is frozen, so it stays hashable through the deploy
    body (a :class:`~reemote.rolevars.VarsSet` is closed over by the factory).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The release to install (``kubectl_ver``); must be one of
    #: :data:`LINUX_CHECKSUMS`' keys or pinned via :attr:`checksums`
    #: (deviation D4).
    ver: str = DEFAULT_VER
    #: The download mirror (``kubectl_mirror``); any ``dl.k8s.io``-shaped
    #: release tree works.
    mirror: str = DEFAULT_MIRROR
    #: Where the binary and its symlink land (``kubectl_install_dir``).
    install_dir: str = "/usr/local/bin"
    #: Whether the three requests elevate (``kubectl_privilege_escalate``);
    #: maps to ``sudo=`` per request (deviation D6).
    privilege_escalate: bool = True
    #: Extra ``sha256`` pins merged over :data:`LINUX_CHECKSUMS`, overriding
    #: a shipped digest for the same ``(version, arch)`` pair.
    checksums: tuple[KubectlChecksum, ...] = ()

    @field_validator("ver")
    @classmethod
    def _ver_non_empty(cls, value: str) -> str:
        """An empty or slash-bearing version would corrupt the URL and paths."""
        if not isinstance(value, str) or not value:
            raise ValueError(f"kubectl_ver must be a non-empty string, got {value!r}")
        if "/" in value or value.startswith("."):
            raise ValueError(f"kubectl_ver must be a plain release name, got {value!r}")
        return value

    @field_validator("mirror")
    @classmethod
    def _mirror_form(cls, value: str) -> str:
        """Non-empty, no trailing slash (the URL join supplies the separator)."""
        if not isinstance(value, str) or not value:
            raise ValueError(f"kubectl_mirror must be a non-empty string, got {value!r}")
        return value.rstrip("/")

    @field_validator("install_dir")
    @classmethod
    def _install_dir_absolute(cls, value: str) -> str:
        """A relative install directory would land beside the daemon's cwd."""
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"kubectl_install_dir must be a non-empty string, got {value!r}"
            )
        if not value.startswith("/"):
            raise ValueError(f"kubectl_install_dir must be absolute, got {value!r}")
        return value.rstrip("/")


# --- pure helpers ------------------------------------------------------------


def kubectl_url(mirror: str, ver: str, arch: str) -> str:
    """The role's ``kubectl_url`` formula for a Linux host (``vars/main.yml``).

    ``{{ kubectl_mirror }}/{{ kubectl_ver }}/bin/linux/{{ kubectl_arch }}/kubectl``
    -- ``kubectl_os`` frozen to ``linux``, ``kubectl_bin`` to ``kubectl``
    (deviation D1).
    """
    return f"{mirror}/{ver}/bin/linux/{arch}/kubectl"


def arch_for(machine: str) -> str:
    """Map ``uname -m`` output to the mirror's architecture name.

    The role's ``kubectl_arch: '{{ kubectl_arch_map[ansible_facts.architecture] }}'``
    lookup; an unmapped machine fails fast with the role's undefined-key
    meaning rather than inventing a URL (deviation D5).
    """
    try:
        return ARCH_MAP[machine]
    except KeyError:
        raise ValueError(
            f"unsupported machine architecture {machine!r}: kubectl_arch_map has no "
            f"entry (known: {', '.join(sorted(ARCH_MAP))})"
        ) from None


def resolve_checksum(hv: KubectlVars, ver: str, arch: str) -> str:
    """The role's ``kubectl_checksum`` lookup: overrides over the shipped table.

    :attr:`KubectlVars.checksums` rows win over :data:`LINUX_CHECKSUMS`, so an
    inventory can pin a release the module does not ship (deviation D4). A
    missing pair fails fast with ``ValueError`` before any fetch -- the waves
    model has no undefined-Jinja-key error, and an unverified binary download
    is the one outcome the role never produced.
    """
    for row in hv.checksums:
        if row.version == ver and row.arch == arch:
            return row.sha256
    try:
        return LINUX_CHECKSUMS[ver][arch]
    except KeyError:
        raise ValueError(
            f"no sha256 checksum for kubectl {ver} linux/{arch}: it is not in the "
            "shipped LINUX_CHECKSUMS table; pin it with a kubectl_checksums entry"
        ) from None


# --- the deploy stages (each returns a per-host generator) --------------------


def install(hv: KubectlVars, arch: str) -> Deploy:
    """The role's three tasks, in task order (``tasks/main.yml``).

    One request each: the install directory at ``0755`` (the ``file: state:
    directory`` task), the checksum-pinned binary download to
    ``kubectl-<ver>`` at ``0755`` (``get_url``, control-side -- deviation D2),
    and the ``kubectl`` symlink beside it (the ``file: state: link`` task).
    Each carries ``sudo=hv.privilege_escalate`` (deviation D6); the transfers
    themselves never elevate beyond the request's ``sudo`` flag.
    """
    url = kubectl_url(hv.mirror, hv.ver, arch)
    exe = f"{hv.install_dir}/kubectl-{hv.ver}"
    link = f"{hv.install_dir}/kubectl"
    checksum = resolve_checksum(hv, hv.ver, arch)

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            files.directory,
            name=f"Ensure install directory {hv.install_dir} exists.",
            path=hv.install_dir,
            mode="0755",
            sudo=hv.privilege_escalate,
        )
        yield op(
            files.download,
            name=f"Download {url}",
            url=url,
            dest=exe,
            mode="0755",
            checksum=f"sha256:{checksum}",
            sudo=hv.privilege_escalate,
        )
        yield op(
            files.link,
            name=f"Link {exe} to {link}.",
            path=link,
            target=exe,
            sudo=hv.privilege_escalate,
        )

    return deploy


# --- the factory --------------------------------------------------------------


def kubectl(v: VarsSet[KubectlVars]) -> Deploy:
    """Install a checksum-pinned kubectl binary and its symlink on Ubuntu hosts.

    Args:
        v: The role's resolved variables, one :class:`KubectlVars` per host
            (``resolve_vars(KubectlVars, "kubectl", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. Publishes nothing, so the
        generator returns ``None``; a composing role sequences it with
        ``yield from scoped("kubectl", kubectl(v))(host)``.

    Raises:
        ValueError: A role variable is invalid (raised by pydantic while the
            variables are resolved), the host's ``uname -m`` is outside
            :data:`ARCH_MAP`, or the configured version/architecture has no
            pinned digest. The last two surface in the body, before the three
            requests are emitted -- no unverified download ever starts.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        # The role's ``ansible_facts.architecture`` read, in-stream: the one
        # barrier, closing the first wave. ``arch_for``/``resolve_checksum``
        # fail fast on the result before any request is emitted.
        machine = yield fact(server.Arch)
        arch = arch_for(machine)

        yield from install(hv, arch)(host)

    return deploy


__all__ = [
    "KubectlChecksum",
    "KubectlVars",
    "kubectl",
]
