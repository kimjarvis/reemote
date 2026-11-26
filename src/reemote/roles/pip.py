# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The pip role (migrated from ``geerlingguy.pip``).
#
# Installs ``pip`` on Ubuntu hosts and then ensures a list of pip packages:
# install the interpreter's pip package via apt, remove the distro's PEP 668
# ``EXTERNALLY-MANAGED`` marker so pip may write the system site-packages, then
# run the ``pip_install_packages`` loop. :func:`pip` is the whole task list and
# :class:`PipVars` is ``defaults/main.yml``, bound from the inventory's
# ``pip_``-prefixed keys by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. A host outside the family returns
# before its first ``yield`` and emits no requests, no barriers and no log lines
# at all. The role's RedHat branch (the ``repo-epel`` dependency the README
# mentions, ``python3-pip`` from EPEL) and its Debian/older-OS ``pip_package``
# variants are out of scope, so the OS dispatch collapses to the body's opening
# family guard and the frozen Ubuntu constants below.
#
# The stream is **one fact barrier, then one request wave** -- the ``repo_epel``
# shape minus its control fetch, with no handler and no checkpoint:
#
# 0. ``fact(PythonVersion)`` -- the interpreter's ``minor`` version, which the
#    role interpolated into the ``EXTERNALLY-MANAGED`` path
#    (``/usr/lib/python3.<minor>/EXTERNALLY-MANAGED``). This is the only read the
#    body needs before it can build a request, and it closes the first wave.
# 1. :func:`install` -- the apt pip package, the ``EXTERNALLY-MANAGED`` removal,
#    and one request per ``pip_install_packages`` item (plus a virtualenv create
#    for each item that names one), all in one stream.
#
# Because the driver materializes and applies one request per host before pulling
# the next, the role's ordering holds inside the single wave: the apt install
# lands before the pip loop runs ``pip``, and the marker removal lands before the
# first system-pip install that PEP 668 would otherwise block. There is **no
# barrier between the marker removal and the pip installs**, so the old
# gather→snapshot→factory / two-deploy split collapses to one pass.
#
# No template engine is needed: the role ships no template and reads no YAML, so
# this module never imports :mod:`reemote.roles._render` and needs no
# ``reemote[templates]`` extra (the ``php``/``nodejs`` precedent).
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: ``ansible.builtin.package`` becomes ``apt.packages`` -- the package
#   module is picked for the Ubuntu target (the role was multi-OS). The
#   ``ansible.posix``/``community.general`` name is irrelevant here; ``state=
#   present`` maps to the operation's default ``present=True``.
# * D2: the ``EXTERNALLY-MANAGED`` path's ``ansible_facts['python']['version']
#   ['minor']`` becomes a ``fact(PythonVersion)`` barrier, not a static constant.
#   On Ubuntu the marker path varies by release (22.04 → ``python3.10``,
#   24.04 → ``python3.12``), so the version must be read from the target rather
#   than frozen. The role removed the file with ``file: state=absent``; that
#   becomes ``files.file(present=False)``, which is idempotent (an already-absent
#   marker is a no-op).
# * D3: the ``pip_install_packages`` loop runs as **one ``pip.packages`` request
#   per item** rather than batching, because the per-item knobs (``version`` pin,
#   ``virtualenv``, ``extra_args``, ``state``) differ across items and reemote's
#   ``pip.packages`` takes ``pip``/``extra_install_args``/``present``/``latest``
#   as call-level scalars. Batching items with different environments into one
#   request would be wrong; one request per item mirrors the role's one-task-per-
#   loop-iteration exactly and keeps each item's log line distinct.
# * D4: ``item.virtualenv`` becomes two requests -- a ``pip.venv`` create for the
#   virtualenv, then a ``pip.packages`` targeting ``pip=<virtualenv>/bin/pip``.
#   Ansible's ``pip`` module auto-created the virtualenv as a hidden side effect;
#   reemote splits venv creation into its own idempotent operation (the
#   ``pip.packages`` deviation note). ``state=absent`` items skip the create (the
#   role would not build a tree just to uninstall from it).
# * D5: ``item.executable`` / the ``pip_executable`` default map onto
#   ``pip.packages``' ``pip=`` argument -- a bare ``pip3`` on the PATH for a
#   system install, or ``<virtualenv>/bin/pip`` for a virtualenv install (which
#   is what the role's ``ternary(omit, pip_executable)`` spelled). The
#   autodetection ``pip3 if pip_package.startswith('python3') else pip`` is
#   :func:`derive_executable`, overridden by an explicit ``pip_executable``.
# * D6: ``state=forcereinstall`` (and any state beyond present/absent/latest) is
#   out of scope -- ``pip.packages`` has no native forced-reinstall direction. A
#   ``PipPackage`` carrying it is rejected by pydantic before any host is
#   contacted; the role's README lists it only as an option, never a default.
# * every request carries ``sudo=True`` -- the role relied on play-level
#   ``become``; reemote has no such thing, so each request elevates explicitly.
#   A virtualenv under the login user's own home would not strictly need it, but
#   sudo is the faithful analog of the become-driven play and is always safe for
#   a root-owned tree.
# * the ``apt: update_cache`` and the older-OS ``set_fact: pip_package`` are
#   ``molecule`` pre_tasks, not part of the role, so they are out of scope.
# * the host-family guard is the body's opening ``if``, not a ``when=`` on every
#   request: an off-target host emits no requests and no log lines at all.
# * variable validation becomes pydantic validation: a bad :class:`PipVars` or
#   :class:`PipPackage` entry raises ``ValidationError`` -- a ``ValueError``, so
#   the runner maps it -- before any host is contacted.
# """

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..hosts import Host
from ..modules import apt, files
from ..modules import pip as pip_mod
from ..modules.server import PythonVersion
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, fact

# --- role constants (defaults/main.yml, Ubuntu path) --------------------------

#: ``defaults/main.yml``'s ``pip_package``. ``For Python 3, use python3-pip``,
#: and modern Ubuntu ships it in the main archive, so no repository prerequisite.
DEFAULT_PIP_PACKAGE = "python3-pip"

#: The PEP 668 marker the role deletes so pip may write the system
#: interpreter's site-packages. ``{minor}`` replaces the Ansible
#: ``ansible_facts['python']['version']['minor']`` interpolation, resolved from
#: the ``PythonVersion`` barrier rather than frozen (D2).
EXTERNALLY_MANAGED_TEMPLATE = "/usr/lib/python3.{minor}/EXTERNALLY-MANAGED"


# --- variables ---------------------------------------------------------------


class PipPackage(BaseModel):
    """One ``pip_install_packages`` entry: a name, an optional pin, a state.

    The role's loop reads each item as either a bare string (``- colorama``) or
    a mapping (``{name: docker, version: 1.2.3, virtualenv: /my_app/venv,
    state: present, extra_args: --index-url ...}``); the ``install_packages``
    before-validator coerces a bare string to ``{"name": ...}`` so pydantic
    builds this model either way. Frozen, so a tuple of them stays hashable
    through the deploy body (design doc section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``item.name | default(item)``.
    name: str
    #: ``item.version | default(omit)``: an optional pin, rendered as pip's
    #: ``<pkg>==<version>`` spec (D3).
    version: str | None = None
    #: ``item.virtualenv | default(omit)``: a virtualenv to install into; it
    #: selects the ``pip=<virtualenv>/bin/pip`` target and triggers a
    #: ``pip.venv`` create (D4).
    virtualenv: str | None = None
    #: ``item.state | default('present')``: the pip module's state, mapped onto
    #: the operation's ``present``/``latest`` flags (D3/D6).
    state: Literal["present", "absent", "latest"] = "present"
    #: ``item.extra_args | default(omit)``: free-form arguments appended to the
    #: install command (``extra_install_args``).
    extra_args: str | None = None

    @field_validator("name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """An empty package name is a typo, not "unset"."""
        if not value:
            raise ValueError("pip package name must be a non-empty string")
        return value

    @field_validator("version", "virtualenv", "extra_args")
    @classmethod
    def _none_or_non_empty(cls, value: str | None) -> str | None:
        """An empty override is a mistake, not "omit"."""
        if value is not None and not value:
            raise ValueError("must be a non-empty string or null")
        return value


class PipVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names carry no ``pip_`` prefix -- the prefix passed to
    :func:`~reemote.rolevars.resolve_vars` binds ``pip_package`` to :attr:`package`,
    ``pip_executable`` to :attr:`executable` and ``pip_install_packages`` to
    :attr:`install_packages`. :attr:`executable` stays ``None`` by default so the
    ``pip_package``-derived autodetect applies (D5), exactly how
    ``defaults/main.yml`` computes ``pip_executable``.

    The model is frozen, so it stays hashable through the deploy body
    (:class:`~reemote.rolevars.VarsSet` is closed over by the factory), and
    :attr:`install_packages` is a ``tuple`` of frozen :class:`PipPackage` models,
    never a ``list`` of dicts (design doc section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``pip_package``: the apt package that provides pip.
    package: str = DEFAULT_PIP_PACKAGE
    #: ``pip_executable``: the pip binary to drive system installs with. The
    #: empty default derives ``pip3``/``pip`` from :attr:`package` (D5).
    executable: str | None = None
    #: ``pip_install_packages``: the packages to ensure with pip, each a bare
    #: name or a ``{name, version, virtualenv, state, extra_args}`` mapping (the
    #: role's ``loop``).
    install_packages: tuple[PipPackage, ...] = ()

    @field_validator("package")
    @classmethod
    def _package_non_empty(cls, value: str) -> str:
        """An empty package name would render an empty apt install."""
        if not value:
            raise ValueError("pip_package must be a non-empty string")
        return value

    @field_validator("executable")
    @classmethod
    def _executable_non_empty(cls, value: str | None) -> str | None:
        """An empty executable is a mistake, not "autodetect"."""
        if value is not None and not value:
            raise ValueError("pip_executable must be a non-empty string or null")
        return value

    @field_validator("install_packages", mode="before")
    @classmethod
    def _coerce_packages(cls, value: Any) -> Any:
        """A bare-string item becomes ``{"name": item}``; a list becomes a tuple.

        The role's ``loop`` accepts ``- colorama`` and
        ``- {name: docker, version: 1.2.3}`` interchangeably; normalize the
        shorthand here so pydantic builds a :class:`PipPackage` either way. A
        non-sequence falls through for pydantic to reject.
        """
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(
                {"name": item} if isinstance(item, str) else item for item in value
            )
        return value


# --- frozen per-host plan ------------------------------------------------------


@dataclass(frozen=True)
class PipPlan:
    """One host's resolved pip targets, built by :func:`pip_plan`.

    Everything except the ``minor`` version (which comes from the barrier) is
    decided from the resolved variables, and the marker path is interpolated
    once the barrier answers. Frozen and hashable; under a per-host body it is a
    local, not a host-keyed dict.
    """

    #: The apt package providing pip (``pip_package``).
    package: str
    #: The pip executable for system installs, autodetected or explicit (D5).
    executable: str
    #: The ``EXTERNALLY-MANAGED`` path, interpolated from the interpreter minor.
    externally_managed: str
    #: The loop items, one per ``pip_install_packages`` entry (D3).
    items: tuple[PipPackage, ...]


# --- pure helpers ------------------------------------------------------------


def derive_executable(package: str) -> str:
    """``pip3 if pip_package.startswith('python3') else pip`` (D5).

    The role's ``defaults/main.yml`` Jinja expression, restated in Python. An
    explicit :attr:`PipVars.executable` overrides it, so this only answers the
    default.
    """
    return "pip3" if package.startswith("python3") else "pip"


def pip_spec(package: PipPackage) -> str:
    """One entry as pip's ``<pkg>`` or ``<pkg>==<version>`` spec (D3)."""
    if package.version:
        return f"{package.name}=={package.version}"
    return package.name


def pip_executable_for(package: PipPackage, system_executable: str) -> str:
    """The ``pip=`` target for one item (D4/D5).

    A virtualenv item names ``<virtualenv>/bin/pip`` -- what the role's
    ``ternary(omit, pip_executable)`` implied by leaving ``executable`` unset
    when ``virtualenv`` was given; a system item names the resolved
    ``pip_executable``.
    """
    if package.virtualenv:
        return f"{package.virtualenv.rstrip('/')}/bin/pip"
    return system_executable


def pip_plan(hv: PipVars, minor: int) -> PipPlan:
    """Freeze every target the deploy needs from the vars and the barrier minor.

    Pure: given the interpreter's ``minor`` version (the only host read), the
    apt package, the executable, the marker path and the loop items are all
    decided before a request is built.
    """
    return PipPlan(
        package=hv.package,
        executable=hv.executable or derive_executable(hv.package),
        externally_managed=EXTERNALLY_MANAGED_TEMPLATE.format(minor=minor),
        items=hv.install_packages,
    )


# --- the deploy --------------------------------------------------------------


def install(plan: PipPlan) -> Deploy:
    """The whole Ubuntu role as one request wave: apt pip, marker, pip loop.

    Every value the role read from its variables or facts is frozen into the
    plan, and the only read (the interpreter minor) already happened at the
    barrier, so this deploy is a pure function of the plan. The driver applies
    each request before pulling the next, which preserves the role's ordering:
    the apt install lands ``pip`` before the loop runs it, and the marker
    removal precedes the first system-pip install. The role has no handlers, so
    there is no notification split and no checkpoint.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        # tasks/main.yml task 1: ``package: name=<pip_package> state=present``.
        yield op(
            apt.packages,
            name="Ensure Pip is installed.",
            packages=(plan.package,),
            sudo=True,
        )

        # tasks/main.yml task 2: ``file: state=absent`` on the PEP 668 marker
        # (D2). Idempotent: an already-removed marker is a no-op.
        yield op(
            files.file,
            name="Remove EXTERNALLY-MANAGED.",
            path=plan.externally_managed,
            present=False,
            sudo=True,
        )

        # tasks/main.yml task 3: the pip_install_packages loop, one request per
        # item (D3). Each is fact-gated by the PipPackages fact, so a converged
        # host's item no-ops.
        for item in plan.items:
            if item.virtualenv and item.state != "absent":
                # D4: create the virtualenv the item installs into, as its own
                # idempotent request before pip is pointed at it.
                yield op(
                    pip_mod.venv,
                    name=f"Ensure virtualenv {item.virtualenv} exists.",
                    path=item.virtualenv,
                    sudo=True,
                )

            if item.state == "absent":
                yield op(
                    pip_mod.packages,
                    name=f"Ensure pip package {item.name} is removed.",
                    packages=(pip_spec(item),),
                    pip=pip_executable_for(item, plan.executable),
                    present=False,
                    sudo=True,
                )
            elif item.state == "latest":
                yield op(
                    pip_mod.packages,
                    name=f"Ensure pip package {item.name} is up to date.",
                    packages=(pip_spec(item),),
                    pip=pip_executable_for(item, plan.executable),
                    latest=True,
                    extra_install_args=item.extra_args,
                    sudo=True,
                )
            else:
                yield op(
                    pip_mod.packages,
                    name=f"Ensure pip package {item.name} is installed.",
                    packages=(pip_spec(item),),
                    pip=pip_executable_for(item, plan.executable),
                    extra_install_args=item.extra_args,
                    sudo=True,
                )

    return deploy


# --- the role ----------------------------------------------------------------


def pip(v: VarsSet[PipVars]) -> Deploy:
    """Install pip via apt and ensure a list of pip packages on Ubuntu hosts.

    Args:
        v: The role's resolved variables, one :class:`PipVars` per host
            (``resolve_vars(PipVars, "pip_", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs -- no
        other role consumes them -- so it returns ``None`` on every path,
        including the family guard's early return.

    Raises:
        ValueError: The interpreter version cannot be read (the
            ``PythonVersion`` fact's own failure on a host with no ``python3``)
            or a marker path cannot be built. Raised after the barrier and
            before any request is applied, so nothing is half-installed. A bad
            :class:`PipVars` / :class:`PipPackage` value is rejected earlier, by
            :func:`~reemote.rolevars.resolve_vars`, before any host is contacted.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        # Wave 0: the interpreter minor the EXTERNALLY-MANAGED path interpolates
        # (D2). The barrier closes the first wave; every request follows in the
        # second.
        python_version = yield fact(PythonVersion)
        plan = pip_plan(hv, python_version["minor"])
        yield from install(plan)(host)

    return deploy


__all__ = [
    "PipPackage",
    "PipVars",
    "pip",
]
