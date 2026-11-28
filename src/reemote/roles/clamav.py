# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The ClamAV role (migrated from ``geerlingguy.clamav``).
#
# Installs and configures ClamAV on Ubuntu hosts: the ``clamav`` /
# ``clamav-base`` / ``clamav-daemon`` packages, the post-install ``freshclam``
# run gated on the install's own change, the ``lineinfile`` edits of
# ``/etc/clamav/clamd.conf`` (and the optional ``freshclam.conf`` ones), the two
# units' running/enabled state, and the role's single ``restart clamav daemon``
# handler. :func:`clamav` is the whole task list -- ``tasks/main.yml`` plus
# ``tasks/setup-vars.yml`` -- and the role's variables are
# :class:`ClamavVars`, bound from the inventory entry's ``clamav`` role mapping
# by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. The role's RedHat branch
# (``setup-RedHat.yml``: the ``clamd-freshclam.service`` unit template plus its
# ``daemon_reload`` stop) is out of scope, so its ``include_vars`` OS dispatch
# collapses to the frozen Debian constants below (D1).
#
# The stream carries four waves on a fresh host (design doc section 5.7):
#
# 0. :func:`install` -- the packages -- closed by
#    ``checkpoint("packages installed")``, which feeds the role's
#    ``register: clamav_packages_install`` + ``when: …changed`` gate.
# 1. the ``freshclam`` command -- an :func:`~reemote.stages.act` barrier, only
#    on a host whose install wave changed; a converged host parks on nothing
#    here, so the wave count is host-dependent.
# 2. :func:`configure` -- the two config files' transforms, the mode companion,
#    both unit states -- closed by ``checkpoint("configure done")``, the
#    handler notification read.
# 3. :func:`handler` -- ``Restart clamav-daemon (handler)``, only on the hosts
#    the notification selected and only while ``daemon_state`` is not
#    ``"stopped"``.
#
# A converged rerun is two waves: the freshclam act disappears (nothing
# changed) and the handler wave disappears (nobody was notified).
#
# :func:`clamav` returns ``None``: no composing role in the corpus consumes an
# output, so there is no outputs dataclass (§17.2).
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: the host-family guard is the body's opening ``if``, not a ``when=`` on
#   every request: a host outside the ``"Ubuntu"`` family returns before it
#   yields anything. ``setup-RedHat.yml`` and its unit template are not
#   migrated.
# * D2: the ``with_items`` loop of one ``package: name=<item>`` task collapses
#   to a single ``apt.packages`` request: the per-package ``register.changed``
#   becomes the union -- freshclam runs when *any* package installed, which is
#   the same effective gate for the play that followed the loop.
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
#   ``clamav_daemon_localsocket``) are frozen module constants, exactly as the
#   README states: "hardcoded specifically for each OS family and cannot be
#   overridden". The default daemon-config rules interpolate the frozen
#   localsocket, so the role's ``{{ clamav_daemon_localsocket }}`` render needs
#   no template engine.
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
from ..modules import apt, files, systemd
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, act, checkpoint

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path only) ---

#: ``__clamav_packages`` (Debian), used when :attr:`ClamavVars.packages` is
#: null -- the role's ``set_fact`` fallback for an undefined
#: ``clamav_packages``.
DEBIAN_PACKAGES = ("clamav", "clamav-base", "clamav-daemon")

#: ``__clamav_daemon`` / ``__clamav_freshclam_daemon`` (Debian): the unit names
#: the two service tasks and the handler act on.
DEBIAN_DAEMON = "clamav-daemon"
DEBIAN_FRESHCLAM_DAEMON = "clamav-freshclam"

#: ``clamav_daemon_localsocket`` / the two config paths (Debian), frozen per
#: D7. The localsocket builds the default ``LocalSocket`` line exactly as the
#: role's Jinja interpolation does.
LOCAL_SOCKET = "/var/run/clamav/clamd.ctl"
CLAMD_CONF = "/etc/clamav/clamd.conf"
FRESHCLAM_CONF = "/etc/clamav/freshclam.conf"

#: The ``mode: 0644`` the daemon-config ``lineinfile`` task asserts (D4).
CONFIG_MODE = "0644"

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


class ClamavVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``clamav`` role mapping, so ``daemon_state`` there sets
    :attr:`daemon_state`. The ``set_fact`` fallbacks of
    ``tasks/setup-vars.yml`` become the defaults of :attr:`daemon`,
    :attr:`freshclam_daemon` and :attr:`packages`: an operator
    override wins, an undefined value keeps the Debian one. The path knobs are
    not fields at all -- the README states they cannot be overridden (D7).

    Sequences are ``tuple``, never ``list``: the model is frozen and hashable,
    and a value that reaches :func:`~reemote.core.op` must hash (design doc
    section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``null`` falls back to :data:`DEBIAN_PACKAGES`, as the role's
    #: ``set_fact`` does for an undefined ``clamav_packages``.
    packages: tuple[str, ...] | None = None
    daemon: str = DEBIAN_DAEMON
    freshclam_daemon: str = DEBIAN_FRESHCLAM_DAEMON
    daemon_state: Literal["started", "stopped", "restarted", "reloaded"] = "started"
    daemon_enabled: bool = True
    freshclam_daemon_state: Literal["started", "stopped", "restarted", "reloaded"] = (
        "started"
    )
    freshclam_daemon_enabled: bool = True
    #: ``clamav_daemon_configuration_changes``: the role's two default rules;
    #: overriding the list replaces them wholesale, as in Ansible.
    daemon_configuration_changes: tuple[ConfigChange, ...] = DEFAULT_DAEMON_CHANGES
    #: ``clamav_freshclam_configuration_changes``: *undefined* in the role
    #: (the ``when: … is defined`` gate), so the default is ``None`` and the
    #: freshclam.conf loop is skipped.
    freshclam_configuration_changes: tuple[ConfigChange, ...] | None = None


#: One resolved ``lineinfile`` item: its regexp (the name ingredient) and the
#: stable pure transform a request carries.
ChangeRule = tuple[str, Callable[[str | None], str]]


@dataclass(frozen=True)
class ClamavPlan:
    """One host's resolved configure targets, built by :func:`configure_plan`.

    Every value the configure wave and the handler need is decided before the
    wave's first request -- the two change-rule sets with their frozen
    transforms, the unit names and states -- so both stages are a pure
    function of this one frozen object.
    """

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

#: The two service tasks' names; neither carries a ``notify`` in the role, so
#: both stay out of :func:`notifying_names`.
DAEMON_SERVICE_NAME = "Ensure ClamAV daemon is running (if configured)"
FRESHCLAM_SERVICE_NAME = "Ensure ClamAV freshclam daemon is running (if configured)"


def configure_plan(v: ClamavVars) -> ClamavPlan:
    """Freeze every read the configure wave and the handler need.

    The role's two ``lineinfile`` loops become stable pure transforms -- the
    callables a request carries are built once per host here, so they survive
    the wave's hashing -- and the ``set_fact`` fallbacks have already resolved
    into the variables model.
    """
    return ClamavPlan(
        daemon=v.daemon,
        freshclam_daemon=v.freshclam_daemon,
        daemon_state=v.daemon_state,
        daemon_enabled=v.daemon_enabled,
        freshclam_daemon_state=v.freshclam_daemon_state,
        freshclam_daemon_enabled=v.freshclam_daemon_enabled,
        daemon_changes=tuple(
            (change.regexp, change_transform(change))
            for change in v.daemon_configuration_changes
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


def install(v: ClamavVars) -> Deploy:
    """``tasks/main.yml``'s package loop, as one request (D2).

    The generic Ansible ``package`` module is ``apt`` on the Ubuntu target.
    The role has no cache-refresh task, so none is invented here.
    ``apt.packages`` is fact-gated on ``AptPackages``: it reports ``changed``
    only when a name really installs, which is what makes the install
    checkpoint a sound read of the role's ``register`` + ``when`` pair.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            apt.packages,
            name=INSTALL_NAME,
            packages=v.packages or DEBIAN_PACKAGES,
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
                path=CLAMD_CONF,
                transform=transform,
                mode=CONFIG_MODE,
                sudo=True,
            )
        yield op(
            files.file,
            name=MODE_NAME,
            path=CLAMD_CONF,
            mode=CONFIG_MODE,
            sudo=True,
        )
        for regexp, transform in plan.freshclam_changes:
            yield op(
                files.transform,
                name=freshclam_change_name(regexp),
                path=FRESHCLAM_CONF,
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
    """Install and configure ClamAV on Ubuntu hosts, and run its handler.

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
        if host.family != "Ubuntu":
            return None

        # Wave 0: the packages, closed by the checkpoint the freshclam gate
        # reads -- the role's `register: clamav_packages_install` +
        # `when: clamav_packages_install.changed`.
        yield from install(hv)(host)
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

        # Wave 2: configure and the service states, then read the
        # notification. The checkpoint must immediately follow the stage it
        # reports on; the freshclam run that also notified is a barrier, not a
        # request, so the body adds this host to the set itself (D3).
        plan = configure_plan(hv)
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
