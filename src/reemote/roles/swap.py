# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The swap file role (migrated from ``geerlingguy.swap``).
#
# Manages one swap file on Ubuntu hosts: its ``/etc/fstab`` entry (the role's
# ``mount`` declaration, ``name: none`` so the line is keyed by the device), the
# file itself at the wanted size (``dd``, mode ``0600`` root:root, ``mkswap``),
# its activation (``swapon``, and ``swapoff`` before a removal or a resize), and
# ``vm.swappiness`` in ``/etc/sysctl.d/90-swap.conf``. :func:`swap` is the whole
# task list -- ``tasks/main.yml`` plus its ``check-size``/``disable``/``enable``
# includes -- and the role's seven defaults are :class:`SwapVars`, bound from
# the inventory entry's ``swap`` role mapping by
# :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. A host outside the family
# returns before its first ``yield`` and emits no requests, no barriers and no
# log lines at all. The upstream role carries no ``include_vars`` dispatch and
# no per-distribution tables -- it is one flat task list of Linux-generic
# commands -- so scoping to Ubuntu costs nothing in shape: no
# ``LinuxDistribution`` barrier exists (the nfs and glusterfs precedent), and
# the paths the role writes (``/swapfile``, ``/etc/fstab``,
# ``/etc/sysctl.d/90-swap.conf``) are the same ones Ubuntu uses.
#
# The stream is one request-and-barrier sequence in the role's task order. Each
# barrier closes the wave it lands in, and a wave with no records is not
# appended, so the wave count is host-dependent (design doc section 5.7):
#
# * ``server.fstab`` -- the ``mount`` task, present or absent with
#   :attr:`SwapVars.state`.
# * ``fact(files.File)`` -- ``check-size.yml``'s ``stat`` register and
#   ``disable.yml``'s ``removes:`` gate in a single read. A missing file or a
#   differing size means the disable branch runs.
# * :func:`disable` -- ``fact(hardware.SwapEnabled)``, then
#   ``act(Shell, swapoff)`` when that read says swap is active, then the
#   ``files.file(present=False)`` request.
# * :func:`enable` -- the ``present`` path: ``act(Shell, dd)`` when the size
#   differs, the ``files.file`` mode/owner request, then ``act(mkswap)`` and
#   (unless :attr:`SwapVars.test_mode`) ``act(swapon)`` on that same condition,
#   then the sysctl directory and the ``sysctl.setting`` request.
#
# That is five waves for a fresh ``present`` host, seven when a wrongly sized
# file is active (the swap-state read and the ``swapoff`` it gates add one
# wave each), four for an ``absent`` host whose file is still active, and two
# for either converged case -- the fstab barrier wave, then one wave of
# no-change requests.
#
# There is **no checkpoint**: the role ships no ``handlers/main.yml``, and no
# later decision depends on what an earlier request did -- the size comparison
# comes from the ``files.File`` barrier, and the act results are only checked
# for failure. :func:`swap` returns ``None``: no composing role consumes an
# output, so there is no outputs dataclass (§17.2).
#
# Prerequisites: none beyond reemote itself -- the role templates nothing.
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: the host-family guard is the body's opening ``if``, not a ``when=`` on
#   every request: a host outside the ``"Ubuntu"`` family returns before it
#   yields anything, so it emits no requests and no log entries at all.
# * D2: the swapoff gate is :class:`~reemote.modules.hardware.SwapEnabled`
#   (``swapon --show`` printed a row) instead of
#   ``ansible_facts.memory_mb['swap']['total'] > 0`` -- the same question,
#   asked without reading the whole memory layout.
# * D3: ``shell`` + ``creates:`` on the ``dd`` task becomes the size comparison
#   the body already made. Because the disable branch removes the file whenever
#   the sizes differ, ``dd`` runs exactly when upstream's ``creates:`` gate
#   would have let it, and ``mkswap``'s ``when: swap_file_create is changed``
#   is the same condition again.
# * D4: ``when: not ansible_check_mode`` on the permissions task is dropped: a
#   ``run_waves(dry=True)`` pass applies nothing at all, so there is no
#   half-created file to protect.
# * D5: ``swap_file_create_command``'s Jinja default is not carried as a
#   string. :attr:`SwapVars.create_command` defaults to ``None`` -- "the
#   generated ``dd``" -- which shell-quotes the path (upstream interpolates it
#   bare); an explicit command is used verbatim, as upstream does.
# * D6: type gates move to :class:`SwapVars`. The role's ``'512'``/``'60'``
#   string defaults coerce to ``int``, and anything else is a pydantic
#   rejection at ``resolve_vars`` time -- before any host is contacted -- rather
#   than a mid-play failure of ``dd count='abc'``.
# * D7: a non-regular file at :attr:`SwapVars.file_path` reads as size ``0``
#   and is removed with ``files.file(present=False, force=True)``, the shape of
#   Ansible's ``file: state=absent`` for any path type. Upstream would compare
#   the inode size of a directory and fail later, inside ``dd``.
# """

import posixpath
import shlex
from collections.abc import Generator
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..actions import Result, Shell
from ..core import op
from ..hosts import CommandError, Host
from ..modules import files, hardware, server, sysctl
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, act, fact

#: :attr:`SwapVars.swappiness` is written under this key --
#: ``tasks/enable.yml``'s ``sysctl`` task.
SWAPPINESS_KEY = "vm.swappiness"

#: The mode and owner ``tasks/enable.yml`` forces on the swap file: ``dd``
#: leaves the umask's default, and a world-readable swap file is a security
#: problem (the kernel refuses to activate it on some configurations).
SWAP_FILE_MODE = "0600"

#: ``tasks/enable.yml``'s directory task: ``state: directory``, root:root,
#: ``0755`` -- the mode ``sysctl.d`` drop-ins expect their parent to carry.
SYSCTL_DIR_MODE = "0755"

#: The divisor behind ``check-size.yml``'s ``stat.size / 1024 / 1024``.
BYTES_PER_MB = 1024 * 1024


class SwapVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``swap`` role mapping, so ``swap_file_path`` there sets
    :attr:`file_path`. The model is frozen, so it stays hashable through the
    deploy body (:class:`~reemote.rolevars.VarsSet` is closed over by the
    factory).

    Both states are implemented: :attr:`state` ``"absent"`` deactivates and
    deletes the file and drops the fstab line, ``"present"`` manages it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``swap_file_path``: the swap file to manage.
    file_path: str = "/swapfile"
    #: ``swap_file_size_mb``: the size in megabytes ``dd`` writes. A differing
    #: existing file is deactivated, removed and recreated (the role's
    #: ``check-size`` + ``disable`` + ``enable`` chain).
    size_mb: int = Field(default=512, gt=0)
    #: ``swap_swappiness``: the ``vm.swappiness`` value persisted and applied.
    swappiness: int = Field(default=60, ge=0, le=200)
    #: ``swap_sysctl_file``: the drop-in :attr:`swappiness` is written to.
    sysctl_file: str = "/etc/sysctl.d/90-swap.conf"
    #: ``swap_file_state``: ``present`` or ``absent``, straight from the role.
    state: Literal["present", "absent"] = "present"
    #: ``swap_file_create_command``: ``None`` means the generated ``dd`` of
    #: :func:`dd_command` (D5); anything else runs verbatim.
    create_command: str | None = None
    #: ``swap_test_mode``: when true, the two activation commands (``swapon``
    #: and ``swapoff``) are skipped and everything else still runs. The one
    #: role default the upstream README does not document -- it is carried
    #: from ``defaults/main.yml`` alone.
    test_mode: bool = False

    @field_validator("file_path", "sysctl_file")
    @classmethod
    def _must_be_absolute(cls, value: str) -> str:
        """Reject a relative path: it would silently manage elsewhere."""
        if not value.startswith("/"):
            raise ValueError(f"must be an absolute path, got {value!r}")
        return value

    @field_validator("create_command")
    @classmethod
    def _none_or_non_empty(cls, value: str | None) -> str | None:
        """An empty override is a mistake, not "use the generated dd"."""
        if value is not None and not value:
            raise ValueError("create_command must be a non-empty string or null")
        return value


def dd_command(path: str, size_mb: int) -> str:
    """The role's default create command, ``swap_file_create_command``.

    Pure. Shell-quotes the path -- the one place the generated command differs
    from upstream's Jinja interpolation, which wrote it bare (D5).
    """
    return f"dd if=/dev/zero of={shlex.quote(path)} bs=1M count={size_mb}"


def existing_size_mb(stat: dict[str, Any] | bool | None) -> int:
    """``check-size.yml``'s two ``set_fact`` tasks, as one pure function.

    The ``files.File`` fact answers the dict the ``stat`` register carried,
    ``None`` when the path does not exist and ``False`` when it is not a
    regular file; both of those are the role's ``swap_file_existing_size_mb: 0``
    branch, and an existing file's byte size divides by
    :data:`BYTES_PER_MB` exactly as ``/ 1024 / 1024 | int`` did.
    """
    if not stat:
        return 0
    return int(stat["size"]) // BYTES_PER_MB


def _check(host: Host, command: str, result: Result) -> None:
    """Turn a failed action result into the library's typed error.

    A barrier never raises on a non-zero exit -- the :class:`~reemote.actions.Result`
    is data -- so the body checks it and raises
    :class:`~reemote.hosts.CommandError` itself, which is what ``command:`` and
    ``shell:`` tasks did in Ansible (the nfs precedent).
    """
    if result["exit_status"] != 0:
        raise CommandError(host.name, command, result["exit_status"], result["stderr"])


def disable(hv: SwapVars, *, exists: bool) -> Deploy:
    """``tasks/disable.yml``: deactivate the file, then remove it.

    ``exists`` is the ``removes:`` gate the task file puts on ``swapoff``, read
    from the body's ``files.File`` barrier. With a file to deactivate and
    test mode off, the branch asks :class:`~reemote.modules.hardware.SwapEnabled`
    whether any swap is active at all before running ``swapoff`` (D2) -- an
    inactive host gets no command. The removal request always runs, and
    ``force=True`` clears a non-file at the path the way Ansible's
    ``state: absent`` would (D7).
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if exists and not hv.test_mode:
            active = yield fact(hardware.SwapEnabled, name="Is swap active?")
            if active:
                command = f"swapoff {shlex.quote(hv.file_path)}"
                result = yield act(
                    Shell,
                    command=command,
                    name="Disable swap (if configured).",
                    sudo=True,
                )
                _check(host, command, result)
        yield op(
            files.file,
            name="Ensure swap file doesn't exist (if configured).",
            path=hv.file_path,
            present=False,
            force=True,
            sudo=True,
        )

    return deploy


def enable(hv: SwapVars, *, created: bool) -> Deploy:
    """``tasks/enable.yml``: build the file, activate it, tune swappiness.

    ``created`` is the body's size comparison -- upstream's ``creates:`` gate
    and the ``when: swap_file_create is changed`` /
    ``when: mkswap_result is changed`` pair collapsed into the one read that
    already decided the disable branch (D3). So ``dd``, ``mkswap`` and
    ``swapon`` run together on the pass that (re)builds the file and on no
    other, while the permissions request runs on every ``present`` pass: it is
    ``files.file``'s own fact comparison that makes it a no-op once the file is
    ``0600`` root:root. ``swapon`` alone respects
    :attr:`SwapVars.test_mode`, as upstream spells it.

    The directory request is kept even though ``sysctl.setting``'s install
    ``mkdir -p``s the destination's parent: the role asks for ``0755``
    root:root explicitly, and only ``files.directory`` compares that.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if created:
            command = hv.create_command or dd_command(hv.file_path, hv.size_mb)
            result = yield act(
                Shell,
                command=command,
                name="Ensure swap file exists.",
                sudo=True,
            )
            _check(host, command, result)

        yield op(
            files.file,
            name="Set permissions on swap file.",
            path=hv.file_path,
            user="root",
            group="root",
            mode=SWAP_FILE_MODE,
            sudo=True,
        )

        if created:
            command = f"mkswap {shlex.quote(hv.file_path)}"
            result = yield act(
                Shell,
                command=command,
                name="Make swap file if necessary.",
                sudo=True,
            )
            _check(host, command, result)
            if not hv.test_mode:
                command = f"swapon {shlex.quote(hv.file_path)}"
                result = yield act(
                    Shell,
                    command=command,
                    name="Run swapon on the swap file.",
                    sudo=True,
                )
                _check(host, command, result)

        yield op(
            files.directory,
            name="Ensure sysctl config directory exists.",
            path=posixpath.dirname(hv.sysctl_file),
            user="root",
            group="root",
            mode=SYSCTL_DIR_MODE,
            sudo=True,
        )
        # ``sysctl_set=True`` is the operation's own default -- the role spells
        # it explicitly because Ansible's sysctl module defaults it to false.
        yield op(
            sysctl.setting,
            name="Set swappiness.",
            key=SWAPPINESS_KEY,
            value=hv.swappiness,
            sysctl_file=hv.sysctl_file,
            sysctl_set=True,
            sudo=True,
        )

    return deploy


def swap(v: VarsSet[SwapVars]) -> Deploy:
    """Manage one swap file and ``vm.swappiness`` on Ubuntu hosts.

    Args:
        v: The role's resolved variables, one :class:`SwapVars` per host
            (``resolve_vars(SwapVars, "swap", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves` that returns ``None`` for every
        host it runs -- no composing role in the corpus consumes an output
        (§17.2).

    Raises:
        CommandError: One of the four elevated commands (``dd``, ``mkswap``,
            ``swapon``, ``swapoff``) exited non-zero. The commands run in body
            order, so a failure surfaces in the wave that carries it.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return None

        present = hv.state == "present"

        # Wave 0: the role's central declaration. Ansible's ``mount`` with
        # ``name: none`` is the fstab line ``server.fstab`` keys by device.
        yield op(
            server.fstab,
            name="Manage swap file entry in fstab.",
            device=hv.file_path,
            mountpoint="none",
            fs_type="swap",
            options=("sw",),
            dump=0,
            passno=0,
            present=present,
            sudo=True,
        )

        # The barrier that closes wave 0 reads the file *after* the fstab write
        # was applied -- it is check-size.yml's stat register and disable.yml's
        # ``removes:`` gate at once.
        stat = yield fact(
            files.File, path=hv.file_path, name="Check if swap file exists"
        )
        size_matches = existing_size_mb(stat) == hv.size_mb
        if not present or not size_matches:
            yield from disable(hv, exists=stat is not None)(host)
        if present:
            yield from enable(hv, created=not size_matches)(host)

        return None

    return deploy


__all__ = ["SwapVars", "swap"]
