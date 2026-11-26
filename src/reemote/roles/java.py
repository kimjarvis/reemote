# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The Java role (migrated from ``geerlingguy.java``).
#
# Installs the distribution's default OpenJDK package and optionally exports
# ``JAVA_HOME`` through a ``/etc/profile.d`` script: the version-keyed
# ``vars/Ubuntu-<major>.yml`` package selection, the ``man1`` directory
# workaround (Debian bug 863199 / role issue #64), the ``apt`` install, and the
# optional ``java_home.sh`` profile template. :func:`java` is the whole task
# list and :class:`JavaVars` is ``defaults/main.yml``, bound from the
# inventory entry's ``java`` role mapping by
# :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. A host outside the family
# returns before its first ``yield`` and emits no requests, no barriers and no
# log lines at all -- so the Alpine and CentOS hosts in a shared inventory are
# never contacted. The role's RedHat, Amazon and FreeBSD branches are out of
# scope, so its ``include_vars`` OS dispatch collapses to the frozen
# :data:`UBUNTU_JAVA_PACKAGES` table below -- keyed not by family but by the
# gathered *major version*, which is why the role still needs a read of the
# machine before it can build a single request.
#
# The stream is two waves -- one barrier, one request wave (design doc section
# 18.12):
#
# 0. ``fact(LinuxDistribution)`` -- the major version the vars table selects on
#    and the ≥ 18 ``man1`` gate reads, and the re-check that the declared
#    family matches the machine.
# 1. :func:`install` -- the ``man1`` directory (when gated), the apt install
#    with the folded cache refresh (D1), and the JAVA_HOME profile install +
#    mode convergence (when :attr:`JavaVars.home` is set).
#
# The role has no handlers, no ``register``/``when`` outcome reads and no
# command/shell tasks, so there is **no checkpoint and no second read**: its
# only mid-play fact was the ``include_vars`` dispatch, which now sits inside
# the body as a barrier instead of in a gather-then-factory split (the old
# example's ``build_java(plans)`` freeze disappears with the two-phase model).
# Neither raises half-applies anything: both error paths fire at the barrier,
# before the request wave is built.
#
# No template engine is needed: the role's one template is the single line
# ``export JAVA_HOME={{ java_home }}``, which :func:`render_java_home`
# reproduces with a plain f-string, so this role never imports
# :mod:`reemote.roles._render` and needs no ``reemote[templates]`` extra.
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: the molecule converge playbook's apt-index refresh ``pre_task``
#   (``update_cache=true cache_valid_time=600 changed_when=false``) is folded
#   into ``apt.packages(update=True, cache_valid_time=600)`` so the role is
#   self-contained on a fresh host. The refresh is gated on the
#   ``AptUpdateStamp`` fact against the controller's UTC clock; a stale-cache
#   refresh reports ``changed=True`` where the pre_task forced
#   ``changed_when: false``.
# * D2: the template renders with an f-string, not Jinja2 -- byte-identical
#   for this one line, trailing newline kept (the apache precedent's
#   ``keep_trailing_newline=True``).
# * D3: a non-Ubuntu distribution on a host declared ``Ubuntu`` fails fast at
#   the barrier instead of dispatching to another OS's vars; the raise is the
#   role's, not the driver's, because a barrier result is data.
# * D4: an unsupported Ubuntu major fails fast **even when the package list is
#   overridden** -- the role's ``include_vars`` runs unconditionally and errors
#   with "file not found" before the override is ever consulted.
# * the host-family guard is the body's opening ``if``, not a ``when=`` on
#   every request: an off-target host emits no requests and no ``[skipped]``
#   lines.
# * variable validation becomes pydantic validation: an invalid
#   :attr:`JavaVars.packages` or :attr:`JavaVars.home` raises
#   ``ValidationError`` -- a ``ValueError``, so the runner maps it -- before
#   any host is contacted (the old example's ``TypeError`` gates included).
# """

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..hosts import Host
from ..modules import apt, files
from ..modules.server import LinuxDistribution
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, fact

# --- role constants (defaults/main.yml + vars/Ubuntu-*.yml, Ubuntu path) ------

#: ``setup-Debian.yml``'s man-directory workaround: openjdk's postinst fails
#: on a minimal Ubuntu image without /usr/share/man/man1 (Debian bug 863199,
#: geerlingguy/ansible-role-java#64). The task runs on Ubuntu >= 18 only.
MAN_DIR = "/usr/share/man/man1"

#: ``tasks/main.yml``'s final template task destination.
JAVA_HOME_PROFILE = "/etc/profile.d/java_home.sh"

#: The gate below >= 18 for the man1 task -- the role's
#: ``distribution_version is version('18', '>=')`` condition, frozen.
MAN_DIR_MIN_MAJOR = 18

#: The molecule converge playbook's pre_task refreshes the apt index with
#: ``cache_valid_time: 600`` before the role runs; the role itself does not.
#: Folded into the install request so the role works on a fresh host
#: (deviation D1).
APT_CACHE_VALID_TIME = 600

#: The role's ``vars/Ubuntu-<major>.yml`` ``__java_packages`` lists, frozen
#: as tuples (one per supported release, exactly as the role ships them).
#: The include_vars dispatch selects by the gathered major version; an
#: unsupported major raises, as the role's include_vars would fail with
#: "file not found" (deviation D4).
UBUNTU_JAVA_PACKAGES: dict[int, tuple[str, ...]] = {
    12: ("openjdk-7-jdk",),
    14: ("openjdk-7-jdk",),
    16: ("openjdk-8-jdk",),
    18: ("openjdk-11-jdk",),
    20: ("openjdk-11-jdk",),
    22: ("openjdk-17-jdk",),
    24: ("openjdk-21-jdk",),
    26: ("openjdk-25-jdk",),
}


# --- variables ---------------------------------------------------------------


class JavaVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``java`` role mapping, so ``packages`` and ``home``
    there set :attr:`packages` and :attr:`home`.

    The model is frozen, so it stays hashable through the deploy body
    (:class:`~reemote.rolevars.VarsSet` is closed over by the factory), and
    :attr:`packages` is a ``tuple``, never a ``list`` (design doc section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``java_packages``: ``None`` reproduces the role's
    #: ``set_fact: java_packages={{ __java_packages | list }}`` -- the vars
    #: table selects from the gathered major version. An explicit tuple (even
    #: an empty one, which installs nothing) wins over the table.
    packages: tuple[str, ...] | None = None
    #: ``java_home``: the empty default skips the profile template task, as
    #: the role's ``when: java_home is defined and java_home != ""`` does; a
    #: non-empty value is rendered into :data:`JAVA_HOME_PROFILE`.
    home: str = ""

    @field_validator("packages", mode="before")
    @classmethod
    def _normalize_sequence(cls, value: Any) -> Any:
        """A list becomes a tuple; a non-sequence falls through for pydantic to reject."""
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @field_validator("packages")
    @classmethod
    def _entries_non_empty(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        """Every entry must be a non-empty package name; the empty list is valid."""
        if value is None:
            return value
        if not all(isinstance(item, str) and item for item in value):
            raise ValueError("packages must hold non-empty package names")
        return value


# --- frozen per-host plan ------------------------------------------------------


@dataclass(frozen=True)
class JavaPlan:
    """One host's resolved targets, built by :func:`java_plan` after the barrier.

    Everything the deploy reads is decided here: the vars-table selection (or
    override), the >= 18 man-directory gate and the rendered JAVA_HOME profile
    content. Frozen and hashable; it replaces the old example's ``plans`` dict
    keyed by host name -- under a per-host body the plan is a local.
    """

    packages: tuple[str, ...]
    man_dir: bool
    java_home_content: str | None


# --- pure helpers ------------------------------------------------------------


def render_java_home(java_home: str) -> str:
    """``templates/java_home.sh.j2``, pre-rendered control-side.

    The template is a single ``export JAVA_HOME={{ java_home }}`` line with no
    other Jinja2 syntax, so a plain f-string is the whole renderer -- no
    Jinja2 dependency (the apache migration needs one; this role does not,
    deviation D2). The trailing newline is kept, matching the template source
    byte-for-byte (the apache precedent's ``keep_trailing_newline=True``).
    """
    return f"export JAVA_HOME={java_home}\n"


def ubuntu_major(info: dict[str, Any]) -> int:
    """``ansible_facts.distribution_version.split('.')[0]`` from a fact read.

    The role targets Ubuntu, so a host reporting another distribution is a
    hard error -- the inventory family said ``Ubuntu`` (deviation D3). The
    ``LinuxDistribution`` fact's ``major`` may arrive as an int or a str; both
    normalize here.
    """
    name = info.get("name")
    if name != "Ubuntu":
        raise ValueError(
            f"this role targets Ubuntu; the host reports {name!r} "
            "(LinuxDistribution.name) -- check the inventory family"
        )
    try:
        return int(info["major"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "cannot read the Ubuntu major version from the distribution "
            f"facts (major={info.get('major')!r})"
        ) from exc


def java_plan(hv: JavaVars, info: dict[str, Any]) -> JavaPlan:
    """Freeze every read the deploy needs: vars table, man1 gate, render.

    Pure, and called immediately after the ``LinuxDistribution`` barrier: the
    gathered major version selects the role's ``vars/Ubuntu-<major>.yml``
    package list (the include_vars task), the ``>= 18`` man-directory gate is
    frozen, and the JAVA_HOME template becomes a plain ``str`` (hashable). An
    unknown major raises here -- the role's include_vars fails with "file not
    found" the same way, even when the package list is overridden
    (deviation D4).
    """
    major = ubuntu_major(info)
    if major not in UBUNTU_JAVA_PACKAGES:
        supported = ", ".join(str(m) for m in sorted(UBUNTU_JAVA_PACKAGES))
        raise ValueError(
            f"the role ships no vars/Ubuntu-{major}.yml "
            f"(supported Ubuntu majors: {supported}); the role's "
            "include_vars task fails on an unsupported release the same way"
        )
    packages = hv.packages
    if packages is None:
        packages = UBUNTU_JAVA_PACKAGES[major]
    return JavaPlan(
        packages=packages,
        man_dir=major >= MAN_DIR_MIN_MAJOR,
        java_home_content=render_java_home(hv.home) if hv.home else None,
    )


# --- the deploy --------------------------------------------------------------


def install(plan: JavaPlan) -> Deploy:
    """The whole Ubuntu role as one request wave: man1, apt, JAVA_HOME profile.

    Every read the role takes mid-play (the include_vars major-version
    dispatch) was frozen into the plan at the barrier, so this deploy is a
    pure function of it. The role has no handlers, so there is no
    notification split and no checkpoint.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if plan.man_dir:
            # setup-Debian.yml's first task, gated on Ubuntu >= 18 (frozen
            # into the plan). files.directory converges the mode on an
            # existing directory too, as Ansible's file module does.
            yield op(
                files.directory,
                name="Ensure 'man' directory exists",
                path=MAN_DIR,
                mode="0755",
                sudo=True,
            )

        # Deviation D1: the role's apt task carries no update_cache; its
        # molecule converge playbook refreshes the index in a pre_task.
        # Folded in here so the role is self-contained on a fresh host. The
        # refresh is gated on the AptUpdateStamp fact against the controller's
        # UTC clock, so a converged host with a fresh cache yields no update
        # command.
        yield op(
            apt.packages,
            name="Ensure Java is installed",
            packages=plan.packages,
            update=True,
            cache_valid_time=APT_CACHE_VALID_TIME,
            sudo=True,
        )

        if plan.java_home_content is not None:
            # The template task, pre-rendered control-side (deviation D2).
            # files.install's idempotence is content-only: a converged file
            # yields no commands even if its mode drifted.
            yield op(
                files.install,
                name="Set JAVA_HOME if configured",
                content=plan.java_home_content,
                dest=JAVA_HOME_PROFILE,
                mode="0644",
                sudo=True,
            )
            # The paired files.file converges the mode:0644 the template task
            # enforces on every run -- the apache precedent's ports.conf
            # companion. A no-op when the mode already matches.
            yield op(
                files.file,
                name="Set JAVA_HOME profile script mode",
                path=JAVA_HOME_PROFILE,
                mode="0644",
                sudo=True,
            )

    return deploy


# --- the role ----------------------------------------------------------------


def java(v: VarsSet[JavaVars]) -> Deploy:
    """Install the Ubuntu default OpenJDK and optionally export JAVA_HOME.

    Args:
        v: The role's resolved variables, one :class:`JavaVars` per host
            (``resolve_vars(JavaVars, "java", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs -- no
        other role consumes them -- so it returns ``None`` on every path,
        including the family guard's early return.

    Raises:
        ValueError: The gathered distribution is not Ubuntu, carries no
            readable major version, or names a release the role ships no
            ``vars/Ubuntu-<major>.yml`` for. Each is raised in the body,
            immediately after the ``LinuxDistribution`` barrier and before any
            request is built, so an unsupported host fails without mutating
            anything.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        # Wave 0: the include_vars dispatch source, and the only proof that
        # the declared family matches the machine. Both error paths raise
        # here, at the barrier -- the role's only error paths, and neither
        # leaves a half-applied host because no request has been built yet.
        info = yield fact(LinuxDistribution)
        plan = java_plan(hv, info)

        # Wave 1: the whole role in one request wave. No checkpoint follows:
        # nothing reads an outcome, and a checkpoint on an unread report would
        # only add a barrier-only wave (design doc section 4.3).
        yield from install(plan)(host)

    return deploy


__all__ = [
    "JavaVars",
    "java",
]
