# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The ClamAV role (migrated from ``geerlingguy.clamav``).
#
# Installs and configures ClamAV on Ubuntu and RHEL-family hosts: the
# package set (the ``clamav`` / ``clamav-base`` / ``clamav-daemon`` trio on
# Debian, the ``clamav`` / ``clamav-update`` / ``clamav-scanner-systemd`` /
# ``clamav-data`` quartet on RedHat), the post-install ``freshclam`` run
# gated on the install's own change, the ``lineinfile`` edits of the
# family's daemon config (and the optional ``freshclam.conf`` ones), the two
# units' running/enabled state, and the role's single ``restart clamav
# daemon`` handler. On a RedHat host the stream also carries
# ``setup-RedHat.yml``: the static ``clamd-freshclam.service`` unit file and
# the change-gated ``daemon_reload``/stop that follows it (D8).
# :func:`clamav` is the whole task list -- ``tasks/main.yml`` plus
# ``tasks/setup-vars.yml`` -- and the role's variables are
# :class:`ClamavVars`, bound from the inventory entry's ``clamav`` role
# mapping by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` or ``"RedHat"`` OS family -- the two
# families upstream ships ``vars/`` files for. The role's ``include_vars``
# OS dispatch collapses to the frozen per-family constant tables below
# (D1, D7). A host outside both families (e.g. Alpine, where the role cannot
# even run -- there is no ``vars/Alpine.yml``) returns before its first
# ``yield``: no requests, no barriers, no log lines.
#
# The stream carries four waves on a fresh Ubuntu host (design doc section
# 5.7) and five on a fresh RedHat one (the extra :func:`setup_unit` wave,
# D8):
#
# 0. :func:`install` -- the packages (``apt`` or ``dnf``, D2) -- closed by
#    ``checkpoint("packages installed")``, which feeds the role's
#    ``register: clamav_packages_install`` + ``when: …changed`` gate.
# 1. the ``freshclam`` command -- an :func:`~reemote.stages.act` barrier, only
#    on a host whose install wave changed; a converged host parks on nothing
#    here, so the wave count is host-dependent.
# 1'. RedHat only: :func:`setup_unit` -- the ``clamd-freshclam.service``
#     file -- closed by ``checkpoint("unit installed")``, which gates the
#     :func:`reload_unit` request (stop + fact-gated daemon-reload) when the
#     file changed.
# 2. :func:`configure` -- the two config files' transforms, the mode companion,
#    both unit states -- closed by ``checkpoint("configure done")``, the
#    handler notification read.
# 3. :func:`handler` -- ``Restart <daemon> (handler)``, only on the hosts the
#    notification selected and only while ``daemon_state`` is not
#    ``"stopped"``.
#
# A converged rerun is two waves on Ubuntu (the freshclam act disappears,
# the handler wave disappears) and three on RedHat (the unit file stays, and
# so does its checkpoint, but nothing behind it fires).
#
# :func:`clamav` returns ``None``: no composing role in the corpus consumes an
# output, so there is no outputs dataclass (§17.2).
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: the host-family guard is the body's opening ``if``, not a ``when=`` on
#   every request: a host outside the ``"Ubuntu"``/``"RedHat"`` families
#   returns before it yields anything. Alpine is out -- upstream's
#   ``include_vars`` has no ``vars/Alpine.yml`` to load there.
# * D2: the ``with_items`` loop of one ``package: name=<item>`` task collapses
#   to a single request on the family's own manager -- ``apt.packages`` on
#   Ubuntu, ``dnf.packages`` on RedHat (the generic Ansible ``package``
#   module): the per-package ``register.changed`` becomes the union --
#   freshclam runs when *any* package installed, which is the same effective
#   gate for the play that followed the loop.
# * D3: ``command: freshclam`` is an :func:`~reemote.stages.act` barrier whose
#   ``Result`` the body interprets. The role's ``failed_when`` list is an
#   ``any`` of ``is failed`` OR ``'locked by another process' in stderr`` --
#   inverted from the task comment's intent to tolerate the lock failure -- and
#   is reproduced *literally*: the body raises the typed
#   :class:`~reemote.hosts.CommandError` on a non-zero exit or on that message.
#   A run that reaches the next line is a success; the ``command`` module
#   always reports ``changed``, so "it ran" queues the restart handler.
# * D4: the ``lineinfile`` loops become :func:`~reemote.modules.files.transform`
#   requests -- pure fixpoint transforms (drop matching lines / rewrite the
#   last match, appending when none) -- with ``create: yes`` free
#   (``transform(None)`` produces the creation content). The task's
#   ``mode: 0644`` rides the transform *and* a paired ``files.file`` request,
#   because transform idempotence is content-only; the freshclam.conf task
#   names no mode, so the operation's ``0644`` default (what Debian ships)
#   stands. The transform requests are named per regexp because they are the
#   handler's notification channel.
# * D5: the handler's ``when: clamav_daemon_state != 'stopped'`` is a plain
#   ``if`` in the body -- a static-variable gate needs no request-level ``when``.
# * D6: ``when: not ansible_check_mode`` on the two service tasks has no
#   analogue: a ``dry`` preview parks on the *first* checkpoint, before the
#   configure wave that holds them is ever built.
# * D7: the path knobs commented out in ``defaults/main.yml``
#   (``clamav_daemon_config_path``, ``clamav_freshclam_daemon_config_path``,
#   ``clamav_daemon_localsocket``) are frozen per-family module constants,
#   exactly as the README states: "hardcoded specifically for each OS family
#   and cannot be overridden". The default daemon-config rules interpolate
#   the family's frozen localsocket, so the role's
#   ``{{ clamav_daemon_localsocket }}`` render needs no template engine.
# * D8: ``setup-RedHat.yml`` maps task-for-task on the RedHat path. The
#   ``template`` task ships **zero Jinja expressions**, so the unit file is a
#   frozen string written through the spool family (:func:`setup_unit` --
#   ``files.install``, content-idempotent) instead of a renderer; its
#   ``register.changed`` gate becomes ``checkpoint("unit installed")`` +
#   :func:`reload_unit`, one ``systemd.service`` request carrying both halves
#   of the upstream task (``state: stopped`` -> ``running=False``,
#   ``daemon_reload: true`` -> ``daemon_reload=True``). Unlike Ansible's
#   unconditional ``systemctl daemon-reload``, the reload half is fact-gated
#   on ``SystemdNeedDaemonReload`` (issues #50/#75): it fires when the
#   manager's loaded state is stale or shadowed and stays silent on a
#   converged run -- on a real host a *fresh* unit is not even pending
#   (systemd loads a new file lazily on ``start``/``enable``), while
#   SimulatedHost keeps its permissive marking for a unit with no loaded
#   state modelled, so a fresh install's offline tree shows the reload and
#   the converged rerun emits no request at all (the checkpoint gates it).
# """

import re
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from ..actions import Shell
from ..core import op
from ..handlers import changed_hosts, restart_handler
from ..hosts import CommandError, Host
from ..modules import apt, dnf, files, systemd
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, act, checkpoint

# --- role constants (defaults/main.yml + vars/*.yml, both target families) ---

#: ``__clamav_packages`` (Debian), used when :attr:`ClamavVars.packages` is
#: null -- the role's ``set_fact`` fallback for an undefined
#: ``clamav_packages``.
DEBIAN_PACKAGES = ("clamav", "clamav-base", "clamav-daemon")

#: ``__clamav_daemon`` / ``__clamav_freshclam_daemon`` (Debian): the unit names
#: the two service tasks and the handler act on.
DEBIAN_DAEMON = "clamav-daemon"
DEBIAN_FRESHCLAM_DAEMON = "clamav-freshclam"

#: ``__clamav_packages`` (RedHat): the scanner split -- ``clamd@scan`` is a
#: template-unit instance of ``clamav-scanner-systemd``'s ``clamd@.service``
#: fed by ``/etc/sysconfig/clamd.scan``.
REDHAT_PACKAGES = ("clamav", "clamav-update", "clamav-scanner-systemd", "clamav-data")

#: ``__clamav_daemon`` / ``__clamav_freshclam_daemon`` (RedHat): the second is
#: the unit this role *creates* (D8); the first is an ``clamd@`` instance.
REDHAT_DAEMON = "clamd@scan"
REDHAT_FRESHCLAM_DAEMON = "clamd-freshclam"

#: ``clamav_daemon_localsocket`` / the two config paths, frozen per family
#: from ``vars/Debian.yml`` and ``vars/RedHat.yml`` (D7). The localsocket
#: builds the default ``LocalSocket`` line exactly as the role's Jinja
#: interpolation does.
LOCAL_SOCKET = "/var/run/clamav/clamd.ctl"
CLAMD_CONF = "/etc/clamav/clamd.conf"
FRESHCLAM_CONF = "/etc/clamav/freshclam.conf"

REDHAT_LOCAL_SOCKET = "/var/run/clamd.scan/clamd.sock"
REDHAT_CLAMD_CONF = "/etc/clamd.d/scan.conf"
REDHAT_FRESHCLAM_CONF = "/etc/freshclam.conf"

#: The ``mode: 0644`` the daemon-config ``lineinfile`` task asserts (D4) and
#: the ``template:`` task's mode on the unit file (D8).
CONFIG_MODE = "0644"

#: Where ``setup-RedHat.yml`` installs the freshclam unit, and the verbatim
#: body of ``templates/clamd-freshclam.service.j2` -- which carries no Jinja
#: expressions at all, so it freezes into a string (D8).
FRESHCLAM_UNIT_PATH = "/lib/systemd/system/clamd-freshclam.service"
FRESHCLAM_UNIT = """# Run freshclam as a daemon.
[Unit]
Description = ClamAV Freshclam service.
After = network.target

[Service]
Type = forking
ExecStart = /usr/bin/freshclam --daemon --checks 2
Restart = on-failure
PrivateTmp = true

[Install]
WantedBy=multi-user.target
"""

#: The stable ``register: clamav_packages_install`` channel: the install
#: request the ``checkpoint("packages installed")`` gate selects on. A rename
#: silently empties the freshclam gate.
INSTALL_NAME = "Ensure ClamAV packages are installed"

#: The ``command: freshclam`` barrier's log label.
FRESHCLAM_NAME = "Run freshclam after ClamAV packages change"

#: The role's ``failed_when`` marker string, reproduced literally (D3).
LOCK_MESSAGE = "locked by another process"


class ConfigChange(BaseModel):
    """One ``lineinfile`` item: the regexp, the line, and the state.

    ``line`` defaults to ``""`` -- the role's ``item.line | default('')`` --
    and ``state`` to ``"present"`` (its ``item.state | default('present')``).
    The model is frozen so the containing :class:`ClamavVars` stays hashable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    regexp: str
    line: str = ""
    state: Literal["present", "absent"] = "present"


#: ``clamav_daemon_configuration_changes`` from ``defaults/main.yml``, with the
#: role's ``{{ clamav_daemon_localsocket }}`` rendered from the frozen Debian
#: constant (D7).
DEFAULT_DAEMON_CHANGES = (
    ConfigChange(regexp=r"^.*Example$", state="absent"),
    ConfigChange(regexp=r"^.*LocalSocket .*$", line=f"LocalSocket {LOCAL_SOCKET}"),
)

#: The same two rules with the RedHat localsocket (D7, D8): upstream defines
#: the rules once and the *path* they interpolate dispatches per family.
REDHAT_DAEMON_CHANGES = (
    ConfigChange(regexp=r"^.*Example$", state="absent"),
    ConfigChange(
        regexp=r"^.*LocalSocket .*$", line=f"LocalSocket {REDHAT_LOCAL_SOCKET}"
    ),
)


@dataclass(frozen=True)
class FamilyProfile:
    """One family's frozen ``vars/<Family>.yml`` plus its package manager.

    The ``include_vars`` dispatch target: every value the body and the
    deploys need is answered from this table before the first request, so no
    fact barrier is required to pick the family (D1, D7).
    """

    packages: tuple[str, ...]
    daemon: str
    freshclam_daemon: str
    clamd_conf: str
    freshclam_conf: str
    default_daemon_changes: tuple[ConfigChange, ...]
    installer: Callable[..., Any]


#: The two families the role ships vars files for; anything else (Alpine)
#: never reaches a request (D1).
FAMILIES: dict[str, FamilyProfile] = {
    "Ubuntu": FamilyProfile(
        packages=DEBIAN_PACKAGES,
        daemon=DEBIAN_DAEMON,
        freshclam_daemon=DEBIAN_FRESHCLAM_DAEMON,
        clamd_conf=CLAMD_CONF,
        freshclam_conf=FRESHCLAM_CONF,
        default_daemon_changes=DEFAULT_DAEMON_CHANGES,
        installer=apt.packages,
    ),
    "RedHat": FamilyProfile(
        packages=REDHAT_PACKAGES,
        daemon=REDHAT_DAEMON,
        freshclam_daemon=REDHAT_FRESHCLAM_DAEMON,
        clamd_conf=REDHAT_CLAMD_CONF,
        freshclam_conf=REDHAT_FRESHCLAM_CONF,
        default_daemon_changes=REDHAT_DAEMON_CHANGES,
        installer=dnf.packages,
    ),
}


class ClamavVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``clamav`` role mapping, so ``daemon_state`` there sets
    :attr:`daemon_state`. The ``set_fact`` fallbacks of
    ``tasks/setup-vars.yml`` become the ``None`` defaults of :attr:`daemon`,
    :attr:`freshclam_daemon`, :attr:`packages` and
    :attr:`daemon_configuration_changes`: an operator override wins, an
    undefined value keeps the family's :data:`FAMILIES` answer. The path
    knobs are not fields at all -- the README states they cannot be
    overridden (D7).

    Sequences are ``tuple``, never ``list``: the model is frozen and hashable,
    and a value that reaches :func:`~reemote.core.op` must hash (design doc
    section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``null`` falls back to the family's ``__clamav_packages``, as the
    #: role's ``set_fact`` does for an undefined ``clamav_packages``.
    packages: tuple[str, ...] | None = None
    #: ``null`` falls back to the family's ``__clamav_daemon``.
    daemon: str | None = None
    #: ``null`` falls back to the family's ``__clamav_freshclam_daemon``.
    freshclam_daemon: str | None = None
    daemon_state: Literal["started", "stopped", "restarted", "reloaded"] = "started"
    daemon_enabled: bool = True
    freshclam_daemon_state: Literal["started", "stopped", "restarted", "reloaded"] = (
        "started"
    )
    freshclam_daemon_enabled: bool = True
    #: ``clamav_daemon_configuration_changes``: ``None`` keeps the family's
    #: two default rules; overriding the tuple replaces them wholesale, as
    #: in Ansible.
    daemon_configuration_changes: tuple[ConfigChange, ...] | None = None
    #: ``clamav_freshclam_configuration_changes``: *undefined* in the role
    #: (the ``when: … is defined`` gate), so the default is ``None`` and the
    #: freshclam.conf loop is skipped.
    freshclam_configuration_changes: tuple[ConfigChange, ...] | None = None


#: One resolved ``lineinfile`` item: its regexp (the name ingredient) and the
#: stable pure transform a request carries.
ChangeRule = tuple[str, Callable[[str | None], str]]


@dataclass(frozen=True)
class ClamavPlan:
    """One host's resolved targets, built by :func:`configure_plan`.

    Every value the family needs -- the package set and its manager, the two
    config paths, the change-rule sets with their frozen transforms, the
    unit names and states -- is decided before the first request, so every
    stage is a pure function of this one frozen object.
    """

    installer: Callable[..., Any]
    packages: tuple[str, ...]
    clamd_conf: str
    freshclam_conf: str
    daemon: str
    freshclam_daemon: str
    daemon_state: str
    daemon_enabled: bool
    freshclam_daemon_state: str
    freshclam_daemon_enabled: bool
    daemon_changes: tuple[ChangeRule, ...]
    freshclam_changes: tuple[ChangeRule, ...]


# --- pure helpers ------------------------------------------------------------


def lineinfile_transform(regexp: str, line: str) -> Callable[[str | None], str]:
    """Ansible ``lineinfile {regexp, line, state: present}`` as a pure transform.

    Same contract as the Apache role's ports edits: when the exact ``line`` is
    already present the content is returned verbatim; otherwise the LAST line
    matching ``regexp`` is replaced, or ``line`` is appended when none matched;
    a write ends with exactly one trailing newline. The result is a fixpoint:
    ``transform(transform(x)) == transform(x)``.
    """
    pattern = re.compile(regexp)

    def transform(current: str | None) -> str:
        text = current if current is not None else ""
        lines = text.splitlines()
        if line in lines:
            return text
        last = None
        for index, candidate in enumerate(lines):
            if pattern.search(candidate):
                last = index
        if last is not None:
            lines[last] = line
        else:
            lines.append(line)
        return "\n".join(lines) + "\n"

    return transform


def lineinfile_absent_transform(regexp: str) -> Callable[[str | None], str]:
    """Ansible ``lineinfile {regexp, state: absent}`` as a pure transform.

    Drops every line matching ``regexp``. A file with no match is returned
    verbatim -- the content (and any trailing-newline style) is never rewritten
    for nothing -- and the result is a fixpoint like the present form.
    """
    pattern = re.compile(regexp)

    def transform(current: str | None) -> str:
        text = current if current is not None else ""
        lines = text.splitlines()
        kept = [candidate for candidate in lines if not pattern.search(candidate)]
        if len(kept) == len(lines):
            return text
        return "\n".join(kept) + "\n" if kept else ""

    return transform


def change_transform(change: ConfigChange) -> Callable[[str | None], str]:
    """The frozen transform for one :class:`ConfigChange`, built per host."""
    if change.state == "absent":
        return lineinfile_absent_transform(change.regexp)
    return lineinfile_transform(change.regexp, change.line)


def daemon_change_name(regexp: str) -> str:
    """The stable name of one clamd.conf edit -- the ``notify`` channel."""
    return f"Change configuration for the ClamAV daemon ({regexp})"


def freshclam_change_name(regexp: str) -> str:
    """The name of one freshclam.conf edit (no ``notify`` in the role)."""
    return f"Change configuration for the freshclam daemon ({regexp})"


#: The paired ``files.file`` request that converges the ``mode: 0644`` the
#: daemon-config task asserts (D4). Named -- and notifying -- because the
#: Ansible task would have queued the handler on a mode-only change too.
MODE_NAME = "Set ClamAV daemon configuration mode"

#: ``setup-RedHat.yml``'s two task names (D8): the unit file's ``register:
#: freshclam_service_template`` channel and the reload task. The reload is
#: named -- and gate-selected -- but never notifies, faithful to the role.
UNIT_NAME = "Ensure Freshclam service is available"
RELOAD_NAME = "Reload systemd after adding service"

#: The two service tasks' names; neither carries a ``notify`` in the role, so
#: both stay out of :func:`notifying_names`.
DAEMON_SERVICE_NAME = "Ensure ClamAV daemon is running (if configured)"
FRESHCLAM_SERVICE_NAME = "Ensure ClamAV freshclam daemon is running (if configured)"


def configure_plan(v: ClamavVars, family: str) -> ClamavPlan:
    """Freeze every read the stream needs, answered from the family's table.

    The ``set_fact`` fallbacks resolve against :data:`FAMILIES` here (the
    ``include_vars`` dispatch, D1/D7), and the role's two ``lineinfile``
    loops become stable pure transforms -- the callables a request carries
    are built once per host here, so they survive the wave's hashing.
    """
    profile = FAMILIES[family]
    return ClamavPlan(
        installer=profile.installer,
        packages=v.packages or profile.packages,
        clamd_conf=profile.clamd_conf,
        freshclam_conf=profile.freshclam_conf,
        daemon=v.daemon or profile.daemon,
        freshclam_daemon=v.freshclam_daemon or profile.freshclam_daemon,
        daemon_state=v.daemon_state,
        daemon_enabled=v.daemon_enabled,
        freshclam_daemon_state=v.freshclam_daemon_state,
        freshclam_daemon_enabled=v.freshclam_daemon_enabled,
        daemon_changes=tuple(
            (change.regexp, change_transform(change))
            for change in (
                v.daemon_configuration_changes or profile.default_daemon_changes
            )
        ),
        freshclam_changes=tuple(
            (change.regexp, change_transform(change))
            for change in v.freshclam_configuration_changes or ()
        ),
    )


def notifying_names(plan: ClamavPlan) -> tuple[str, ...]:
    """Every configure request name that queues ``restart clamav daemon``.

    Enumerated from the same frozen plan the deploy was built from, so the
    notification set matches the requests exactly. The freshclam.conf edits
    carry no ``notify`` in the role and the service tasks never do, so their
    names are deliberately absent; the ``freshclam`` command's notification is
    not a request and is added by the body (D3).
    """
    names = [daemon_change_name(regexp) for regexp, _transform in plan.daemon_changes]
    names.append(MODE_NAME)
    return tuple(names)


# --- helper deploys ----------------------------------------------------------


def install(plan: ClamavPlan) -> Deploy:
    """``tasks/main.yml``'s package loop, as one request on the family's
    manager (D2).

    The generic Ansible ``package`` module resolves to ``apt`` on Ubuntu and
    ``dnf`` on RedHat -- :func:`configure_plan` froze the choice with the
    package set. Neither role branch has a cache-refresh task, so none is
    invented here. Both managers are fact-gated on their installed-packages
    read: they report ``changed`` only when a name really installs, which is
    what makes the install checkpoint a sound read of the role's
    ``register`` + ``when`` pair.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            plan.installer,
            name=INSTALL_NAME,
            packages=plan.packages,
            sudo=True,
        )

    return deploy


def configure(plan: ClamavPlan) -> Deploy:
    """The two ``lineinfile`` loops and the two ``service`` tasks.

    The clamd.conf transforms (and their mode companion) are the handler's
    notification channel -- :func:`notifying_names`; the freshclam.conf
    transforms never notify, faithful to the role. Both service tasks map the
    ``state:`` variable to the ``systemd.service`` verbs; started/stopped are
    fact-gated and converge, restarted/reloaded are stateless and always
    report changed, faithful to Ansible's service module.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for regexp, transform in plan.daemon_changes:
            yield op(
                files.transform,
                name=daemon_change_name(regexp),
                path=plan.clamd_conf,
                transform=transform,
                mode=CONFIG_MODE,
                sudo=True,
            )
        yield op(
            files.file,
            name=MODE_NAME,
            path=plan.clamd_conf,
            mode=CONFIG_MODE,
            sudo=True,
        )
        for regexp, transform in plan.freshclam_changes:
            yield op(
                files.transform,
                name=freshclam_change_name(regexp),
                path=plan.freshclam_conf,
                transform=transform,
                sudo=True,
            )

        state = plan.daemon_state
        yield op(
            systemd.service,
            name=DAEMON_SERVICE_NAME,
            service=plan.daemon,
            running=state != "stopped",
            restarted=state == "restarted",
            reloaded=state == "reloaded",
            enabled=plan.daemon_enabled,
            sudo=True,
        )
        state = plan.freshclam_daemon_state
        yield op(
            systemd.service,
            name=FRESHCLAM_SERVICE_NAME,
            service=plan.freshclam_daemon,
            running=state != "stopped",
            restarted=state == "restarted",
            reloaded=state == "reloaded",
            enabled=plan.freshclam_daemon_enabled,
            sudo=True,
        )

    return deploy


def setup_unit(plan: ClamavPlan) -> Deploy:
    """``setup-RedHat.yml`` task 1: the ``clamd-freshclam.service`` file (D8).

    The upstream template carries no Jinja expressions, so its body is a
    frozen string (no renderer, no ``reemote[templates]``) written through
    the spool family: ``files.install`` reads the destination back and is
    content-idempotent, which is exactly the ``register:
    freshclam_service_template`` the reload task gates on. RedHat hosts
    only -- Ubuntu ships ``clamav-freshclam`` packaged and needs no unit.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            files.install,
            name=UNIT_NAME,
            content=FRESHCLAM_UNIT,
            dest=FRESHCLAM_UNIT_PATH,
            mode=CONFIG_MODE,
            user="root",
            group="root",
            sudo=True,
        )

    return deploy


def reload_unit(plan: ClamavPlan) -> Deploy:
    """``setup-RedHat.yml`` task 2: ``state: stopped`` + ``daemon_reload: true``.

    One request carries both halves of the upstream task, in the upstream
    order -- the fact-gated reload (issued only while
    ``SystemdNeedDaemonReload`` says pending, D8) before the gated stop.
    ``enabled`` is untouched, as upstream omits it. The request never
    notifies: the role's reload task carries no ``notify``, so its name
    stays out of :func:`notifying_names`.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            systemd.service,
            name=RELOAD_NAME,
            service=plan.freshclam_daemon,
            running=False,
            daemon_reload=True,
            sudo=True,
        )

    return deploy


def handler(plan: ClamavPlan, notified: frozenset[str]) -> Deploy:
    """``handlers/main.yml``: ``restart clamav daemon``.

    The role's handler is a ``service`` action, so the
    :func:`~reemote.handlers.restart_handler` factory applies (a *command*-bodied
    handler would need an ``act`` barrier instead). Its ``when:`` gate lives in
    the body, not here (D5). An empty ``notified`` set yields no request,
    which is the converged no-op Ansible's handler gives for free;
    ``restarted`` always reports changed, faithful to Ansible.
    """
    return restart_handler(notified, plan.daemon)


# --- the role ----------------------------------------------------------------


def clamav(v: VarsSet[ClamavVars]) -> Deploy:
    """Install and configure ClamAV on Ubuntu and RedHat hosts, and run its handler.

    Args:
        v: The role's resolved variables, one :class:`ClamavVars` per host
            (``resolve_vars(ClamavVars, "clamav", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves` that returns ``None`` for
        every host it runs -- no composing role in the corpus consumes an
        output (§17.2).

    Raises:
        CommandError: The ``freshclam`` act barrier exited non-zero or its
            stderr carried the lock message (D3). A barrier result is data, so
            the body turns it into the typed error itself; the driver
            propagates it and closes the hosts in its own ``finally``. Bad
            *variables* are rejected earlier, by
            :func:`~reemote.rolevars.resolve_vars`.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family not in FAMILIES:
            return None
        plan = configure_plan(hv, host.family)

        # Wave 0: the packages, closed by the checkpoint the freshclam gate
        # reads -- the role's `register: clamav_packages_install` +
        # `when: clamav_packages_install.changed`.
        yield from install(plan)(host)
        install_report = yield checkpoint("packages installed")

        # Wave 1 (conditional): freshclam, only where the install changed. On
        # a converged host nothing changes here, so this wave is never
        # appended and the run carries one wave less. A barrier never raises
        # on a non-zero exit -- the Result is data -- so the body interprets
        # it exactly as the role's `failed_when` does (D3), and a run that
        # reaches the next line queues the restart handler.
        ran_freshclam = False
        if host.name in changed_hosts(install_report, (INSTALL_NAME,)):
            result = yield act(
                Shell, command="freshclam", name=FRESHCLAM_NAME, sudo=True
            )
            if result["exit_status"] != 0 or LOCK_MESSAGE in result["stderr"]:
                raise CommandError(
                    host.name, "freshclam", result["exit_status"], result["stderr"]
                )
            ran_freshclam = True

        # RedHat only, ahead of the config edits like the include sits in
        # the task list (D8): the unit file, its checkpoint, and the
        # change-gated reload + stop that follows.
        if host.family == "RedHat":
            yield from setup_unit(plan)(host)
            unit_report = yield checkpoint("unit installed")
            if host.name in changed_hosts(unit_report, (UNIT_NAME,)):
                yield from reload_unit(plan)(host)

        # Configure and the service states, then read the
        # notification. The checkpoint must immediately follow the stage it
        # reports on; the freshclam run that also notified is a barrier, not a
        # request, so the body adds this host to the set itself (D3).
        yield from configure(plan)(host)
        wave_report = yield checkpoint("configure done")

        notified = set(changed_hosts(wave_report, notifying_names(plan)))
        if ran_freshclam:
            notified.add(host.name)

        # Wave 3: the handler, gated on the static state variable (D5) and
        # only on the hosts the notification selected.
        if hv.daemon_state != "stopped":
            yield from handler(plan, frozenset(notified))(host)

        return None

    return deploy


__all__ = [
    "ClamavVars",
    "ConfigChange",
    "clamav",
]
