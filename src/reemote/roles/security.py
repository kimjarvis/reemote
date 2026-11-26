# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The security role (migrated from ``geerlingguy.security``).
#
# Hardens Ubuntu hosts: the SSH daemon's configuration (the eight security
# directives plus optional ``AllowUsers``/``AllowGroups``), passwordless and
# passworded ``/etc/sudoers`` entries, Fail2Ban (package, ``jail.local``,
# ``fail2ban.local``, running and enabled on boot), and unattended-upgrades
# (package plus ``10periodic`` and ``50unattended-upgrades``). :func:`security` is
# the whole task list and :class:`SecurityVars` is ``defaults/main.yml``, bound
# from the inventory entry's ``security`` role mapping by
# :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. A host outside the target family
# returns before its first ``yield`` and emits no requests, no barriers and no log
# lines at all -- which is also how ``tasks/autoupdate-RedHat.yml``, the RedHat
# ``enablerepo: epel`` Fail2Ban install and ``vars/RedHat.yml`` are dropped
# (deviation D1).
#
# The stream is one request wave plus one checkpoint. Nothing on the Ubuntu path
# reads the host *between* tasks -- the role's only ``ansible_facts`` uses are the
# ``include_vars`` dispatch and two Debian-family version gates, both settled
# statically here -- so there is no ``fact``, ``act`` or ``control`` barrier. The
# single ``checkpoint("configure done")`` is the whole handler mechanism, read
# three times: the eight SSH directives notify ``reload systemd`` **and** ``restart
# ssh``, ``AllowUsers``/``AllowGroups`` notify only ``restart ssh``, and the two
# Fail2Ban files notify ``reload fail2ban``. The handler wave runs the role's
# handlers in definition order and collapses to nothing on a converged host, so the
# run is two waves changed and one wave converged.
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: ``include_vars``' OS dispatch becomes frozen module constants
#   (:data:`SSH_CONFIG_PATH`, :data:`SSHD_NAME`) plus the family guard. The RedHat
#   autoupdate file, the EPEL-scoped Fail2Ban install and the ``vars/RedHat.yml``
#   they rely on (unit ``sshd``) are not migrated.
# * D2: ``lineinfile`` becomes :func:`~reemote.modules.files.transform` -- the
#   spool-and-install edit of a root-owned file -- with each regexp/line pair
#   compiled into a pure fixpoint (:func:`lineinfile_transform`), plus one paired
#   :func:`~reemote.modules.files.file` request per edited file to converge its
#   ``mode``, because transform idempotence is content-only. ``validate: '… %s'``
#   becomes ``validate: '… {spool}'``, and ``create: true`` needs no analogue: a
#   transform of an absent file creates it.
# * D3: ``notify:`` becomes three ``changed_hosts`` channels over the *one*
#   configure report, and the handler deploy yields them in ``handlers/main.yml``'s
#   definition order (Ansible flushes handlers in that order, not notification
#   order). ``security_ssh_restart_handler_state`` maps onto
#   :func:`~reemote.handlers.service_handler`'s ``action``, with ``started`` →
#   ``"running"``.
# * D4: ``reload systemd`` is :func:`~reemote.modules.systemd.daemon_reload` with
#   **no unit**, the unconditional stateless form, because the handler passes
#   ``daemon_reload: true`` with no unit name. The state-gated
#   ``service_handler(daemon_reload=True)`` is deliberately *not* used: it reloads
#   only while a unit reports ``NeedDaemonReload``, and an ``sshd_config`` edit
#   touches no unit file. The notification gate is what makes it converge.
# * D5: all four templates render as plain f-strings -- no Jinja2, so no
#   ``reemote[templates]`` extra. ``${distro_codename}``/``${distro_id}`` stay
#   literal passthroughs (``unattended-upgrades`` substitutes them at run time, not
#   Ansible), and the ``distribution_major_version | int >= 12`` gates are
#   unconditionally true on Ubuntu (major version 22 or 24), so
#   ``backend = systemd`` and ``fail2ban.local`` are always written.
# * D6: ``security_fail2ban_custom_configuration_template`` accepts only the
#   bundled ``jail.local.j2``; any other value raises while the variables are
#   resolved. A custom template is a Jinja2 file, and this migration deliberately
#   takes on no template dependency (``apache`` and ``nginx``, which do render
#   custom templates, are the roles that carry the extra).
# * D7: the Ansible values that are *strings* stay strings --
#   ``security_ssh_password_authentication: "no"``,
#   ``security_autoupdate_reboot: "false"``, ``security_autoupdate_reboot_time:
#   "03:00"`` -- so a rendered line is byte-identical; ``security_ssh_port: 22`` is
#   coerced to ``"22"``, and the list vars freeze to tuples.
# * D8: the play-level ``become`` becomes an explicit ``sudo=True`` on every
#   request. The transfer half of an edit or a template stays unprivileged in the
#   ``/tmp`` spool; only the validation and the install elevate.
# * D9: ``service: name={{ security_sshd_name }}`` sets no ``enabled``, so the
#   request leaves the boot setting alone (``enabled=None``); only the Fail2Ban
#   task manages it. A ``started``/``stopped`` request is fact-gated and converges;
#   ``restarted``/``reloaded`` are stateless (always changed), faithful to Ansible's
#   ``service`` module.
# """

import re
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..handlers import changed_hosts, service_handler
from ..hosts import Host
from ..modules import apt, files, systemd
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, checkpoint

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path only) ---

#: ``vars/Debian.yml``'s ``security_ssh_config_path``, frozen (deviation D1).
SSH_CONFIG_PATH = "/etc/ssh/sshd_config"

#: ``vars/Debian.yml``'s ``security_sshd_name`` -- the unit the service task and
#: the ``restart ssh`` handler act on (RedHat's ``sshd`` is out of scope).
SSHD_NAME = "ssh"

#: The file both sudoers loops edit, and the modes their tasks carry.
SUDOERS_PATH = "/etc/sudoers"
SSHD_MODE = "0644"
SUDOERS_MODE = "0440"

#: The two Fail2Ban files and their directory (``tasks/fail2ban.yml``).
FAIL2BAN_DIR = "/etc/fail2ban"
JAIL_LOCAL_PATH = f"{FAIL2BAN_DIR}/jail.local"
FAIL2BAN_LOCAL_PATH = f"{FAIL2BAN_DIR}/fail2ban.local"

#: ``tasks/autoupdate-Debian.yml``'s template destinations.
APT_CONF_DIR = "/etc/apt/apt.conf.d"
PERIODIC_FILE = "10periodic"
UNATTENDED_FILE = "50unattended-upgrades"

#: The packages the two included task files install.
FAIL2BAN_PACKAGE = "fail2ban"
UNATTENDED_PACKAGE = "unattended-upgrades"

#: The role's two ``validate:`` commands, run against the spool that
#: :func:`~reemote.modules.files.transform` stages the candidate content in (D2).
#: Both read one config file, so both accept the ``/tmp`` spool, and a non-zero
#: exit aborts before the target is touched.
SSHD_VALIDATE = "sshd -T -f {spool}"
VISUDO_VALIDATE = "visudo -cf {spool}"

#: ``tasks/ssh.yml``'s loop, in order: the directive -- which is also its
#: ``regexp``'s body -- and the :class:`SecurityVars` field holding its value.
SSHD_ITEMS: tuple[tuple[str, str], ...] = (
    ("PasswordAuthentication", "ssh_password_authentication"),
    ("PermitRootLogin", "ssh_permit_root_login"),
    ("Port", "ssh_port"),
    ("UseDNS", "ssh_usedns"),
    ("PermitEmptyPasswords", "ssh_permit_empty_password"),
    ("ChallengeResponseAuthentication", "ssh_challenge_response_auth"),
    ("GSSAPIAuthentication", "ssh_gss_api_authentication"),
    ("X11Forwarding", "ssh_x11_forwarding"),
)

#: ``templates/10periodic.j2`` is a static file, carried verbatim.
PERIODIC_CONTENT = (
    'APT::Periodic::Update-Package-Lists "1";\n'
    'APT::Periodic::Download-Upgradeable-Packages "1";\n'
    'APT::Periodic::AutocleanInterval "7";\n'
    'APT::Periodic::Unattended-Upgrade "1";\n'
)

#: ``templates/fail2ban.local.j2``, verbatim (and unconditional on Ubuntu, D5).
FAIL2BAN_LOCAL_CONTENT = "[Definition]\nlogtarget = SYSTEMD-JOURNAL\n"

#: The literal ``Origins-Pattern`` entries of the unattended-upgrades template.
#: ``${distro_codename}``/``${distro_id}`` are *not* interpolated here: apt
#: substitutes them when the package reads the file (deviation D5), so each line is
#: a plain string and the trailing comment line is where the loop's items go.
ORIGINS_PATTERN_LINES: tuple[str, ...] = (
    "    // Debian security repositories",
    '    "origin=Debian,codename=${distro_codename},label=Debian-Security";',
    '    "origin=Debian,codename=${distro_codename}-security,label=Debian-Security";',
    "",
    "    // Ubuntu security repository",
    '    "origin=Ubuntu,archive=${distro_codename}-security";',
    "",
    "    // Ubuntu ESM repositories",
    '    "origin=${distro_id}ESMApps,archive=${distro_codename}-apps-security";',
    '    "origin=${distro_id}ESM,archive=${distro_codename}-infra-security";',
    "",
    "    // Custom repositories",
)

# --- request names (the handler notification channels) -----------------------


def sshd_edit_name(directive: str) -> str:
    """The request name of one ``sshd_config`` directive edit.

    The Ansible task name plus the directive it writes, so a loop's ten items stay
    distinguishable in the report and each one is individually selectable as a
    notification channel member.
    """
    return f"Update SSH configuration to be more secure. ({directive})"


ALLOW_USERS_NAME = "Add configured users allowed to connect over ssh"
ALLOW_GROUPS_NAME = "Add configured groups allowed to connect over ssh"
JAIL_NAME = "Copy jail custom configuration file into place."
FAIL2BAN_LOCAL_NAME = "Copy fail2ban custom configuration file into place."

#: The Ansible task name both ``/etc/apt/apt.conf.d`` templates share; the file
#: name is appended to each request so the loop's two items stay distinguishable.
AUTOUPDATE_NAME = "Copy unattended-upgrades configuration files in place."

#: The ``files.file`` request that converges ``sshd_config``'s mode, standing in
#: for the ``mode: 0644`` every ``lineinfile`` task in the file carries (D2). It
#: belongs to the hardening channel, so a mode-only drift notifies -- as it does
#: upstream, where each of those tasks reports ``changed`` on a mode drift.
SSHD_MODE_NAME = "Set sshd_config mode"

#: The same for ``/etc/sudoers`, whose tasks notify nothing.
SUDOERS_MODE_NAME = "Set sudoers mode"


def sudoers_edit_name(kind: str, user: str) -> str:
    """The request name of one sudoers item (``kind`` is ``passwordless`` or
    ``passworded``, the two loops' Ansible task names)."""
    return f"Add configured user accounts to {kind} sudoers. ({user})"


# --- variables ---------------------------------------------------------------


class SecurityVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``security`` role mapping, so ``ssh_port`` there sets
    :attr:`ssh_port` (the upstream variable is ``security_ssh_port``; the role name
    already carries the prefix). The model is frozen, so it stays hashable through
    the deploy body, and sequences are tuples, never lists.

    ``security_ssh_config_path`` and ``security_sshd_name`` are deliberately
    **absent**: they come from ``vars/Debian.yml``, which ``include_vars`` sets
    above any inventory value, so they are frozen constants (deviation D1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", coerce_numbers_to_str=True)

    #: ``security_ssh_port``: the ``Port`` directive and the jail's ``port``.
    ssh_port: str = "22"
    #: ``security_ssh_password_authentication``: ``"yes"``/``"no"``, verbatim.
    ssh_password_authentication: str = "no"
    #: ``security_ssh_permit_root_login``.
    ssh_permit_root_login: str = "no"
    #: ``security_ssh_usedns``.
    ssh_usedns: str = "no"
    #: ``security_ssh_permit_empty_password``.
    ssh_permit_empty_password: str = "no"
    #: ``security_ssh_challenge_response_auth``.
    ssh_challenge_response_auth: str = "no"
    #: ``security_ssh_gss_api_authentication``.
    ssh_gss_api_authentication: str = "no"
    #: ``security_ssh_x11_forwarding``.
    ssh_x11_forwarding: str = "no"
    #: ``security_sshd_state``: the ``Ensure SSH daemon is running.`` task's
    #: ``state:``. Ansible's service vocabulary; anything else raises while the
    #: variables are resolved.
    sshd_state: Literal["started", "stopped", "restarted", "reloaded"] = "started"
    #: ``security_ssh_restart_handler_state``: the ``restart ssh`` handler's
    #: ``state:``. ``"started"`` maps onto the ``"running"`` start action (D3).
    ssh_restart_handler_state: Literal[
        "started", "restarted", "reloaded"
    ] = "restarted"
    #: ``security_ssh_allowed_users``: the ``AllowUsers`` line's members. An empty
    #: list means the line is not managed (the task's ``when``).
    ssh_allowed_users: tuple[str, ...] = ()
    #: ``security_ssh_allowed_groups``: the ``AllowGroups`` line's members.
    ssh_allowed_groups: tuple[str, ...] = ()
    #: ``security_sudoers_passwordless``: users granted ``NOPASSWD: ALL``.
    sudoers_passwordless: tuple[str, ...] = ()
    #: ``security_sudoers_passworded``: users granted ``ALL`` (password required).
    sudoers_passworded: tuple[str, ...] = ()
    #: ``security_autoupdate_enabled``: the gate on the whole Debian autoupdate
    #: file (``when: security_autoupdate_enabled | bool``).
    autoupdate_enabled: bool = True
    #: ``security_autoupdate_blacklist``: package names held back from upgrading.
    autoupdate_blacklist: tuple[str, ...] = ()
    #: ``security_autoupdate_additional_origins``: extra ``Origins-Pattern``
    #: entries, written verbatim into the file.
    autoupdate_additional_origins: tuple[str, ...] = ()
    #: ``security_autoupdate_reboot``: the string ``"true"``/``"false"`` that apt
    #: reads, not a Python bool (deviation D7).
    autoupdate_reboot: str = "false"
    #: ``security_autoupdate_reboot_time``.
    autoupdate_reboot_time: str = "03:00"
    #: ``security_autoupdate_mail_to``: empty means both Mail directives are
    #: omitted, as the template's outer ``{% if %}`` does.
    autoupdate_mail_to: str = ""
    #: ``security_autoupdate_mail_on_error``: adds ``MailOnlyOnError "true"``.
    autoupdate_mail_on_error: bool = True
    #: ``security_fail2ban_enabled``: the gate on the whole ``fail2ban.yml`` file.
    fail2ban_enabled: bool = True
    #: ``security_fail2ban_custom_configuration_template``: only the bundled
    #: ``jail.local.j2`` is migrated; any other value raises (deviation D6).
    fail2ban_custom_configuration_template: str = "jail.local.j2"

    @field_validator(
        "ssh_allowed_users",
        "ssh_allowed_groups",
        "sudoers_passwordless",
        "sudoers_passworded",
        "autoupdate_blacklist",
        "autoupdate_additional_origins",
        mode="before",
    )
    @classmethod
    def _normalize_sequence(cls, value: Any) -> Any:
        """A list freezes to a tuple; a bare string stays a string, so pydantic
        rejects it rather than iterating its characters."""
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @field_validator("ssh_allowed_users", "ssh_allowed_groups", mode="after")
    @classmethod
    def _validate_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Both lines are the list ``| join(' ')``, so a member must be one token."""
        for item in value:
            if not item or item.split() != [item]:
                raise ValueError(
                    f"an allowed user or group must be one token, got {item!r}"
                )
        return value

    @field_validator("sudoers_passwordless", "sudoers_passworded", mode="after")
    @classmethod
    def _validate_sudoers_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Each item is interpolated into ``^{{ item }}`` and a sudoers line, so it
        must be a plain name: whitespace would split the line, ``#``/newline would
        inject one."""
        for item in value:
            if not item or re.search(r"[\s:#]", item):
                raise ValueError(
                    f"a sudoers user or group must be a plain name, got {item!r}"
                )
        return value

    @field_validator("fail2ban_custom_configuration_template")
    @classmethod
    def _reject_custom_template(cls, value: str) -> str:
        """The one branch of the target path this migration does not take (D6)."""
        if value != "jail.local.j2":
            raise ValueError(
                f"security_fail2ban_custom_configuration_template {value!r} names a "
                "custom Jinja2 template, which this migration does not render: only "
                "the bundled jail.local.j2 is migrated (deviation D6)"
            )
        return value


# --- pure helpers ------------------------------------------------------------


def lineinfile_transform(regexp: str, line: str) -> Callable[[str | None], str]:
    """Ansible ``lineinfile {regexp, line, state: present}`` as a pure transform.

    Built once per host, in :func:`resolve_plan`, so the callable a request carries
    is stable for the whole run. Semantics, matching the module: when the exact
    ``line`` is already present the content is returned verbatim (no change);
    otherwise the LAST line matching ``regexp`` is replaced, or ``line`` is appended
    when none matched; a write ends with exactly one trailing newline. The result is
    a fixpoint: ``transform(transform(x)) == transform(x)``.
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


def render_jail_local(hv: SecurityVars) -> str:
    """``templates/jail.local.j2``, f-string rendered (deviation D5).

    The template's only conditional is the Debian-family ``major >= 12`` gate on
    ``backend = systemd``, which every supported Ubuntu satisfies, so the line is
    unconditional here.
    """
    return (
        "[sshd]\n"
        "enabled = true\n"
        f"port    = {hv.ssh_port}\n"
        "filter  = sshd\n"
        "backend = systemd\n"
    )


def render_unattended_upgrades(hv: SecurityVars) -> str:
    """``templates/50unattended-upgrades.j2``, f-string rendered.

    The blank-line layout is what Jinja2 produces with Ansible's whitespace
    defaults: a block tag's own newline is dropped, so an unset
    :attr:`SecurityVars.autoupdate_mail_to` leaves the template's two blank lines
    adjacent, and an empty loop leaves its comment line directly above the ``}``.
    """
    lines = [
        f'Unattended-Upgrade::Automatic-Reboot "{hv.autoupdate_reboot}";',
        f'Unattended-Upgrade::Automatic-Reboot-Time "{hv.autoupdate_reboot_time}";',
        "",
    ]
    if hv.autoupdate_mail_to:
        lines.append(f'Unattended-Upgrade::Mail "{hv.autoupdate_mail_to}";')
        if hv.autoupdate_mail_on_error:
            lines.append('Unattended-Upgrade::MailOnlyOnError "true";')
    lines.append("")
    lines.append("Unattended-Upgrade::Origins-Pattern {")
    lines.extend(ORIGINS_PATTERN_LINES)
    lines.extend(f'    "{origin}";' for origin in hv.autoupdate_additional_origins)
    lines.append("}")
    lines.append("")
    lines.append("Unattended-Upgrade::Package-Blacklist{")
    lines.extend(f'    "{package}";' for package in hv.autoupdate_blacklist)
    lines.append("}")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class Edit:
    """One ``lineinfile`` item, resolved: the request name, target and transform."""

    #: The stable request name -- an id of a handler notification channel.
    name: str
    #: The remote file the transform edits.
    path: str
    #: The exact line the transform makes present (kept for observability).
    line: str
    #: The pure fixpoint handed to :func:`~reemote.modules.files.transform`.
    transform: Callable[[str | None], str]


@dataclass(frozen=True)
class Template:
    """One ``template:`` task, resolved: the request name, destination and content.

    The content is already rendered control-side (D5), so the deploy body is one
    content-idempotent :func:`~reemote.modules.files.install` request.
    """

    name: str
    dest: str
    content: str


@dataclass(frozen=True)
class SecurityPlan:
    """One host's resolved targets: the states, the edits and the renders.

    The pure half of the role: everything a task decided from a variable is computed
    once here, before the first request is built, so a bad value raises with nothing
    applied and the deploy bodies hold no branching of their own beyond "is this
    list empty".
    """

    #: ``vars/Debian.yml``'s unit name (service task and ``restart ssh`` handler).
    sshd_name: str = SSHD_NAME
    #: ``security_sshd_state``, verbatim.
    sshd_state: str = "started"
    #: The ``restart ssh`` handler's :func:`~reemote.handlers.service_handler`
    #: action (D3).
    restart_action: str = "restarted"
    #: ``tasks/ssh.yml``'s eight hardening items.
    sshd_edits: tuple[Edit, ...] = ()
    #: The ``AllowUsers``/``AllowGroups`` items, each present only when its list is
    #: non-empty (the tasks' ``when``).
    ssh_access_edits: tuple[Edit, ...] = ()
    #: The two sudoers loops, passwordless items first (the file's task order).
    sudoers_edits: tuple[Edit, ...] = ()
    #: ``tasks/fail2ban.yml``'s two templates, empty when Fail2Ban is disabled.
    fail2ban_templates: tuple[Template, ...] = ()
    #: ``tasks/autoupdate-Debian.yml``'s two templates, empty when disabled.
    autoupdate_templates: tuple[Template, ...] = ()


def _edit(name: str, path: str, line: str) -> Edit:
    """One ``lineinfile`` item: the edit of *path* so that *line* is present.

    The regexp is the line's first token, anchored -- which is exactly what every
    task in the role writes (``^PasswordAuthentication``, ``^AllowUsers``,
    ``^{{ item }}`` for a sudoers name), so the derived pattern needs no extra
    argument to stay faithful to upstream.
    """
    return Edit(
        name=name,
        path=path,
        line=line,
        transform=lineinfile_transform(f"^{line.split()[0]}", line),
    )


def _access_edit(name: str, keyword: str, members: tuple[str, ...]) -> Edit | None:
    """The ``AllowUsers``/``AllowGroups`` item, or ``None`` when unmanaged."""
    if not members:
        return None
    return _edit(name, SSH_CONFIG_PATH, f"{keyword} {' '.join(members)}")


def resolve_plan(hv: SecurityVars) -> SecurityPlan:
    """Turn one host's variables into the frozen plan the deploy bodies consume.

    ``security_ssh_restart_handler_state: started`` becomes the ``"running"`` action
    :func:`~reemote.handlers.service_handler` understands; its two restart verbs pass
    through, and that function is what rejects anything else.
    """
    sshd_edits = tuple(
        _edit(
            sshd_edit_name(directive),
            SSH_CONFIG_PATH,
            f"{directive} {getattr(hv, field)}",
        )
        for directive, field in SSHD_ITEMS
    )
    access_edits = tuple(
        edit
        for edit in (
            _access_edit(ALLOW_USERS_NAME, "AllowUsers", hv.ssh_allowed_users),
            _access_edit(ALLOW_GROUPS_NAME, "AllowGroups", hv.ssh_allowed_groups),
        )
        if edit is not None
    )
    sudoers_edits = tuple(
        _edit(
            sudoers_edit_name(kind, user),
            SUDOERS_PATH,
            f"{user} ALL=(ALL) {suffix}",
        )
        for kind, members, suffix in (
            ("passwordless", hv.sudoers_passwordless, "NOPASSWD: ALL"),
            ("passworded", hv.sudoers_passworded, "ALL"),
        )
        for user in members
    )
    fail2ban_templates = (
        ()
        if not hv.fail2ban_enabled
        else (
            Template(JAIL_NAME, JAIL_LOCAL_PATH, render_jail_local(hv)),
            Template(FAIL2BAN_LOCAL_NAME, FAIL2BAN_LOCAL_PATH, FAIL2BAN_LOCAL_CONTENT),
        )
    )
    autoupdate_templates = (
        ()
        if not hv.autoupdate_enabled
        else (
            Template(
                f"{AUTOUPDATE_NAME} ({PERIODIC_FILE})",
                f"{APT_CONF_DIR}/{PERIODIC_FILE}",
                PERIODIC_CONTENT,
            ),
            Template(
                f"{AUTOUPDATE_NAME} ({UNATTENDED_FILE})",
                f"{APT_CONF_DIR}/{UNATTENDED_FILE}",
                render_unattended_upgrades(hv),
            ),
        )
    )
    return SecurityPlan(
        sshd_state=hv.sshd_state,
        restart_action=(
            "running"
            if hv.ssh_restart_handler_state == "started"
            else hv.ssh_restart_handler_state
        ),
        sshd_edits=sshd_edits,
        ssh_access_edits=access_edits,
        sudoers_edits=sudoers_edits,
        fail2ban_templates=fail2ban_templates,
        autoupdate_templates=autoupdate_templates,
    )


def daemon_reload_names(plan: SecurityPlan) -> tuple[str, ...]:
    """The requests that notify ``reload systemd`` (and ``restart ssh``).

    ``tasks/ssh.yml``'s hardening loop is the only task that notifies both, so the
    set is its eight items plus the mode request that stands in for their
    ``mode: 0644`` (D2). Enumerated from the frozen plan, never from the inventory.
    """
    return tuple(edit.name for edit in plan.sshd_edits) + (SSHD_MODE_NAME,)


def restart_ssh_names(plan: SecurityPlan) -> tuple[str, ...]:
    """The requests that notify ``restart ssh``: the hardening loop's channel, plus
    the two ``AllowUsers``/``AllowGroups`` tasks (which notify only this one)."""
    return daemon_reload_names(plan) + tuple(edit.name for edit in plan.ssh_access_edits)


def reload_fail2ban_names(plan: SecurityPlan) -> tuple[str, ...]:
    """The two ``template:`` tasks that notify ``reload fail2ban``; empty when the
    whole ``fail2ban.yml`` file is gated off."""
    return tuple(template.name for template in plan.fail2ban_templates)


# --- the three task files, as sub-deploys ------------------------------------


def configure_fail2ban(plan: SecurityPlan) -> Deploy:
    """``tasks/fail2ban.yml`` (Debian path): the package, two files, service state.

    The RedHat install task's ``enablerepo: epel`` is out of scope (D1), so the
    Debian task is the only install, and the file yields nothing at all when
    ``security_fail2ban_enabled`` is false. Both templates notify ``reload
    fail2ban``; the final service task notifies nothing, and its ``started``/
    ``enabled`` are fact-gated, so a converged host reports no change.
    """
    def deploy(host: Host) -> Generator[StageItem, Any]:
        if not plan.fail2ban_templates:
            return None
        yield op(
            apt.packages,
            name="Install fail2ban (Debian).",
            packages=(FAIL2BAN_PACKAGE,),
            sudo=True,
        )
        for template in plan.fail2ban_templates:
            yield op(
                files.install,
                name=template.name,
                content=template.content,
                dest=template.dest,
                mode=SSHD_MODE,
                user="root",
                group="root",
                sudo=True,
            )
        yield op(
            systemd.service,
            name="Ensure fail2ban is running and enabled.",
            service=FAIL2BAN_PACKAGE,
            running=True,
            enabled=True,
            sudo=True,
        )

    return deploy


def configure_ssh(plan: SecurityPlan) -> Deploy:
    """``tasks/ssh.yml``: the service state, then the two protected config files.

    The role's own order is kept, including the service task *first* -- upstream
    brings the daemon up before it tightens the config, and the new settings take
    effect only when a handler restarts the unit. Every ``sshd_config`` edit carries
    the role's ``validate: 'sshd -T -f %s'`` and every ``/etc/sudoers`` edit
    ``validate: 'visudo -cf %s'``, so a rejected line never lands (D2). Only the
    sshd mode request notifies; the sudoers tasks do not (D3).
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        state = plan.sshd_state
        yield op(
            systemd.service,
            name="Ensure SSH daemon is running.",
            service=plan.sshd_name,
            running=state != "stopped",
            restarted=state == "restarted",
            reloaded=state == "reloaded",
            sudo=True,
        )
        for edit in (*plan.sshd_edits, *plan.ssh_access_edits):
            yield op(
                files.transform,
                name=edit.name,
                path=edit.path,
                transform=edit.transform,
                mode=SSHD_MODE,
                validate=SSHD_VALIDATE,
                sudo=True,
            )
        yield op(
            files.file,
            name=SSHD_MODE_NAME,
            path=SSH_CONFIG_PATH,
            mode=SSHD_MODE,
            sudo=True,
        )
        if plan.sudoers_edits:
            for edit in plan.sudoers_edits:
                yield op(
                    files.transform,
                    name=edit.name,
                    path=edit.path,
                    transform=edit.transform,
                    mode=SUDOERS_MODE,
                    validate=VISUDO_VALIDATE,
                    sudo=True,
                )
            yield op(
                files.file,
                name=SUDOERS_MODE_NAME,
                path=SUDOERS_PATH,
                mode=SUDOERS_MODE,
                sudo=True,
            )

    return deploy


def configure_autoupdate(plan: SecurityPlan) -> Deploy:
    """``tasks/autoupdate-Debian.yml``: the package and the two apt config files.

    Nothing here notifies -- neither task has a ``notify`` in the role, so no name of
    this stage appears in a handler channel. The two renders are content-idempotent
    installs, so a converged host reports no change.
    """
    def deploy(host: Host) -> Generator[StageItem, Any]:
        if not plan.autoupdate_templates:
            return None
        yield op(
            apt.packages,
            name="Install unattended upgrades package.",
            packages=(UNATTENDED_PACKAGE,),
            sudo=True,
        )
        for template in plan.autoupdate_templates:
            yield op(
                files.install,
                name=template.name,
                content=template.content,
                dest=template.dest,
                mode=SSHD_MODE,
                user="root",
                group="root",
                sudo=True,
            )

    return deploy


def handler(
    plan: SecurityPlan,
    daemon_reload_notified: frozenset[str],
    restart_notified: frozenset[str],
    reload_notified: frozenset[str],
) -> Deploy:
    """``handlers/main.yml``, flushed in definition order.

    Ansible runs notified handlers in the order they are *defined*, not the order
    they were notified, so the composed deploy yields ``reload systemd``, then
    ``restart ssh``, then ``reload fail2ban``. The first is a
    :func:`~reemote.modules.systemd.daemon_reload` request gated on the channel that
    notifies it (D4); the other two are :func:`~reemote.handlers.service_handler`
    deploys, which gate on their own notified sets. An empty notification yields no
    request -- the converged no-op Ansible's handler gives for free.
    """
    restart_ssh = service_handler(
        restart_notified, (plan.sshd_name,), action=plan.restart_action
    )
    reload_fail2ban = service_handler(
        reload_notified, (FAIL2BAN_PACKAGE,), action="reloaded"
    )

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if host.name in daemon_reload_notified:
            yield op(
                systemd.daemon_reload,
                name="Reload systemd (handler)",
                sudo=True,
            )
        yield from restart_ssh(host)
        yield from reload_fail2ban(host)

    return deploy


# --- the role ----------------------------------------------------------------


def security(v: VarsSet[SecurityVars]) -> Deploy:
    """Harden SSH, sudoers, Fail2Ban and unattended-upgrades, and run the handlers.

    Args:
        v: The role's resolved variables, one :class:`SecurityVars` per host
            (``resolve_vars(SecurityVars, "security", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves` that returns ``None`` for every host
        it runs -- no other role consumes an output, and the family guard's early
        return publishes it too.

    Raises:
        ValueError: A role variable is invalid -- an unknown service or handler
            state, an ``AllowUsers``/sudoers name that is not a plain token, a
            custom jail template, or a field the model does not have. Each is raised
            by pydantic while the variables are resolved, before any host is
            contacted.

    Note:
        A failing ``sshd -T -f`` or ``visudo -cf`` validation does not raise here:
        the driver aborts it as a :class:`~reemote.hosts.CommandError`, and the
        spooled content never lands on the target file.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return None

        # Everything the tasks decided from variables, decided once, before the
        # first request -- so a bad value raises with nothing applied.
        plan = resolve_plan(hv)

        # One wave, in the role's own file order (fail2ban.yml, ssh.yml,
        # autoupdate-Debian.yml). Requests do not close waves, and the driver
        # materializes and applies each one in its own step against a fresh fact
        # cache, so the ten sequential edits of one file compose: each transform
        # reads sshd_config as the previous request left it.
        yield from configure_fail2ban(plan)(host)
        yield from configure_ssh(plan)(host)
        yield from configure_autoupdate(plan)(host)

        # The checkpoint must immediately follow the stage it reports on, and it is
        # read three times -- three channels over the one report, never a second
        # checkpoint (§18.3, D3).
        configure_report = yield checkpoint("configure done")
        daemon_reload_notified = changed_hosts(
            configure_report, daemon_reload_names(plan)
        )
        restart_notified = changed_hosts(configure_report, restart_ssh_names(plan))
        reload_notified = changed_hosts(configure_report, reload_fail2ban_names(plan))

        # The handler wave: reload systemd, restart ssh, reload fail2ban, in
        # definition order, and only on the hosts each channel selected.
        yield from handler(
            plan, daemon_reload_notified, restart_notified, reload_notified
        )(host)

        return None

    return deploy


__all__ = ["SecurityVars", "security"]
