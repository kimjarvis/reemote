# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The NFS role (migrated from ``geerlingguy.nfs``).
#
# Installs the NFS server packages on Ubuntu hosts, creates the directories the
# role exports, writes ``/etc/exports`` from the role's template (root:root,
# mode ``0644``), starts and enables ``nfs-kernel-server`` when any export is
# configured, and re-exports the filesystems (``exportfs -ra``) whenever the
# exports file changes. :func:`nfs` is the whole task list and the handler,
# :class:`NfsVars` is ``defaults/main.yml``, bound from the inventory entry's
# ``nfs`` role mapping by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. A host outside the family
# returns before its first ``yield`` and emits no requests, no barriers and no
# log lines at all -- the Alpine and RedHat hosts in a shared inventory are
# never contacted. The role's RedHat/Fedora branches (``setup-RedHat.yml``,
# ``vars/RedHat.yml``, ``vars/Fedora.yml``) are out of scope, and because the
# vars split only by OS family -- not by release -- the single-OS scope
# collapses the ``include_vars`` dispatch to frozen module constants: no
# ``LinuxDistribution`` barrier exists (the nginx and memcached precedent).
#
# The stream is one request wave and two barriers (design doc section 18.14):
#
# 0. :func:`configure` -- the install (with the molecule cache refresh folded
#    in), one directory per unique export path, the rendered exports install
#    and its metadata companion (both notify), and the gated service
#    start/enable -- closed by ``checkpoint("exports done")``.
# 1. :func:`handler` -- ``reload nfs``, only on the hosts that checkpoint
#    selected. It is the corpus's one **command** handler: ``exportfs -ra`` is
#    an ``act`` barrier, so the handler wave carries a barrier line and no
#    request line, and an unnotified host yields nothing at all.
#
# **No facts at all.** Every input is a resolved variable or a frozen
# constant, and the exports render is a plain Python join (the template is a
# static comment header plus one line per export), so this role needs neither
# a ``fact`` barrier nor the ``reemote[templates]`` extra. The old example's
# two-phase shape -- one ``run()``, then ``changed_hosts`` on its report, then
# ``gather_action(Shell, ...)`` outside any deploy -- collapses into the body:
# the notification read is an in-stream checkpoint and the handler command is
# an in-stream ``act``.
#
# Prerequisites: none beyond reemote itself.
#
# Deviations from the Ansible role, all deliberate:
#
# * the host-family guard is the body's opening ``if``, not a ``when=`` on
#   every request: an off-target host emits no requests and no ``[skipped]``
#   log lines.
# * the molecule converge playbook's apt index refresh is folded into the
#   install request (D1): the role's own ``apt`` task carries no
#   ``update_cache``, but the pre_task does, and folding it keeps the role
#   self-contained on a fresh host.
# * the template renders with a Python join rather than Jinja2 (D2), which
#   reproduces it byte-for-byte under Ansible's whitespace settings.
# * the template task becomes two notifying requests (D3): ``files.install``'s
#   idempotence is content-only, so a root:root ``0644`` drift on unchanged
#   content would never converge, while Ansible's template module reports a
#   change on owner/group/mode drift too (the java/apache companion precedent).
# * the handler stays a bare command, so it is an ``act`` barrier and the body
#   raises its own :class:`~reemote.hosts.CommandError` (D4): a barrier result
#   is data and a non-zero exit never raises. No ``reload_handler`` --
#   ``systemd.service`` reloads only an already-running unit, so on the default
#   path (no exports, service deliberately stopped) it would *start*
#   ``nfs-kernel-server`` and never run ``exportfs``.
# * the RedHat/Fedora path is scoped out (D5), and the two ``nfs_rpcbind_*``
#   defaults that only it consumes are carried as inert fields so an inventory
#   written for the role still binds.
# * type gates move to :class:`NfsVars` (D6): a wrongly typed variable is a
#   pydantic rejection at ``resolve_vars`` time, before any host is contacted,
#   not a mid-play failure of the directory loop.
# """

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..actions import Shell
from ..core import op
from ..handlers import changed_hosts
from ..hosts import CommandError, Host
from ..modules import apt, files, systemd
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, act, checkpoint

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path) --------

#: ``vars/Debian.yml``'s ``nfs_server_daemon``, frozen: the service the
#: ``service`` task manages. The ``include_vars`` OS dispatch collapses to this
#: constant (Ubuntu is Debian-family); ``vars/RedHat.yml`` and
#: ``vars/Fedora.yml`` name ``nfs-server`` and are out of scope.
NFS_SERVER_DAEMON = "nfs-kernel-server"

#: ``tasks/setup-Debian.yml``'s apt task, frozen as a tuple.
NFS_PACKAGES = ("nfs-common", "nfs-kernel-server")

#: ``tasks/main.yml``'s template task destination.
EXPORTS_PATH = "/etc/exports"

#: ``templates/exports.j2``'s static comment header, byte-for-byte (the
#: template's first ten lines, each newline-terminated -- the file uses spaces,
#: not tabs, and ends without a newline after ``{% endfor %}``).
EXPORTS_HEADER = (
    "# /etc/exports: the access control list for filesystems which may be exported\n"
    "#   to NFS clients.  See exports(5).\n"
    "#\n"
    "# Example for NFSv2 and NFSv3:\n"
    "# /srv/homes       hostname1(rw,sync,no_subtree_check) "
    "hostname2(ro,sync,no_subtree_check)\n"
    "#\n"
    "# Example for NFSv4:\n"
    "# /srv/nfs4        gss/krb5i(rw,sync,fsid=0,crossmnt,no_subtree_check)\n"
    "# /srv/nfs4/homes  gss/krb5i(rw,sync,no_subtree_check)\n"
    "#\n"
)

#: ``handlers/main.yml``: the ``reload nfs`` handler's command, run elevated.
EXPORTFS_COMMAND = "exportfs -ra"

#: The molecule converge playbook's ``pre_task`` refreshes the apt index with
#: ``cache_valid_time: 600`` before the role runs; the role itself does not.
#: Folded into the install request so the role is self-contained on a fresh
#: host (deviation D1).
APT_CACHE_VALID_TIME = 600

#: The request names that queue ``reload nfs`` -- the template task's
#: ``notify:``. Both the content install and its metadata companion carry the
#: notification (the apache migration's ports.conf precedent): Ansible reports
#: the template task changed on a content *or* mode/owner drift.
NOTIFYING: frozenset[str] = frozenset(
    {"Copy exports file", "Set exports file mode and owner"}
)


# --- variables ---------------------------------------------------------------


class NfsVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``nfs`` role mapping, so ``exports`` there sets
    :attr:`exports`. The model is frozen and hashable, so the export list is a
    ``tuple`` (never a ``list``) and a value that reaches
    :func:`~reemote.core.op` hashes (design doc section 8.1).

    :attr:`rpcbind_state` and :attr:`rpcbind_enabled` are carried, and inert:
    ``setup-RedHat.yml`` is the only thing that reads them, and the RedHat path
    is scoped out (D5). They exist here so an inventory that sets them still
    resolves instead of raising on an unknown key.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``nfs_exports``: one line per export, each beginning with the directory
    #: to export, e.g. ``"/srv/nfs 192.0.2.0/24(rw,sync,no_subtree_check)"``.
    #: The directories are created, the lines are rendered into
    #: ``/etc/exports``, and a non-empty list starts and enables the service.
    exports: tuple[str, ...] = ()
    #: ``nfs_rpcbind_state``: the RedHat-only rpcbind service state. Inert.
    rpcbind_state: str = "started"
    #: ``nfs_rpcbind_enabled``: the RedHat-only rpcbind enablement. Inert.
    rpcbind_enabled: bool = True

    @field_validator("exports", mode="before")
    @classmethod
    def _normalize_sequence(cls, value: Any) -> Any:
        """A list becomes a tuple; anything else falls through to the field."""
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("exports")
    @classmethod
    def _export_lines_are_usable(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Every line must have a first token, or the directory loop has no path.

        Ansible's ``nfs_exports | map('split') | map('first')`` turns a blank
        entry into ``Undefined``, which fails the ``file`` task; reemote fails
        it here instead, before any host is contacted (D6).
        """
        if not all(line.split() for line in value):
            raise ValueError(
                "each export must be a non-empty line beginning with a directory path"
            )
        return value


@dataclass(frozen=True)
class NfsPlan:
    """One host's resolved configure target, built by the body.

    Three derived artifacts, all pure functions of :attr:`NfsVars.exports`: the
    export directories the loop walks, the rendered ``/etc/exports`` content,
    and the frozen answer to the service task's ``when: nfs_exports|length >
    0``. It replaces the example's ``plans`` dict keyed by host name: under a
    per-host body the plan is a local, derived before the request wave is built.
    """

    directories: tuple[str, ...]
    exports_content: str
    start_service: bool


# --- pure helpers ------------------------------------------------------------


def render_exports(exports: tuple[str, ...]) -> str:
    """``templates/exports.j2``, pre-rendered control-side (deviation D2).

    The template is the static :data:`EXPORTS_HEADER` comment block followed by
    ``{% for export in nfs_exports %}{{ export }}{% endfor %}`` -- with
    Ansible's ``trim_blocks=True`` the block tags' own newlines vanish, so the
    render is the header plus one newline-terminated line per export. A plain
    join reproduces it byte-for-byte (verified against Jinja2 with Ansible's
    environment: ``trim_blocks=True``, ``lstrip_blocks=False``,
    ``keep_trailing_newline=True``), which is why this role needs neither a
    template file nor the ``reemote[templates]`` extra.
    """
    return EXPORTS_HEADER + "".join(f"{export}\n" for export in exports)


def export_directories(exports: tuple[str, ...]) -> tuple[str, ...]:
    """The directory loop's items: ``nfs_exports | map('split') |
    map('first') | unique``.

    Jinja's ``split`` with no arguments splits on whitespace runs and
    ``unique`` preserves first-occurrence order -- ``str.split()`` and
    ``dict.fromkeys`` are the exact Python equivalents.
    """
    return tuple(dict.fromkeys(export.split()[0] for export in exports))


def nfs_plan(hv: NfsVars) -> NfsPlan:
    """Freeze every read the deploy needs: directories, render, service gate.

    Control-side work inside the body, before the request wave is built: the
    template becomes a plain ``str`` (hashable, identical across waves) and the
    service task's ``when`` becomes a bool. Nothing here contacts a host -- the
    role's vars split only by OS family, so no ``fact`` barrier feeds it.
    """
    return NfsPlan(
        directories=export_directories(hv.exports),
        exports_content=render_exports(hv.exports),
        start_service=bool(hv.exports),
    )


# --- the deploy stages --------------------------------------------------------


def configure(plan: NfsPlan) -> Deploy:
    """``tasks/main.yml``, Ubuntu path: packages, directories, exports, service.

    Branch-free apart from the two loops the plan already froze: this stage is
    what the old example's ``build_nfs`` deploy was, minus the per-request
    ``when=is_ubuntu`` (the family guard covers it, rule 7). The two exports
    requests carry the stable names :data:`NOTIFYING` enumerates; the install,
    directory and service tasks carry no ``notify`` in the role, so their names
    are deliberately absent from the set.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        # The role's install task, with the molecule pre_task's refresh folded
        # in (D1). The refresh is gated on the AptUpdateStamp fact against the
        # controller's UTC clock, so a converged host with a fresh cache yields
        # no update command.
        yield op(
            apt.packages,
            name="Ensure NFS utilities are installed",
            packages=NFS_PACKAGES,
            update=True,
            cache_valid_time=APT_CACHE_VALID_TIME,
            sudo=True,
        )

        # The directory loop, one request per unique export path. The role
        # sets no mode (its `file:` task carries ansible-lint's noqa 208
        # exemption deliberately), so mode is left unset: files.directory
        # creates a missing path with mkdir -p and leaves an existing
        # directory's permissions untouched -- Ansible's mode-less
        # `state: directory` exactly.
        for directory in plan.directories:
            yield op(
                files.directory,
                name=f"Ensure export directory {directory} exists",
                path=directory,
                sudo=True,
            )

        # The template task, pre-rendered control-side (D2). files.install's
        # idempotence is content-only: a converged file yields no commands even
        # if its mode or owner drifted.
        yield op(
            files.install,
            name="Copy exports file",
            content=plan.exports_content,
            dest=EXPORTS_PATH,
            mode="0644",
            user="root",
            group="root",
            sudo=True,
        )
        # The paired files.file converges the mode:0644 owner:group root:root
        # the template task enforces on every run (D3, the apache migration's
        # ports.conf precedent). A no-op when the metadata already matches.
        # Both requests notify: Ansible's template task reports changed on
        # a content *or* metadata drift, and either queues `reload nfs`.
        yield op(
            files.file,
            name="Set exports file mode and owner",
            path=EXPORTS_PATH,
            mode="0644",
            user="root",
            group="root",
            sudo=True,
        )

        # The service task's `when: nfs_exports|length > 0`, frozen into the
        # plan. running/enabled are fact-gated, so a converged host yields no
        # systemctl command. It carries no notify in the role.
        if plan.start_service:
            yield op(
                systemd.service,
                name="Ensure nfs is running",
                service=NFS_SERVER_DAEMON,
                running=True,
                enabled=True,
                sudo=True,
            )

    return deploy


def handler(notified: frozenset[str]) -> Deploy:
    """``handlers/main.yml``: ``reload nfs`` -- the corpus's one command handler.

    ``handlers/main.yml`` is ``command: 'exportfs -ra'``, not a service action,
    so there is no handler factory to call (D4): the deploy yields the command
    as an ``act`` barrier and the wave tree shows a barrier line where another
    role would show a request. An unnotified host yields nothing, so the
    converged run has no handler wave at all.

    A barrier never raises on a non-zero exit -- the ``Result`` is data -- so
    the body checks ``exit_status`` and re-raises the typed
    :class:`~reemote.hosts.CommandError` itself. A silently un-reloaded
    ``exportfs`` is the one failure this role cannot tolerate: the exports file
    would say one thing and the kernel another.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if host.name not in notified:
            return
        result = yield act(
            Shell, command=EXPORTFS_COMMAND, name="reload nfs", sudo=True
        )
        if result["exit_status"] != 0:
            raise CommandError(
                host.name, EXPORTFS_COMMAND, result["exit_status"], result["stderr"]
            )

    return deploy


# --- the role ----------------------------------------------------------------


def nfs(v: VarsSet[NfsVars]) -> Deploy:
    """Install and configure the NFS server on Ubuntu hosts, and run its handler.

    Args:
        v: The role's resolved variables, one :class:`NfsVars` per host
            (``resolve_vars(NfsVars, "nfs", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs -- no
        other role consumes them -- so it returns ``None`` on every path,
        including the family guard's early return.

    Raises:
        CommandError: The ``reload nfs`` handler's ``exportfs -ra`` exited
            non-zero on a notified host. A barrier result is data, so the body
            turns it into the typed error itself; the driver propagates it and
            closes the hosts in its own ``finally``. Bad *variables* are
            rejected earlier, by :func:`~reemote.rolevars.resolve_vars`.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        # Wave 0: the whole task list -- static plan, no fact barriers --
        # closed by the notification read. The checkpoint must immediately
        # follow the stage it reports on; the handler's own barrier wave would
        # be invisible to a second checkpoint, and nothing here needs one.
        yield from configure(nfs_plan(hv))(host)
        wave_report = yield checkpoint("exports done")

        # Wave 1: `reload nfs`, only on the hosts this checkpoint selected.
        notified = changed_hosts(wave_report, NOTIFYING)
        yield from handler(notified)(host)

    return deploy


__all__ = [
    "NfsVars",
    "nfs",
]
