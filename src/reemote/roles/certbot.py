# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The Certbot role (migrated from ``geerlingguy.certbot``).
#
# Installs `Certbot <https://certbot.eff.org/>`_ on Ubuntu and RHEL-family
# hosts and, when configured, requests certificates (standalone or webroot
# method) and adds the automatic-renewal cron entry. :func:`certbot` is the
# whole task list and :class:`CertbotVars` is ``defaults/main.yml`` plus the
# ``vars/`` dispatch, bound from the inventory entry's ``certbot`` role
# mapping by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` or ``"RedHat"`` OS family -- the two
# families the role's task graph branches on (the apt/dnf package install and
# ``setup-RedHat.yml``). A host outside both families (e.g. Alpine) returns
# before its first ``yield`` and emits no requests, no barriers and no log
# lines at all.
#
# The stream is one deploy body carrying ``fact`` gates and ``act`` barriers --
# no checkpoint and no handler, because the role ships no ``handlers/main.yml``
# and nothing reads an outcome. With ``C`` configured certificates the wave
# count is ``2 + 2C`` (four with one cert, on a fresh host and on a converged
# one alike); a host that only installs and schedules collapses to two waves.
# The shape: one ``fact(LinuxDistribution)`` for the ``include_vars`` release
# dispatch; on a RedHat host the ``setup-RedHat.yml`` pair comes first -- the
# ``dnf-plugins-core`` request closed by the ``act(Shell, config-manager …
# crb)`` barrier (D8, skipped on Fedora, which collapses the setup back into
# the install wave); then the package-install request closed by the first
# cert's ``fact(FileExists)`` gate; then per cert the hook-directory/hook-file
# (standalone) or webroot-directory requests closed by the create action when
# the cert is absent, or by the ``act(Shell, certificates ...)`` domain probe
# (the role's ``changed_when: false`` read) with one further action wave when
# the domains changed; and finally the renewal-cron request wave. A converged
# host changes nothing and runs only the probes.
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: the play-level ``become`` becomes an explicit ``sudo=True`` on every
#   elevated request (the package install, the hook directories/files, the
#   webroot directory, the cron entry for a ``root`` renew user, and both
#   actions); the certificate ``stat`` stays unprivileged -- reemote's
#   ``FileExists`` is a ``test -e``, and ``/etc/letsencrypt`` is world-readable.
# * D2: the ``include_vars`` ``with_first_found`` dispatch becomes the
#   ``fact(LinuxDistribution)`` barrier plus per-family resolution (only
#   ``vars/Ubuntu-16.04.yml`` ships upstream, so the release table is
#   Ubuntu-only; every RedHat distribution reads ``vars/default.yml``'s
#   ``certbot``): a gathered distribution outside the host's declared family
#   raises (the java/repo_epel precedent), because a barrier result is data
#   and the raise is the role's.
# * D3: the snap and source install branches -- and with them
#   ``certbot_repo``/``certbot_version``/``certbot_keep_updated``/
#   ``certbot_dir`` -- are **rejected** at variable resolution; only
#   ``install_method: package`` is migrated (the Phase 0 scope).
# * D4: the two hook templates render as plain f-strings with the
#   ``service_mgr`` branch frozen to ``systemctl`` (every Ubuntu the role
#   supports runs systemd), and the ``{{ ansible_managed }}`` header becomes a
#   static reemote line -- no Jinja2, so no ``reemote[templates]`` extra.
# * D5: the cert-creation command is rebuilt by :func:`create_command` with
#   whitespace normalized (the YAML ``>-`` folding left runs of spaces around
#   every false Jinja conditional) and ``--expand`` emitted **once** (the
#   upstream template emits it twice); the full
#   ``certbot_create_command`` override knob is not modeled --
#   ``create_extra_args`` covers the additive use case.
# * D6: the cron task's ``user=`` becomes a privilege mapping
#   (:func:`cron_privilege`): the role's default (the connecting user) targets
#   the unprivileged request's table, ``root`` targets ``sudo=True``, and any
#   third user raises -- ``crontab.crontab`` deliberately has no ``user``
#   parameter (issue #46).
# * D7: ``deploy_hook`` presence is "non-empty": an explicitly empty
#   ``deploy_hook: ""`` skips the flag where the role's
#   ``'deploy_hook' in cert_item`` would still emit ``--deploy-hook ''``.
# * D8: ``setup-RedHat.yml`` maps task-for-task: the ``yum`` task becomes a
#   ``dnf.packages`` request, and the ``shell: dnf config-manager
#   --set-enabled crb`` task -- ``changed_when: false``, so the faithful
#   mapping is an ``act`` barrier (a read that never logs a change) rather
#   than a request, with the play's default failure semantics kept (a
#   non-zero exit raises). The Fedora gate reads the gathered distribution
#   ``name`` (Ansible's ``ansible_distribution``). Like upstream the step
#   only enables ``crb`` (Rocky/Alma 9+, issue #107): RHEL 8's ``powertools``
#   and the EPEL repository itself are not this role's job upstream either.
# """

from collections.abc import Generator
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ..actions import Shell
from ..core import op
from ..hosts import CommandError, Host
from ..modules import apt, crontab, dnf, files
from ..modules.files import FileExists
from ..modules.server import LinuxDistribution
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, act, fact

# --- role constants (defaults/main.yml + vars/*.yml, both target families) ----

#: ``vars/default.yml``'s ``certbot_package`` -- every Ubuntu release but 16.04,
#: and every RedHat distribution (no release-split vars file ships upstream).
DEFAULT_PACKAGE = "certbot"

#: ``setup-RedHat.yml`` task 1 (``yum: name=dnf-plugins-core``): provides
#: ``dnf config-manager``, which task 2 shells.
DNF_PLUGINS_PACKAGE = "dnf-plugins-core"

#: ``setup-RedHat.yml`` task 2 verbatim (``shell:``, ``changed_when: false``);
#: the ``when: ansible_distribution != 'Fedora'`` gate lives at the call site.
CRB_ENABLE_COMMAND = "dnf config-manager --set-enabled crb"

#: The ``include_vars`` dispatch table, frozen: only ``vars/Ubuntu-16.04.yml``
#: differs upstream (deviation D2). Keys are ``VERSION_ID``-style releases.
PACKAGE_BY_RELEASE: dict[str, str] = {"16.04": "letsencrypt"}

#: Where certbot keeps issued certificates (the ``stat`` gate's home).
LIVE_DIR = "/etc/letsencrypt/live"

#: The standalone-method pre/post hook directories and script paths
#: (``create-cert-standalone.yml`` and its two templates).
HOOK_PRE_DIR = "/etc/letsencrypt/renewal-hooks/pre"
HOOK_POST_DIR = "/etc/letsencrypt/renewal-hooks/post"
PRE_HOOK_PATH = f"{HOOK_PRE_DIR}/stop_services"
POST_HOOK_PATH = f"{HOOK_POST_DIR}/start_services"

#: The renewal cron entry's name (``renew-cron.yml``), kept verbatim;
#: ``crontab.crontab`` stores it as a ``# reemote-name=`` marker comment.
CRON_NAME = "Certbot automatic renewal."


# --- variables ---------------------------------------------------------------


class Cert(BaseModel):
    """One ``certbot_certs`` list entry (the role's ``cert_item``).

    Every field except :attr:`domains` is optional exactly as upstream's
    ``| default(...)`` chain reads it: an empty :attr:`name` derives from the
    first domain, an empty :attr:`email` falls back to
    :attr:`CertbotVars.admin_email`, an empty :attr:`webroot` to
    :attr:`CertbotVars.webroot`, and an empty :attr:`deploy_hook` means the
    flag is not emitted (deviation D7).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``cert_item.name``; the ``--cert-name`` when set, else derived.
    name: str = ""
    #: ``cert_item.email``; wins over the global ``certbot_admin_email``.
    email: str = ""
    #: ``cert_item.webroot``; the per-cert override of the global webroot.
    webroot: str = ""
    #: ``cert_item.domains`` -- required: the role's ``domains | first`` and
    #: ``join(',')`` both need it. Accepted as a list, frozen as a tuple.
    domains: tuple[str, ...] = ()
    #: ``cert_item.deploy_hook``; emitted single-quoted, verbatim.
    deploy_hook: str = ""

    @field_validator("domains", mode="before")
    @classmethod
    def _normalize_domains(cls, value: Any) -> Any:
        """A list becomes a tuple; a bare string is rejected as not-a-list."""
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def _domains_required(self) -> "Cert":
        """Every cert needs at least one non-blank domain (both usages ``first``
        and ``join`` the list, so an empty one is unusable upstream too)."""
        if not self.domains or not all(
            isinstance(domain, str) and domain.strip() for domain in self.domains
        ):
            raise ValueError(
                "each certbot_certs entry must list at least one non-empty domain"
            )
        return self


class CertbotVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``certbot`` role mapping, so ``admin_email`` there sets
    :attr:`admin_email`. The model is frozen, so it stays hashable through the
    deploy body (a :class:`~reemote.rolevars.VarsSet` is closed over by the
    factory), and sequences are tuples, never lists.

    The source-install knobs (``certbot_repo``, ``certbot_version``,
    ``certbot_keep_updated``, ``certbot_dir``) are deliberately **absent**: the
    branch is scoped out (deviation D3), and ``extra="forbid"`` rejects any
    attempt to set them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``certbot_auto_renew``: whether the renewal cron entry is managed.
    auto_renew: bool = True
    #: ``certbot_auto_renew_user``: ``""`` (the upstream default, the
    #: connecting user) targets the unprivileged crontab, ``"root"`` the
    #: privileged one; any other user raises (deviation D6).
    auto_renew_user: str = ""
    #: ``certbot_auto_renew_hour``: the cron hour (string, as upstream).
    auto_renew_hour: str = "3"
    #: ``certbot_auto_renew_minute``: the cron minute.
    auto_renew_minute: str = "30"
    #: ``certbot_auto_renew_options``: appended to ``certbot renew``.
    auto_renew_options: str = "--quiet"
    #: ``certbot_testmode``: add ``--test-cert`` (staging) to every create.
    testmode: bool = False
    #: ``certbot_hsts``: add ``--hsts`` to every create.
    hsts: bool = False
    #: ``certbot_expand``: add ``--expand`` to every create (emitted once,
    #: unlike the upstream template -- deviation D5).
    expand: bool = False
    #: ``certbot_create_if_missing``: the gate on the whole cert-creation loop.
    create_if_missing: bool = False
    #: ``certbot_create_method``: ``"standalone"`` or ``"webroot"``; anything
    #: else raises at resolution (the role's two include branches).
    create_method: str = "standalone"
    #: ``certbot_create_extra_args``: spliced verbatim into the create command.
    create_extra_args: str = ""
    #: ``certbot_admin_email``: the global ``--email`` default.
    admin_email: str = "email@example.com"
    #: ``certbot_webroot``: the global webroot, per-cert-overridable.
    webroot: str = "/var/www/letsencrypt"
    #: ``certbot_certs``: the certificates to create (empty by default, as
    #: upstream). Accepted as a list of ``{name, email, webroot, domains,
    #: deploy_hook}`` objects; frozen as :class:`Cert` models.
    certs: tuple[Cert, ...] = ()
    #: ``certbot_create_standalone_stop_services``: the services the pre/post
    #: hooks stop/start (standalone method only); a non-empty list gates both
    #: hook files *and* the ``--pre-hook``/``--post-hook`` flags, as upstream.
    create_standalone_stop_services: tuple[str, ...] = ("nginx",)
    #: ``certbot_install_method``: only ``"package"`` is migrated; ``"snap"``
    #: or ``"source"`` raises at resolution (deviation D3).
    install_method: str = "package"
    #: ``certbot_package``: explicit override of the release-dispatched
    #: package name; ``""`` consults :data:`PACKAGE_BY_RELEASE`.
    package: str = ""

    @field_validator("certs", mode="before")
    @classmethod
    def _normalize_certs(cls, value: Any) -> Any:
        """A list becomes a tuple; each item stays a dict for pydantic to bind."""
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @field_validator("create_standalone_stop_services", mode="before")
    @classmethod
    def _normalize_services(cls, value: Any) -> Any:
        """The commented-out-in-YAML list shape arrives as a list; freeze it."""
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def _reject_scoped_out(self) -> "CertbotVars":
        """Fail fast on the branches this migration scopes out, before any
        host is contacted: the snap and source installs (D3) and an unknown
        create method (the role's two mutually-exclusive include gates).
        """
        if self.install_method != "package":
            raise ValueError(
                f"certbot_install_method {self.install_method!r} is scoped out of "
                "this migration (only the 'package' install is migrated); the "
                "snap and source branches are documented as scoped out"
            )
        if self.create_method not in ("standalone", "webroot"):
            raise ValueError(
                "certbot_create_method must be 'standalone' or 'webroot', got "
                f"{self.create_method!r}"
            )
        return self


# --- pure helpers ------------------------------------------------------------


def cert_name(cert: Cert) -> str:
    """``cert_item.name | default(cert_item.domains | first | replace('*.', ''))``."""
    return cert.name or cert.domains[0].replace("*.", "")


def ubuntu_release(info: dict[str, Any]) -> str:
    """``ansible_facts.distribution_version`` from a LinuxDistribution read.

    Ubuntu's ``/etc/os-release`` carries ``VERSION_ID`` (e.g. ``"16.04"``);
    the fact exposes the upper-cased keys through ``release_meta``, and the
    numeric ``major``/``minor`` pair is the fallback. The role targets
    Ubuntu, so a host reporting another distribution is a hard error (the
    java precedent, deviation D2).
    """
    name = info.get("name")
    if name != "Ubuntu":
        raise ValueError(
            f"this role targets Ubuntu; the host reports {name!r} "
            "(LinuxDistribution.name) -- check the inventory family"
        )
    version = (info.get("release_meta") or {}).get("VERSION_ID")
    if isinstance(version, str) and version:
        return version
    major = info.get("major")
    if major is None:
        raise ValueError(
            "cannot read the Ubuntu release from the distribution facts "
            f"(release_meta={info.get('release_meta')!r}, major={major!r})"
        )
    minor = info.get("minor")
    if minor is None:
        return str(major)
    return f"{major}.{int(minor):02d}"


def resolve_package(hv: CertbotVars, info: dict[str, Any]) -> str:
    """The role's ``certbot_package`` (and its ``certbot_script`` set_fact),
    Ubuntu branch.

    An explicit :attr:`CertbotVars.package` wins; otherwise the frozen
    release table answers the ``include_vars`` dispatch (deviation D2). The
    package name *is* the script name on the package install path -- the
    role's ``set_fact: certbot_script={{ certbot_package }}``. RedHat hosts
    take the same two steps without the release table (there is no
    ``vars/RedHat*.yml`` upstream), which is :data:`DEFAULT_PACKAGE`.
    """
    if hv.package:
        return hv.package
    return PACKAGE_BY_RELEASE.get(ubuntu_release(info), DEFAULT_PACKAGE)


def render_service_hook(action: str, services: tuple[str, ...]) -> str:
    """``templates/stop_services.j2`` / ``templates/start_services.j2``.

    The templates are a shebang, an ``ansible_managed`` header and one
    ``echo`` + service-manager call per service; the ``service_mgr`` branch
    freezes to ``systemctl`` and the header becomes a static line (deviation
    D4). ``action`` is ``"stop"`` or ``"start"``, selecting the template and
    the echo's verb exactly as the two files differ.
    """
    verb = "stopping" if action == "stop" else "starting"
    lines = [
        "#!/bin/bash",
        "# Managed by reemote (migrated from geerlingguy.certbot)",
        "",
    ]
    for service in services:
        lines.append(f'echo "{verb} service {service}"')
        lines.append(f"systemctl {action} {service}")
    return "\n".join(lines) + "\n"


def domains_command(script: str, name: str) -> str:
    """The role's domain-probe ``shell:`` -- pipeline reproduced verbatim."""
    return (
        f"{script} certificates --cert-name {name} "
        "| grep -E '(Domains|Identifiers)' | cut -d':' -f2"
    )


def parse_domains(stdout: str) -> tuple[str, ...]:
    """The role's cleanup chain: ``trim | split(' ') | map('trim') |
    ``select('!=', '') | list | sort``, as a frozen tuple."""
    parts = (part.strip() for part in stdout.strip().split(" "))
    return tuple(sorted(part for part in parts if part))


def desired_domains(cert: Cert) -> tuple[str, ...]:
    """``cert_item.domains | map('trim') | select('!=', '') | list | sort``."""
    return tuple(sorted(domain.strip() for domain in cert.domains if domain.strip()))


def create_command(script: str, hv: CertbotVars, cert: Cert, name: str) -> str:
    """``defaults/main.yml``'s ``certbot_create_command``, rebuilt in Python.

    Same flags in the template's order; whitespace is normalized and
    ``--expand`` emitted once (deviation D5). ``--webroot-path`` and the
    ``--pre-hook``/``--post-hook`` pair follow the template's conditionals
    (method and the non-empty service list), and ``--deploy-hook`` keeps the
    template's single-quoting of the raw value.
    """
    parts = [script, "certonly", f"--{hv.create_method}"]
    if hv.hsts:
        parts.append("--hsts")
    if hv.testmode:
        parts.append("--test-cert")
    parts += ["--noninteractive", "--agree-tos"]
    parts += ["--email", cert.email or hv.admin_email]
    if hv.expand:
        parts.append("--expand")
    if hv.create_method == "webroot":
        parts += ["--webroot-path", cert.webroot or hv.webroot]
    if hv.create_extra_args:
        parts.append(hv.create_extra_args)
    parts += ["--cert-name", name, "-d", ",".join(cert.domains)]
    if hv.create_method == "standalone" and hv.create_standalone_stop_services:
        parts += ["--pre-hook", PRE_HOOK_PATH, "--post-hook", POST_HOOK_PATH]
    if cert.deploy_hook:
        parts.append(f"--deploy-hook '{cert.deploy_hook}'")
    return " ".join(" ".join(parts).split())


def renew_command(script: str, options: str) -> str:
    """The cron ``job:``: ``{{ certbot_script }} renew <options>`` (D5-normalized)."""
    return " ".join(f"{script} renew {options}".split())


def cron_privilege(hv: CertbotVars, host: Host) -> bool:
    """Map ``certbot_auto_renew_user`` onto the request's privilege (D6).

    ``crontab.crontab`` edits the *invoking* user's table -- which table
    follows the request's privilege, never a ``user=`` argument. The role's
    default (the connecting user, since the ``cron`` module's user then
    matches the login) is the unprivileged request; ``root`` is
    ``sudo=True``; a third user cannot be targeted and raises.
    """
    user = hv.auto_renew_user
    if user == "root":
        return True
    connection = host.connection or {}
    login = connection.get("username") or connection.get("user")
    if user == "" or user == login:
        return False
    raise ValueError(
        f"certbot_auto_renew_user {user!r} names a third user's crontab, which "
        "this migration cannot target: crontab.crontab follows the request's "
        "privilege, never a user= argument. Use '' (the connecting user) or "
        "'root'"
    )


# --- the cert-creation deploy (one per cert, as the role's include) -----------


def create_cert(script: str, hv: CertbotVars, cert: Cert) -> Deploy:
    """``create-cert-standalone.yml`` / ``create-cert-webroot.yml``, per item.

    The gate order is the role's: ``stat`` first, then the standalone
    hook directories/files (or the webroot directory), then the domain probe
    only when the cert exists (``changed_when: false`` -- a read, so an
    ``act`` barrier whose failure the body raises on), then the create action
    when the cert is absent or its domains changed. The requests between the
    barriers land in the wave the next barrier closes, so the hooks and the
    webroot exist before anything reads or issues a certificate.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        name = cert_name(cert)
        exists = bool(
            (yield fact(FileExists, path=f"{LIVE_DIR}/{name}/cert.pem"))
        )

        if hv.create_method == "standalone" and hv.create_standalone_stop_services:
            # "Ensure pre and post hook folders exist." -- the loop's two items.
            yield op(
                files.directory,
                name="Ensure pre hook folder exists.",
                path=HOOK_PRE_DIR,
                mode="0755",
                user="root",
                group="root",
                sudo=True,
            )
            yield op(
                files.directory,
                name="Ensure post hook folder exists.",
                path=HOOK_POST_DIR,
                mode="0755",
                user="root",
                group="root",
                sudo=True,
            )
            # The two templates, f-string rendered control-side at 0750 (D4);
            # files.install is content-idempotent, so a converged hook is a
            # no-op request.
            yield op(
                files.install,
                name="Create pre hook to stop services.",
                content=render_service_hook("stop", hv.create_standalone_stop_services),
                dest=PRE_HOOK_PATH,
                mode="0750",
                user="root",
                group="root",
                sudo=True,
            )
            yield op(
                files.install,
                name="Create post hook to start services.",
                content=render_service_hook(
                    "start", hv.create_standalone_stop_services
                ),
                dest=POST_HOOK_PATH,
                mode="0750",
                user="root",
                group="root",
                sudo=True,
            )
        if hv.create_method == "webroot":
            # The role's webroot task sets no mode/owner, so none is passed --
            # files.directory leaves both alone (mode/group default None).
            yield op(
                files.directory,
                name="Create webroot directory if it doesn't exist yet.",
                path=cert.webroot or hv.webroot,
                sudo=True,
            )

        domains_changed = False
        if exists:
            command = domains_command(script, name)
            result = yield act(
                Shell,
                command=command,
                name="Check if domains have changed.",
                sudo=True,
            )
            if result["exit_status"] != 0:
                # changed_when: false makes it a read, but a failed probe still
                # fails the play upstream -- and a half-parsed stdout must
                # never drive a renewal decision.
                raise CommandError(
                    host.name, command, result["exit_status"], result["stderr"]
                )
            domains_changed = parse_domains(result["stdout"]) != desired_domains(cert)

        if not exists or domains_changed:
            command = create_command(script, hv, cert, name)
            result = yield act(
                Shell,
                command=command,
                name=f"Generate new certificate for {name}.",
                sudo=True,
            )
            if result["exit_status"] != 0:
                raise CommandError(
                    host.name, command, result["exit_status"], result["stderr"]
                )

    return deploy


# --- the factory --------------------------------------------------------------


def certbot(v: VarsSet[CertbotVars]) -> Deploy:
    """Install Certbot, issue configured certs, and schedule the renewal cron.

    Covers both families the upstream role branches on: Ubuntu hosts get the
    release-dispatched apt package, RedHat hosts first run ``setup-RedHat.yml``
    (the ``dnf-plugins-core`` install and, on Rocky/AlmaLinux, the
    ``crb`` enable) and then the dnf package install; everything after the
    install -- certs, hooks, renewal cron -- is family-agnostic.

    Args:
        v: The role's resolved variables, one :class:`CertbotVars` per host
            (``resolve_vars(CertbotVars, "certbot", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs -- no
        other role consumes them -- so it returns ``None`` on every path,
        including the family guard's early return; a composing role sequences
        it with ``yield from scoped("certbot", certbot(v))(host)``.

    Raises:
        ValueError: A role variable is invalid (raised by pydantic while the
            variables are resolved, before any host is contacted), the gathered
            distribution does not match the host's declared Ubuntu/RedHat
            family or carries no readable Ubuntu release (right after the
            ``LinuxDistribution`` barrier, so nothing is applied yet), or
            ``auto_renew_user`` names a third user's crontab.
        CommandError: The ``dnf config-manager`` enable step, a domain probe
            or a certificate-creation action exited non-zero. Raised in the
            body from the barrier's own ``Result`` -- an ``act`` barrier never
            raises on a failing exit status, so the check belongs to the role.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family not in ("Ubuntu", "RedHat"):
            return

        # Wave 0: the include_vars dispatch source (D2). This is also the only
        # proof the declared family matches the machine; both raise paths run
        # before any request is built, so nothing is half-applied.
        info = yield fact(LinuxDistribution)
        if host.family == "Ubuntu":
            script = resolve_package(hv, info)
            installer = apt.packages
        else:
            if info.get("family") != "RedHat":
                raise ValueError(
                    f"{host.name}: declared RedHat family but the distribution "
                    f"is {info.get('name')!r} (family {info.get('family')!r}) "
                    "-- check the inventory family"
                )
            script = hv.package or DEFAULT_PACKAGE
            # setup-RedHat.yml (D8), ahead of the install like upstream: the
            # dnf-plugins-core request lands in this wave and the config-manager
            # act barrier closes it, so the binary exists when the shell runs.
            yield op(
                dnf.packages,
                name="Ensure dnf-plugins are installed on Rocky/AlmaLinux.",
                packages=(DNF_PLUGINS_PACKAGE,),
                sudo=True,
            )
            if info.get("name") != "Fedora":
                # changed_when: false -- a re-runnable read, so an act barrier
                # on every pass, but the shell task still fails the play.
                result = yield act(
                    Shell,
                    command=CRB_ENABLE_COMMAND,
                    name="Enable the CRB repository.",
                    sudo=True,
                )
                if result["exit_status"] != 0:
                    raise CommandError(
                        host.name,
                        CRB_ENABLE_COMMAND,
                        result["exit_status"],
                        result["stderr"],
                    )
            installer = dnf.packages

        # The install request and the per-cert creates accumulate until the
        # next barrier closes their wave; the package name is the script name
        # (the role's set_fact), and the role's generic ``package`` task
        # becomes the family's own manager.
        yield op(
            installer,
            name="Install Certbot.",
            packages=(script,),
            sudo=True,
        )

        if hv.create_if_missing:
            for cert in hv.certs:
                yield from create_cert(script, hv, cert)(host)

        if hv.auto_renew:
            # renew-cron.yml: minute/hour/name verbatim; user= mapped onto the
            # request's privilege (D6). The entry lands in root's or the
            # connecting user's table exactly as upstream's cron task wrote it.
            yield op(
                crontab.crontab,
                name="Add cron job for certbot renewal.",
                command=renew_command(script, hv.auto_renew_options),
                cron_name=CRON_NAME,
                minute=hv.auto_renew_minute,
                hour=hv.auto_renew_hour,
                sudo=cron_privilege(hv, host),
            )

    return deploy


__all__ = [
    "Cert",
    "CertbotVars",
    "certbot",
]
