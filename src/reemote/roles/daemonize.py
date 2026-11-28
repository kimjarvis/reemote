# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The daemonize role (migrated from ``geerlingguy.daemonize``).
#
# Builds and installs the ``daemonize`` utility from its GitHub release
# tarball on Ubuntu hosts: the ``build-essential`` toolchain the role's README
# names as a prerequisite, the ``release-<version>`` tarball extracted into
# the workspace behind Ansible's ``creates:`` gate, and -- only on a host
# where the binary is not already on ``PATH`` -- the ``./configure`` /
# ``make`` / ``make install`` build as root. :func:`daemonize` is the whole
# of ``tasks/main.yml`` (four tasks, no handlers, no templates) and
# :class:`DaemonizeVars` is ``defaults/main.yml``, bound from the inventory
# entry's ``daemonize`` role mapping by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. The role ships no ``vars/``
# dispatch and its ``meta/main.yml`` names Ubuntu, Debian and GenericUNIX;
# the ``gcc``/``make`` prerequisite is installed here with ``apt`` (D5), so
# the Debian family is the only one migrated and a host outside it is
# skipped, not mapped onto another package manager.
#
# The stream carries two waves (design doc section 5):
#
# 0. :func:`setup` -- ``apt.packages`` (the toolchain) and
#    ``files.unarchive`` (the tarball, ``creates=<src_dir>/INSTALL``) --
#    closed by ``fact(server.Which)``, the read the role's ``register:
#    daemonize_installed`` task performed.
# 1. :func:`build` -- the ``act(Shell)`` compile, **only on a host whose
#    probe found nothing**. The barrier's ``Result`` is data, so the body
#    checks the exit status and raises :class:`~reemote.CommandError`
#    itself; a host with the binary already on ``PATH`` never reaches this
#    wave, which is the whole point of the in-stream read.
#
# There is deliberately no ``checkpoint``: the role ships no
# ``handlers/main.yml``, so no report would ever be read, and no stateless
# operation (a restart, a reload) needs gating behind one.
#
# :func:`daemonize` returns ``None``: no composing role in the corpus
# consumes an output, so there is no outputs dataclass (§17.2).
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: the host-family guard is the body's opening ``if``, not a ``when=``
#   on every request: a host outside the ``"Ubuntu"`` family returns before
#   its first ``yield``, so it emits no requests, no barriers and no log
#   entries at all.
# * D2: the ``get_url`` task and the ``unarchive`` task collapse into one
#   ``files.unarchive`` request. The role downloaded the tarball into
#   ``{{ workspace }}`` and then unpacked that remote file
#   (``copy: false``); reemote fetches an ``http(s)`` ``src`` **control-side**
#   as binary bytes, spools it to ``/tmp``, extracts it and removes the
#   spool. The end state -- the source tree under the workspace -- is the
#   same; the workspace no longer holds a leftover ``.tar.gz``.
# * D3: ``unarchive``'s ``mode: 0755`` is not passed. ``files.unarchive``
#   renders a mode as a recursive ``chmod -R`` of ``dest``, and this role's
#   ``dest`` is the shared workspace (``/root`` by default), so passing it
#   would re-mode the operator's home rather than the archive. The role's
#   own ``chmod`` only ever mattered to the build, which reads the tree as
#   root anyway (the ``php_xdebug`` precedent, which drops the same ``mode``).
# * D4: the build is an ``act`` barrier inside the stream, not three
#   ``command:`` items with ``chdir=`` and not ``gather_action`` between two
#   ``run()`` calls. ``Shell`` has no ``chdir`` parameter, so the working
#   directory is an inline ``cd <src_dir> &&``; the three commands become one
#   ``&&`` chain, which stops on the first failure exactly like Ansible's
#   loop does, and the whole chain runs as one root shell.
# * D5: the ``build-essential`` install is **new**. The role only documents
#   the toolchain as a README prerequisite -- an operator who lacked ``gcc``
#   got a failing ``configure`` -- so naming the dependency in the deploy is
#   strictly more faithful to the role's stated requirement than leaving it
#   out. It is fact-gated (``apt.packages`` reads ``AptPackages``), so a host
#   that already has the toolchain reports no change and runs no install.
# * D6: the role's ``ignore_errors: "{{ ansible_check_mode }}"`` is dropped.
#   It exists so a ``--check`` run (which applies nothing, so the source tree
#   is absent) does not fail on the build; reemote's ``dry=True`` preview
#   parks on the first barrier and applies nothing beyond it, so the build
#   barrier is never reached in a preview and has no error to swallow.
# * D7: the ``which`` probe becomes a ``fact(server.Which)`` barrier rather
#   than an ``act`` -- it is a read whose only purpose is to branch the body,
#   ``command -v daemonize || true`` mapped to ``str | None``. The role's
#   ``changed_when: false`` is free (facts never record a change) and its
#   ``failed_when: false`` is built in (an unknown command reads as ``None``,
#   not as an error).
# """

import shlex
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..actions import Shell
from ..core import op
from ..hosts import CommandError, Host
from ..modules import apt, files, server
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, act, fact

#: ``defaults/main.yml``'s ``workspace``. The role unpacks the archive into
#: it, so it doubles as the ``dest`` of the one ``files.unarchive`` request.
WORKSPACE = "/root"

#: ``defaults/main.yml``'s ``daemonize_version``. The README's table still
#: documents 1.7.5, but the shipped default is what the deploy builds.
VERSION = "1.7.7"

#: ``defaults/main.yml``'s ``daemonize_install_path``. It reaches the build
#: only as ``./configure --prefix=<path>``, so ``make install`` lands the
#: binary under ``<path>/bin``.
INSTALL_PATH = "/usr"

#: The tarball's home. The role interpolates the version into both the
#: ``release-`` tag name and the archive suffix, so a version bump moves the
#: URL and the source directory together.
TARBALL_URL = "https://github.com/bmc/daemonize/archive/release-{version}.tar.gz"

#: The toolchain the role's README demands (``apt-get install build-essential``
#: on the Debian family). Installed as a declarative prerequisite (D5), which
#: is what lets a host with no compiler converge on the second pass instead of
#: failing the build the way the Ansible role does.
BUILD_PACKAGES = ("build-essential",)

#: The two stable request names -- the role's own task names, verbatim
#: (trailing periods included). Nothing here notifies a handler, so the names
#: carry the report line and the ``changed`` bookkeeping rather than a
#: ``changed_hosts`` selection.
SETUP_NAME = "Expand daemonize archive."
TOOLCHAIN_NAME = "Ensure build tools are installed."
BUILD_NAME = "Build daemonize."


class DaemonizeVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``daemonize`` role mapping, so ``version`` there sets
    :attr:`version`. The three ``defaults/main.yml`` knobs are the whole
    surface: the role reads no other variable.

    The model is frozen, so it stays hashable through the deploy body
    (:class:`~reemote.rolevars.VarsSet` is closed over by the factory), and
    every field is a scalar because nothing here reaches ``op()`` as a
    sequence -- the one package tuple is the module constant
    :data:`BUILD_PACKAGES`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``daemonize_version`` -- the ``release-<version>`` tag to build. Dotted
    #: digits, because it names both the archive and the directory inside it.
    version: str = VERSION

    #: ``workspace`` -- where the source tree is unpacked.
    workspace: str = WORKSPACE

    #: ``daemonize_install_path`` -- the ``--prefix`` of the build. Absolute,
    #: since the upstream Makefile writes ``<path>/sbin/daemonize``.
    install_path: str = INSTALL_PATH

    @field_validator("version")
    @classmethod
    def _version_shape(cls, value: str) -> str:
        """The role's ``version`` must be dotted digits, as the paths assume."""
        parts = value.split(".")
        if not all(part.isdigit() for part in parts):
            raise ValueError(f"daemonize_version must be dotted digits, got {value!r}")
        return value

    @field_validator("workspace")
    @classmethod
    def _workspace_non_empty(cls, value: str) -> str:
        """An empty workspace would extract into ``/daemonize-release-...``."""
        if not value:
            raise ValueError("daemonize_workspace must be a non-empty string")
        return value

    @field_validator("install_path")
    @classmethod
    def _install_path_absolute(cls, value: str) -> str:
        """A relative ``--prefix`` would install under the build directory."""
        if not value.startswith("/"):
            raise ValueError(f"daemonize_install_path must be absolute, got {value!r}")
        return value


@dataclass(frozen=True)
class DaemonizePlan:
    """One host's resolved build, frozen before the first request.

    Built by :func:`resolve_daemonize` from this host's variables, so the
    three paths the deploy and the build action need -- the source directory,
    its ``creates:`` marker and the tarball URL -- are decided once from the
    same version instead of being re-interpolated per request.
    """

    version: str
    workspace: str
    install_path: str
    src_dir: str
    creates: str
    tarball_url: str


def resolve_daemonize(hv: DaemonizeVars) -> DaemonizePlan:
    """Combine the role's variables into one frozen per-host plan."""
    return DaemonizePlan(
        version=hv.version,
        workspace=hv.workspace,
        install_path=hv.install_path,
        src_dir=f"{hv.workspace}/daemonize-release-{hv.version}",
        creates=f"{hv.workspace}/daemonize-release-{hv.version}/INSTALL",
        tarball_url=TARBALL_URL.format(version=hv.version),
    )


def build_command(plan: DaemonizePlan) -> str:
    """The build action's shell: ``cd <src_dir> && ./configure && make && ...``.

    The role's ``command:`` loop runs the three items in order with
    ``chdir=src_dir`` and stops on the first failure -- an ``&&`` chain does
    exactly that (D4). ``Shell`` with ``sudo=True`` renders ``"sudo " +
    command`` verbatim, with no ``sh -c`` wrapper of its own, so the chain is
    wrapped explicitly here: the command that reaches the host (and the
    simulation seed) is ``sudo sh -c 'cd ... && ./configure --prefix=/usr &&
    make && make install'``.
    """
    inner = (
        f"cd {shlex.quote(plan.src_dir)}"
        f" && ./configure --prefix={shlex.quote(plan.install_path)}"
        " && make"
        " && make install"
    )
    return f"sh -c {shlex.quote(inner)}"


def setup(plan: DaemonizePlan) -> Deploy:
    """The toolchain prerequisite and the source extraction.

    :func:`~reemote.modules.files.unarchive` is the role's ``get_url`` plus
    ``unarchive`` pair as one request (D2): the URL is fetched control-side as
    binary, spooled, extracted and cleaned up, with ``creates=<src_dir>/INSTALL``
    resolving Ansible's ``creates:`` gate in-operation -- a converged host is a
    no-op and never fetches. The toolchain request that precedes it is D5.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            apt.packages,
            name=TOOLCHAIN_NAME,
            packages=BUILD_PACKAGES,
            present=True,
            sudo=True,
        )
        yield op(
            files.unarchive,
            name=SETUP_NAME,
            src=plan.tarball_url,
            dest=plan.workspace,
            creates=plan.creates,
            sudo=True,
        )

    return deploy


def build(plan: DaemonizePlan) -> Deploy:
    """``tasks/main.yml``'s build loop, as one imperative step.

    The deploy yields the ``act`` barrier itself, so its ``Result`` is data:
    the caller reads the exit status (the role's ``failed_when: false`` pair of
    the ``which`` probe is the *build's* opposite -- a failing ``make`` is a
    real failure) and raises :class:`~reemote.CommandError` on it. Kept as a
    sub-deploy so the body's branch reads as a stage rather than inline shell
    arithmetic.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        command = build_command(plan)
        result = yield act(Shell, command=command, name=BUILD_NAME, sudo=True)
        if result["exit_status"] != 0:
            raise CommandError(
                host.name, command, result["exit_status"], result["stderr"]
            )

    return deploy


def daemonize(v: VarsSet[DaemonizeVars]) -> Deploy:
    """Build and install the ``daemonize`` utility on Ubuntu hosts.

    Args:
        v: The role's resolved variables, one :class:`DaemonizeVars` per host
            (``resolve_vars(DaemonizeVars, "daemonize", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves` that returns ``None`` for every
        host it runs -- no composing role in the corpus consumes an output
        (§17.2).

    Raises:
        ValueError: ``daemonize_version`` is not dotted digits, the workspace
            is empty, or the install path is relative. Raised by pydantic while
            the variables are resolved, so before any host is contacted.
        CommandError: The build action exited non-zero. Raised in the body, from
            the barrier's own ``Result`` -- an ``act`` barrier never raises on a
            failing exit status, so the check belongs to the role.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return None

        plan = resolve_daemonize(hv)

        # Wave 0: the toolchain and the extraction, closed by the read that
        # decides the build. The source tree must be on the host before
        # ``configure`` can run, so the probe -- like the build -- sits after
        # this wave applied, which is exactly why it cannot be hoisted.
        yield from setup(plan)(host)

        # The role's `register: daemonize_installed` + `when: rc != 0` pair,
        # moved into the body. The answer is fresh per pass, so a host that
        # built the binary on pass 1 reads it back on pass 2 and queues
        # nothing: the second pass is quiet because the machine changed, not
        # because a plan was frozen.
        installed = yield fact(server.Which, command="daemonize")

        # Wave 1: the compile, only on a host whose probe found nothing.
        if installed is None:
            yield from build(plan)(host)

        return None

    return deploy


__all__ = ["DaemonizeVars", "daemonize"]
