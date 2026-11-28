# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The GitLab role (migrated from ``geerlingguy.gitlab``).
#
# Installs and configures the GitLab CE omnibus package on Ubuntu and RedHat
# hosts: the dependency packages, the vendor package repository (fetched with
# the upstream ``script.deb.sh``/``script.rpm.sh`` installer and run
# elevated), the ``gitlab-ce`` package
# (optionally version-pinned), the first-run ``gitlab-ctl reconfigure`` that
# bootstraps the instance, the optional self-signed SSL certificate under
# ``/etc/gitlab/ssl``, and the rendered ``/etc/gitlab/gitlab.rb`` (root:root,
# mode ``0600``) whose change queues the role's one ``restart gitlab`` handler.
# :func:`gitlab` is the whole task list and the handler, :class:`GitlabVars` is
# ``defaults/main.yml``, bound from the inventory entry's ``gitlab`` role
# mapping by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` and ``"RedHat"`` OS families -- exactly
# the two the Ansible role supports (its README installs "on any RedHat/CentOS
# or Debian/Ubuntu linux system", ``vars/RedHat.yml`` carries the RPM-side
# constants, and the CI matrix runs ubuntu2004, debian10 and rockylinux8).
# The ``include_vars`` OS dispatch collapses to the frozen per-family constants
# below because the vars split only by OS family -- not by release -- and the
# inventory states the family up front: no ``LinuxDistribution`` barrier
# exists. Alpine (and every other family) has no upstream branch at all -- no
# ``vars/Alpine.yml`` for the dispatch to load -- so it stays excluded, guarded
# out before the first yield.
#
# The stream is fact-gated, so its wave count is host-dependent:
#
# 0. the dependency install (``apt.packages`` with the gated cache refresh on
#    Ubuntu, plain ``dnf.packages`` on RedHat, D4) -- closed by the
#    ``/usr/bin/gitlab-ctl`` probe that decides whether the repo has to be set
#    up at all (task 3's ``stat``).
# 1. when GitLab is absent: the vendor-script download and its
#    ``act(Shell, "bash …")`` run (tasks 6-7); the version-pinned (or bare)
#    ``gitlab-ce`` install then joins the wave closed by the
#    ``/var/opt/gitlab/bootstrapped`` probe (tasks 8-10). When GitLab is
#    present, that probe closes this wave on its own.
# 2. when not bootstrapped: ``gitlab-ctl reconfigure`` as an ``act`` whose
#    result is deliberately unchecked -- Ansible's ``failed_when: false``.
# 3. when a self-signed cert is wanted: the ``0700`` ``/etc/gitlab/ssl``
#    directory, closed by a ``fact(FileExists)`` read of the certificate --
#    the fact re-probes elevated behind the mode-``0700`` parent (issue #70),
#    so the ``creates=`` gate needs no hand-rolled elevated probe (D9).
# 4. when the certificate is missing: the ``openssl req`` act (task 12).
# 5. :func:`configure` -- the rendered ``gitlab.rb`` install and its metadata
#    companion (both notify) -- closed by ``checkpoint("configure done")``.
# 6. :func:`handler` -- ``restart gitlab``, only on the hosts that checkpoint
#    selected. Like nfs it is a **command** handler
#    (``command: gitlab-ctl reconfigure``), so the handler wave carries a
#    barrier line and no request line.
#
# Prerequisites: ``reemote[templates]`` -- ``templates/gitlab.rb.j2`` renders
# control-side through :func:`reemote.roles._render.jinja2_environment`, the
# role's one Jinja2 template. The host needs passwordless sudo (every request
# and action is elevated; reemote has no play-level ``become``) and enough
# disk/RAM for the omnibus package: the install is multi-GB and the first
# reconfigure is minutes-long.
#
# Deviations from the Ansible role, all deliberate:
#
# * the host-family guard is the body's opening ``if``, not a ``when=`` on
#   every request (D1): an off-target host emits no requests, no barriers and
#   no log lines at all.
# * the ``include_vars`` OS dispatch collapses to frozen per-family constants
#   (D2): the ``script.deb.sh``/``script.rpm.sh`` URLs and the Debian-only
#   ``gnupg2`` half. Upstream's per-family version separator (``=`` on Debian,
#   ``-`` on RedHat) is reemote's single ``<pkg>=<version>`` pin spelling on
#   both families: apt passes it through, ``dnf.packages`` renders the ``-``
#   NEVRA form itself.
# * task 2's ``stat`` of ``/etc/gitlab/gitlab.rb`` is dropped (D3): it is
#   registered as ``gitlab_config_file`` and *never referenced* by any later
#   task or handler, so the read is dead code in the role itself.
# * the two dependency tasks (``package: gitlab_dependencies`` and the Debian
#   ``apt: gnupg2``) become one per-family request (``apt.packages`` on
#   Ubuntu, ``dnf.packages`` on RedHat); on the apt half a gated cache
#   refresh is folded in (D4) -- the nfs/docker precedent that keeps the role
#   self-contained on a fresh host, where the distro index may be stale. The
#   role refreshes no metadata on RedHat, so neither does the dnf half.
# * ``async: 300``/``poll: 5`` on the package install is dropped (D5): a
#   reemote request runs to completion and reports when it is done, and the
#   300-second ceiling is a wall Ansible builds routinely exceed here.
# * the template task becomes two notifying requests (D6): ``files.install``'s
#   idempotence is content-only, so a root:root ``0600`` drift on unchanged
#   content would never converge, while Ansible's template module reports a
#   change on owner/group/mode drift too (the apache/nfs companion precedent).
# * task 10's ``failed_when: false`` is preserved as an unchecked ``act``
#   result (D7): the first reconfigure legitimately fails on some installs,
#   and the handler's later run is the fix -- a barrier result is data, so
#   nothing raises.
# * the handler's ``failed_when: gitlab_restart_handler_failed_when | bool``
#   moves from a string that coerces to ``False`` to a real bool field (D8):
#   the default never fails; ``True`` makes the body raise its own
#   :class:`~reemote.hosts.CommandError` on a non-zero exit.
# * task 12's ``creates=`` gate is a typed ``fact(FileExists)`` read (D9): the
#   certificate lives behind a mode-``0700`` directory the login user cannot
#   traverse, and ``FileExists`` re-probes exactly that case elevated.
# * ``gitlab_download_validate_certs`` is carried and inert (D10): the script
#   is fetched control-side by ``files.download``, where TLS verification is
#   the controller's ``urllib`` policy, not a per-request knob.
# * ``gitlab_config_template`` keeps its dual meaning (D11): the bundled name
#   renders the verbatim template inlined below; any other value is a
#   *controller-side* path, read here rather than on the host (the apache
#   ``vhosts_template`` precedent).
# * the two ``gitlab_nginx_listen_*`` template gates (``is defined``) and
#   ``gitlab_extra_settings`` become ``None``/empty sentinels omitted from the
#   render context (D12): the template's ``is defined`` checks then behave
#   exactly as they do for a host where the variable was never set.
# """

import json
import shlex
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..actions import Shell
from ..core import op
from ..handlers import changed_hosts
from ..hosts import CommandError, Host
from ..modules import apt, dnf, files
from ..modules.files import FileExists
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, act, checkpoint, fact

# --- role constants (defaults/main.yml + vars/Debian.yml + vars/RedHat.yml) ---

#: ``tasks/main.yml``'s install probe (task 3), frozen: the omnibus control
#: binary whose presence decides whether the repo and package tasks run.
GITLAB_CTL_PATH = "/usr/bin/gitlab-ctl"

#: Task 10's ``creates=`` path, frozen: the bootstrap marker the first
#: successful ``gitlab-ctl reconfigure`` leaves behind.
BOOTSTRAPPED_PATH = "/var/opt/gitlab/bootstrapped"

#: Task 13's template destination, frozen.
CONFIG_PATH = "/etc/gitlab/gitlab.rb"

#: Task 11's SSL directory, frozen.
SSL_DIR = "/etc/gitlab/ssl"

#: Task 6's ``get_url`` destination, frozen: the spooled vendor script.
REPO_SCRIPT_SPOOL = "/tmp/gitlab_install_repository.sh"

#: ``vars/Debian.yml``/``vars/RedHat.yml``'s
#: ``gitlab_repository_installation_script_url``, with the edition
#: interpolated: the vendor publishes one installer script per package
#: format under the same repositories path.
REPO_SCRIPT_URLS: dict[str, str] = {
    "Ubuntu": (
        "https://packages.gitlab.com/install/repositories/gitlab/"
        "{edition}/script.deb.sh"
    ),
    "RedHat": (
        "https://packages.gitlab.com/install/repositories/gitlab/"
        "{edition}/script.rpm.sh"
    ),
}

#: The version-pin separator inside :func:`package_pin`. Upstream spells the
#: pin per family (``vars/Debian.yml`` ``=``, ``vars/RedHat.yml`` ``-``), but
#: reemote's package operations share ONE user-facing grammar
#: ``<pkg>=<version>``: ``apt.packages`` passes it through and
#: ``dnf.packages`` rewrites it to the ``-`` NEVRA form when rendering.
VERSION_SEPARATOR = "="

#: The OS families this role targets -- the two the Ansible role supports.
#: Alpine and every other family returns before the body's first ``yield``.
TARGET_FAMILIES: tuple[str, ...] = ("Ubuntu", "RedHat")

#: Task 10 and the ``restart gitlab`` handler's command, run elevated.
RECONFIGURE_COMMAND = "gitlab-ctl reconfigure"

#: The name ``templates/gitlab.rb.j2`` is identified by; anything else in
#: :attr:`GitlabVars.config_template` is a controller-side path (D11).
BUNDLED_TEMPLATE = "gitlab.rb.j2"

#: The Debian-family ``apt: gnupg2`` task (upstream's Debian-only ``when``),
#: merged into the Ubuntu dependency install (D4): ``script.deb.sh`` needs
#: gpg to dearmor the vendor key. The RedHat list stays exactly
#: ``gitlab_dependencies``, as upstream's ``package`` task gets it.
GNUPG2_PACKAGE = "gnupg2"

#: The Ubuntu dependency install folds in a gated cache refresh so the role is
#: self-contained on a fresh host (D4, the nfs/docker precedent). Upstream
#: refreshes nothing on RedHat, so the dnf half carries no equivalent.
APT_CACHE_VALID_TIME = 600

#: The request names that queue ``restart gitlab`` -- task 13's ``notify``.
#: Both the content install and its metadata companion carry the notification
#: (D6): Ansible reports the template task changed on a content *or*
#: owner/group/mode drift.
NOTIFYING: frozenset[str] = frozenset(
    {"Copy GitLab configuration file", "Set GitLab configuration file mode and owner"}
)

#: ``templates/gitlab.rb.j2``, verbatim (byte-identical to the role's file:
#: 4983 bytes, one trailing newline). Rendered control-side by
#: :func:`render_gitlab_rb` under Ansible's whitespace settings; only two
#: non-builtin filters appear in it -- ``lower`` (Jinja2's builtin already
#: stringifies booleans, so ``true | lower`` renders ``true``) and
#: ``to_json``, registered on the environment by the render function.
GITLAB_RB_TEMPLATE = """# The URL through which GitLab will be accessed.
external_url "{{ gitlab_external_url }}"

# gitlab.yml configuration
gitlab_rails['time_zone'] = "{{ gitlab_time_zone }}"
gitlab_rails['backup_keep_time'] = {{ gitlab_backup_keep_time }}
gitlab_rails['gitlab_email_enabled'] = {{ gitlab_email_enabled | lower }}
{% if gitlab_email_enabled %}
gitlab_rails['gitlab_email_from'] = "{{ gitlab_email_from }}"
gitlab_rails['gitlab_email_display_name'] = "{{ gitlab_email_display_name }}"
gitlab_rails['gitlab_email_reply_to'] = "{{ gitlab_email_reply_to }}"
{% endif %}

# Default Theme
gitlab_rails['gitlab_default_theme'] = "{{ gitlab_default_theme }}"

# Whether to redirect http to https.
nginx['redirect_http_to_https'] = {{ gitlab_redirect_http_to_https | lower }}
nginx['ssl_certificate'] = "{{ gitlab_ssl_certificate }}"
nginx['ssl_certificate_key'] = "{{ gitlab_ssl_certificate_key }}"

letsencrypt['enable'] = {{ gitlab_letsencrypt_enable | lower }}
{% if gitlab_letsencrypt_enable %}
letsencrypt['contact_emails'] = {{ gitlab_letsencrypt_contact_emails | to_json }}
letsencrypt['auto_renew_hour'] = "{{ gitlab_letsencrypt_auto_renew_hour }}"
letsencrypt['auto_renew_minute'] = "{{ gitlab_letsencrypt_auto_renew_minute }}"
letsencrypt['auto_renew_day_of_month'] = "{{ gitlab_letsencrypt_auto_renew_day_of_month }}"
letsencrypt['auto_renew'] = {{ gitlab_letsencrypt_auto_renew | lower }}
{% endif %}

# The directory where Git repositories will be stored.
git_data_dirs({"default" => {"path" => "{{ gitlab_git_data_dir }}"} })

# The directory where Gitlab backups will be stored
gitlab_rails['backup_path'] = "{{ gitlab_backup_path }}"

# These settings are documented in more detail at
# https://gitlab.com/gitlab-org/gitlab-ce/blob/master/config/gitlab.yml.example#L118
gitlab_rails['ldap_enabled'] = {{ gitlab_ldap_enabled | lower }}
{% if gitlab_ldap_enabled %}
gitlab_rails['ldap_host'] = '{{ gitlab_ldap_host }}'
gitlab_rails['ldap_port'] = {{ gitlab_ldap_port }}
gitlab_rails['ldap_uid'] = '{{ gitlab_ldap_uid }}'
gitlab_rails['ldap_method'] = '{{ gitlab_ldap_method}}' # 'ssl' or 'plain'
gitlab_rails['ldap_bind_dn'] = '{{ gitlab_ldap_bind_dn }}'
gitlab_rails['ldap_password'] = '{{ gitlab_ldap_password }}'
gitlab_rails['ldap_allow_username_or_email_login'] = true
gitlab_rails['ldap_base'] = '{{ gitlab_ldap_base }}'
{% endif %}

# GitLab Nginx
## See https://gitlab.com/gitlab-org/omnibus-gitlab/blob/master/doc/settings/nginx.md
{% if gitlab_nginx_listen_port is defined %}
nginx['listen_port'] = "{{ gitlab_nginx_listen_port }}"
{% endif %}
{% if gitlab_nginx_listen_https is defined %}
nginx['listen_https'] = {{ gitlab_nginx_listen_https | lower }}
{% endif %}

# Use smtp instead of sendmail/postfix
# More details and example configuration at
# https://gitlab.com/gitlab-org/omnibus-gitlab/blob/master/doc/settings/smtp.md
gitlab_rails['smtp_enable'] = {{ gitlab_smtp_enable | lower }}
{% if gitlab_smtp_enable %}
gitlab_rails['smtp_address'] = '{{ gitlab_smtp_address }}'
gitlab_rails['smtp_port'] = {{ gitlab_smtp_port }}
{% if gitlab_smtp_user_name %}
gitlab_rails['smtp_user_name'] = '{{ gitlab_smtp_user_name }}'
{% endif %}
{% if gitlab_smtp_password %}
gitlab_rails['smtp_password'] = '{{ gitlab_smtp_password }}'
{% endif %}
gitlab_rails['smtp_domain'] = '{{ gitlab_smtp_domain }}'
{% if gitlab_smtp_authentication %}
gitlab_rails['smtp_authentication'] = '{{ gitlab_smtp_authentication }}'
{% endif %}
gitlab_rails['smtp_enable_starttls_auto'] = {{ gitlab_smtp_enable_starttls_auto | lower }}
gitlab_rails['smtp_tls'] = {{ gitlab_smtp_tls | lower }}
gitlab_rails['smtp_openssl_verify_mode'] = '{{ gitlab_smtp_openssl_verify_mode }}'
gitlab_rails['smtp_ca_path'] = '{{ gitlab_smtp_ca_path }}'
gitlab_rails['smtp_ca_file'] = '{{ gitlab_smtp_ca_file }}'
{% endif %}

# 2-way SSL Client Authentication.
{% if gitlab_nginx_ssl_verify_client %}
nginx['ssl_verify_client'] = "{{ gitlab_nginx_ssl_verify_client }}"
{% endif %}
{% if gitlab_nginx_ssl_client_certificate %}
nginx['ssl_client_certificate'] = "{{ gitlab_nginx_ssl_client_certificate }}"
{% endif %}

# GitLab registry.
registry['enable'] = {{ gitlab_registry_enable | lower }}
{% if gitlab_registry_enable %}
registry_external_url "{{ gitlab_registry_external_url }}"
registry_nginx['ssl_certificate'] = "{{ gitlab_registry_nginx_ssl_certificate }}"
registry_nginx['ssl_certificate_key'] = "{{ gitlab_registry_nginx_ssl_certificate_key }}"
{% endif %}

{% if gitlab_extra_settings is defined %}
# Extra configuration
{% for extra in gitlab_extra_settings %}
{% for setting in extra %}
{% for kv in extra[setting] %}
{% if (kv.type is defined and kv.type == 'plain') or (kv.value is not string) %}
{{ setting }}['{{ kv.key }}'] = {{ kv.value }}
{% else %}
{{ setting }}['{{ kv.key }}'] = '{{ kv.value }}'
{% endif %}
{% endfor %}
{% endfor %}

{% endfor %}
{% endif %}

# To change other settings, see:
# https://gitlab.com/gitlab-org/omnibus-gitlab/blob/master/README.md#changing-gitlab-yml-settings
"""

# --- variables ---------------------------------------------------------------


class GitlabExtraOption(BaseModel):
    """One ``gitlab_extra_settings`` key/value line: the loop's ``kv`` item.

    Field names and semantics are the upstream keys verbatim (``key``,
    ``value``, ``type``). A ``type`` of ``"plain"`` -- or any non-string
    ``value`` -- renders the value raw (no quotes) in ``gitlab.rb``, exactly
    as the template's triple loop does; an omitted ``type`` stays *undefined*
    for the template, like upstream kv dicts that never set it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``kv.key``: the setting name inside ``section['...']``.
    key: str
    #: ``kv.value``: rendered quoted, unless ``type`` is ``"plain"`` or this
    #: is not a string (a list renders as a Python literal, as Jinja2/Ansible
    #: would print it).
    value: Any = None
    #: ``kv.type``: ``"plain"`` emits the value unquoted. ``None`` leaves the
    #: key undefined for the template's ``is defined`` check.
    type: str | None = None


@dataclass(frozen=True)
class GitlabPlan:
    """One host's resolved targets, built by the body before its first wave.

    The four ``defaults/main.yml`` values derived from ``gitlab_domain`` (the
    external URL, both certificate paths and the subject) are empty-string
    defaults in :class:`GitlabVars` and resolved here, so an explicit
    override -- of one path but not the other -- behaves as upstream does
    (each variable is independent in Ansible too). The rendered config rides
    the plan as a plain ``str``: hashable, identical across waves.

    :attr:`family` carries the ``include_vars`` dispatch: it selects the
    vendor script URL and the Debian-only ``gnupg2`` append baked into the
    fields below, and the body reads it again to pick the package operation
    (``apt.packages`` vs ``dnf.packages``).
    """

    family: str
    dependencies: tuple[str, ...]
    package_pin: tuple[str, ...]
    script_url: str
    external_url: str
    ssl_certificate: str
    ssl_certificate_key: str
    cert_subj: str
    openssl_command: str
    config_content: str


class GitlabVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``gitlab`` role mapping, so ``domain`` there sets
    :attr:`domain`. The model is frozen; every sequence is a ``tuple`` so any
    value that reaches :func:`~reemote.core.op` hashes (design doc section
    8.1). The four derived-by-default variables (:attr:`external_url`,
    :attr:`ssl_certificate`, :attr:`ssl_certificate_key`,
    :attr:`self_signed_cert_subj`) default to ``""`` and resolve against
    :attr:`domain` in :func:`gitlab_plan`, which is where upstream's
    ``{{ gitlab_domain }}`` interpolation lives in this migration.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # General config.
    #: ``gitlab_domain``: the host name the derived defaults interpolate.
    domain: str = "gitlab"
    #: ``gitlab_external_url``: empty means ``https://{domain}/``, upstream's
    #: interpolated default.
    external_url: str = ""
    #: ``gitlab_git_data_dir``: the ``git_data_dirs`` path.
    git_data_dir: str = "/var/opt/gitlab/git-data"
    #: ``gitlab_edition``: the omnibus package name; also selects the vendor
    #: repo URL (both installer scripts sit under this path, per edition).
    edition: str = "gitlab-ce"
    #: ``gitlab_version``: empty installs the candidate; non-empty pins
    #: ``gitlab-ce=<version>`` (the D2 pin spelling on both families). The
    #: bundled template is
    #: upstream's verbatim and upstream predates GitLab 18, so an unpinned
    #: install of a 18.0-or-newer candidate makes ``gitlab-ctl reconfigure``
    #: refuse the rendered ``git_data_dirs`` line (deprecated in 17.8, removed
    #: in 18.0) -- a failure the handler records and, per D8, does not raise.
    #: Pin a 17.x release for the configuration to be applied.
    version: str = ""
    #: ``gitlab_backup_path``: the ``gitlab_rails['backup_path']`` directory.
    backup_path: str = "/var/opt/gitlab/backups"
    #: ``gitlab_config_template``: the bundled ``gitlab.rb.j2`` name renders
    #: the inlined template; anything else is a controller-side path (D11).
    config_template: str = BUNDLED_TEMPLATE

    # SSL configuration.
    #: ``gitlab_redirect_http_to_https``.
    redirect_http_to_https: bool = True
    #: ``gitlab_ssl_certificate``: empty means ``/etc/gitlab/ssl/{domain}.crt``.
    ssl_certificate: str = ""
    #: ``gitlab_ssl_certificate_key``: empty means ``/etc/gitlab/ssl/{domain}.key``.
    ssl_certificate_key: str = ""

    # SSL self-signed certificate configuration.
    #: ``gitlab_create_self_signed_cert``: the task 11/12 gate.
    create_self_signed_cert: bool = True
    #: ``gitlab_self_signed_cert_subj``: empty means the upstream
    #: ``/C=US/ST=Missouri/L=Saint Louis/O=IT/CN={domain}`` default.
    self_signed_cert_subj: str = ""

    # LDAP configuration.
    #: ``gitlab_ldap_enabled``: the template's LDAP block gate.
    ldap_enabled: bool = False
    #: ``gitlab_ldap_host``.
    ldap_host: str = "example.com"
    #: ``gitlab_ldap_port``: rendered unquoted, as the template does.
    ldap_port: str = "389"
    #: ``gitlab_ldap_uid``.
    ldap_uid: str = "sAMAccountName"
    #: ``gitlab_ldap_method``: ``ssl`` or ``plain``.
    ldap_method: str = "plain"
    #: ``gitlab_ldap_bind_dn``.
    ldap_bind_dn: str = "CN=Username,CN=Users,DC=example,DC=com"
    #: ``gitlab_ldap_password``.
    ldap_password: str = "password"
    #: ``gitlab_ldap_base``.
    ldap_base: str = "DC=example,DC=com"

    # SMTP configuration.
    #: ``gitlab_smtp_enable``: the template's SMTP block gate.
    smtp_enable: bool = False
    #: ``gitlab_smtp_address``.
    smtp_address: str = "smtp.server"
    #: ``gitlab_smtp_port``: rendered unquoted, as the template does.
    smtp_port: str = "465"
    #: ``gitlab_smtp_user_name``: falsy omits the line (the template's gate).
    smtp_user_name: str = "smtp user"
    #: ``gitlab_smtp_password``: falsy omits the line.
    smtp_password: str = "smtp password"
    #: ``gitlab_smtp_domain``.
    smtp_domain: str = "example.com"
    #: ``gitlab_smtp_authentication``: falsy omits the line.
    smtp_authentication: str = "login"
    #: ``gitlab_smtp_enable_starttls_auto``.
    smtp_enable_starttls_auto: bool = True
    #: ``gitlab_smtp_tls``.
    smtp_tls: bool = False
    #: ``gitlab_smtp_openssl_verify_mode``.
    smtp_openssl_verify_mode: str = "none"
    #: ``gitlab_smtp_ca_path``.
    smtp_ca_path: str = "/etc/ssl/certs"
    #: ``gitlab_smtp_ca_file``.
    smtp_ca_file: str = "/etc/ssl/certs/ca-certificates.crt"

    # 2-way SSL client authentication support.
    #: ``gitlab_nginx_ssl_verify_client``: falsy omits the line.
    nginx_ssl_verify_client: str = ""
    #: ``gitlab_nginx_ssl_client_certificate``: falsy omits the line.
    nginx_ssl_client_certificate: str = ""

    # Handler behaviour (D8).
    #: ``gitlab_restart_handler_failed_when``: upstream is the string
    #: ``'gitlab_restart.rc != 0'``, which ``| bool`` coerces to ``False`` --
    #: so the handler *never fails by default*. The bool ``True`` makes the
    #: handler raise :class:`~reemote.hosts.CommandError` on a non-zero exit.
    restart_handler_failed_when: bool = False

    # Dependencies.
    #: ``gitlab_dependencies``; ``gnupg2`` is appended by the merged Debian
    #: request (D4), not carried here.
    dependencies: tuple[str, ...] = (
        "openssh-server",
        "postfix",
        "curl",
        "openssl",
        "tzdata",
    )

    # Optional settings.
    #: ``gitlab_time_zone``.
    time_zone: str = "UTC"
    #: ``gitlab_backup_keep_time``: rendered unquoted.
    backup_keep_time: str = "604800"
    #: ``gitlab_download_validate_certs``: **inert** (D10) -- the vendor
    #: script is fetched control-side, where TLS verification is the
    #: controller's policy, not a per-request knob.
    download_validate_certs: bool = True
    #: ``gitlab_default_theme``.
    default_theme: str = "2"

    # Email configuration.
    #: ``gitlab_email_enabled``: the template's email block gate.
    email_enabled: bool = False
    #: ``gitlab_email_from``.
    email_from: str = "gitlab@example.com"
    #: ``gitlab_email_display_name``.
    email_display_name: str = "Gitlab"
    #: ``gitlab_email_reply_to``.
    email_reply_to: str = "gitlab@example.com"

    # Registry configuration.
    #: ``gitlab_registry_enable``: the template's registry block gate.
    registry_enable: bool = False
    #: ``gitlab_registry_external_url``.
    registry_external_url: str = "https://gitlab.example.com:4567"
    #: ``gitlab_registry_nginx_ssl_certificate``.
    registry_nginx_ssl_certificate: str = "/etc/gitlab/ssl/gitlab.crt"
    #: ``gitlab_registry_nginx_ssl_certificate_key``.
    registry_nginx_ssl_certificate_key: str = "/etc/gitlab/ssl/gitlab.key"

    # LetsEncrypt configuration.
    #: ``gitlab_letsencrypt_enable``: the template's letsencrypt block gate.
    letsencrypt_enable: bool = False
    #: ``gitlab_letsencrypt_contact_emails``: rendered by ``to_json``.
    letsencrypt_contact_emails: tuple[str, ...] = ("gitlab@example.com",)
    #: ``gitlab_letsencrypt_auto_renew_hour``.
    letsencrypt_auto_renew_hour: int = 1
    #: ``gitlab_letsencrypt_auto_renew_minute``.
    letsencrypt_auto_renew_minute: int = 30
    #: ``gitlab_letsencrypt_auto_renew_day_of_month``.
    letsencrypt_auto_renew_day_of_month: str = "*/7"
    #: ``gitlab_letsencrypt_auto_renew``.
    letsencrypt_auto_renew: bool = True

    # Template-gated extras (upstream sets none in ``defaults/main.yml``).
    #: ``gitlab_nginx_listen_port``: ``None`` leaves the template's
    #: ``is defined`` check false, as on an unset variable (D12).
    nginx_listen_port: int | None = None
    #: ``gitlab_nginx_listen_https``: ``None`` omits the line (D12).
    nginx_listen_https: bool | None = None
    #: ``gitlab_extra_settings``: one item per upstream dict -- a mapping of
    #: the setting name (``gitlab_rails``, ``gitlab_shell``, …) to its option
    #: list; empty omits the whole block (D12).
    extra_settings: tuple[dict[str, tuple[GitlabExtraOption, ...]], ...] = ()

    @field_validator("dependencies", "letsencrypt_contact_emails", mode="before")
    @classmethod
    def _normalize_sequence(cls, value: Any) -> Any:
        """A list becomes a tuple; anything else falls through to the field."""
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("extra_settings", mode="before")
    @classmethod
    def _normalize_extra_settings(cls, value: Any) -> Any:
        """A (possibly nested) JSON list structure becomes tuples of dicts.

        Pydantic coerces the outer sequence and each section's option list on
        its own; this only turns the top-level list into a tuple so a
        non-empty mapping row stays the same shape on every pass.
        """
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("domain", "edition")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """Both feed derived paths and the repo URL, so neither may be empty.

        Upstream would interpolate an empty domain into
        ``/etc/gitlab/ssl/.crt``; reemote rejects it at ``resolve_vars``
        time, before any host is contacted.
        """
        if not value:
            raise ValueError("must be a non-empty string")
        return value


# --- pure helpers ------------------------------------------------------------


def package_pin(hv: GitlabVars) -> tuple[str, ...]:
    """Tasks 8-9: ``gitlab_package_name | default(gitlab_edition)``.

    The ``set_fact`` joins edition, the family's separator and the version.
    Both families spell the pin ``<pkg>=<version>`` here: ``apt.packages``
    pins exactly that way and ``dnf.packages`` renders the ``-`` NEVRA form
    upstream uses (D2).
    """
    if hv.version:
        return (f"{hv.edition}{VERSION_SEPARATOR}{hv.version}",)
    return (hv.edition,)


def openssl_command(subj: str, cert: str, key: str) -> str:
    """Task 12's ``command``, with the ``creates=`` paths interpolated.

    The upstream template string interpolates bare; these paths are built
    from ``gitlab_domain``, which may be any hostname-shaped string, so the
    three variable parts are shell-quoted here (the command is otherwise
    fixed).
    """
    return (
        "openssl req -new -nodes -x509 "
        f"-subj {shlex.quote(subj)} "
        "-days 3650 "
        f"-keyout {shlex.quote(key)} "
        f"-out {shlex.quote(cert)} "
        "-extensions v3_ca"
    )


def extra_settings_context(
    extra_settings: tuple[dict[str, tuple[GitlabExtraOption, ...]], ...],
) -> list[dict[str, list[dict[str, Any]]]]:
    """Reshape :attr:`GitlabVars.extra_settings` for the template's loops.

    ``{% for extra in gitlab_extra_settings %}{% for setting in extra %}
    {% for kv in extra[setting] %}`` walks *dicts*: each item maps a setting
    name to its list of kv dicts, and ``model_dump(exclude_none=True)`` drops
    an unset ``type`` so the template's ``kv.type is defined`` check reads it
    exactly as it reads an upstream kv dict without the key.
    """
    return [
        {
            section: [option.model_dump(exclude_none=True) for option in options]
            for section, options in extra.items()
        }
        for extra in extra_settings
    ]


def render_gitlab_rb(
    hv: GitlabVars, external_url: str, ssl_certificate: str, ssl_certificate_key: str
) -> str:
    """``templates/gitlab.rb.j2``, pre-rendered control-side.

    The context keeps the upstream ``gitlab_*`` variable names so the
    template rides verbatim, and passes real Python bools so the ``{% if %}``
    block gates and Jinja2's builtin ``lower`` (which stringifies: ``True``
    renders ``true``) both behave as Ansible's did. ``to_json`` is the one
    non-builtin filter the template uses, registered here. The two
    ``is defined`` knobs and the extra-settings block are *omitted* from the
    context when unset (D12), which under ``StrictUndefined`` makes the
    template's checks answer exactly as they do for an undefined variable.

    A custom ``config_template`` is a controller-side path, read here rather
    than on the host (D11). The jinja2 import is inside this function so that
    importing ``reemote.roles`` stays clean without the extra (§17.4).
    """
    from ._render import jinja2_environment

    template_text = (
        GITLAB_RB_TEMPLATE
        if hv.config_template == BUNDLED_TEMPLATE
        else Path(hv.config_template).read_text(encoding="utf-8")
    )
    env = jinja2_environment()
    env.filters["to_json"] = json.dumps
    context: dict[str, Any] = {
        "gitlab_external_url": external_url,
        "gitlab_time_zone": hv.time_zone,
        "gitlab_backup_keep_time": hv.backup_keep_time,
        "gitlab_email_enabled": hv.email_enabled,
        "gitlab_email_from": hv.email_from,
        "gitlab_email_display_name": hv.email_display_name,
        "gitlab_email_reply_to": hv.email_reply_to,
        "gitlab_default_theme": hv.default_theme,
        "gitlab_redirect_http_to_https": hv.redirect_http_to_https,
        "gitlab_ssl_certificate": ssl_certificate,
        "gitlab_ssl_certificate_key": ssl_certificate_key,
        "gitlab_letsencrypt_enable": hv.letsencrypt_enable,
        "gitlab_letsencrypt_contact_emails": list(hv.letsencrypt_contact_emails),
        "gitlab_letsencrypt_auto_renew_hour": hv.letsencrypt_auto_renew_hour,
        "gitlab_letsencrypt_auto_renew_minute": hv.letsencrypt_auto_renew_minute,
        "gitlab_letsencrypt_auto_renew_day_of_month": (
            hv.letsencrypt_auto_renew_day_of_month
        ),
        "gitlab_letsencrypt_auto_renew": hv.letsencrypt_auto_renew,
        "gitlab_git_data_dir": hv.git_data_dir,
        "gitlab_backup_path": hv.backup_path,
        "gitlab_ldap_enabled": hv.ldap_enabled,
        "gitlab_ldap_host": hv.ldap_host,
        "gitlab_ldap_port": hv.ldap_port,
        "gitlab_ldap_uid": hv.ldap_uid,
        "gitlab_ldap_method": hv.ldap_method,
        "gitlab_ldap_bind_dn": hv.ldap_bind_dn,
        "gitlab_ldap_password": hv.ldap_password,
        "gitlab_ldap_base": hv.ldap_base,
        "gitlab_smtp_enable": hv.smtp_enable,
        "gitlab_smtp_address": hv.smtp_address,
        "gitlab_smtp_port": hv.smtp_port,
        "gitlab_smtp_user_name": hv.smtp_user_name,
        "gitlab_smtp_password": hv.smtp_password,
        "gitlab_smtp_domain": hv.smtp_domain,
        "gitlab_smtp_authentication": hv.smtp_authentication,
        "gitlab_smtp_enable_starttls_auto": hv.smtp_enable_starttls_auto,
        "gitlab_smtp_tls": hv.smtp_tls,
        "gitlab_smtp_openssl_verify_mode": hv.smtp_openssl_verify_mode,
        "gitlab_smtp_ca_path": hv.smtp_ca_path,
        "gitlab_smtp_ca_file": hv.smtp_ca_file,
        "gitlab_nginx_ssl_verify_client": hv.nginx_ssl_verify_client,
        "gitlab_nginx_ssl_client_certificate": hv.nginx_ssl_client_certificate,
        "gitlab_registry_enable": hv.registry_enable,
        "gitlab_registry_external_url": hv.registry_external_url,
        "gitlab_registry_nginx_ssl_certificate": hv.registry_nginx_ssl_certificate,
        "gitlab_registry_nginx_ssl_certificate_key": (
            hv.registry_nginx_ssl_certificate_key
        ),
    }
    if hv.nginx_listen_port is not None:
        context["gitlab_nginx_listen_port"] = hv.nginx_listen_port
    if hv.nginx_listen_https is not None:
        context["gitlab_nginx_listen_https"] = hv.nginx_listen_https
    if hv.extra_settings:
        context["gitlab_extra_settings"] = extra_settings_context(hv.extra_settings)
    return env.from_string(template_text).render(**context)


def gitlab_plan(hv: GitlabVars, family: str) -> GitlabPlan:
    """Freeze every read the deploy needs: derived paths, the pin, the render.

    Control-side work inside the body, before the first wave is built: the
    four domain-interpolated defaults resolve exactly as ``defaults/main.yml``
    interpolates them, the two package requests' argument tuples are derived,
    and the template becomes a plain ``str``. Nothing here contacts a host --
    the role's vars split only by OS family, so the ``family`` argument (a
    key of :data:`TARGET_FAMILIES`) replaces the ``include_vars`` dispatch
    where a ``fact`` barrier would otherwise feed it.
    """
    external_url = hv.external_url or f"https://{hv.domain}/"
    ssl_certificate = hv.ssl_certificate or f"{SSL_DIR}/{hv.domain}.crt"
    ssl_certificate_key = hv.ssl_certificate_key or f"{SSL_DIR}/{hv.domain}.key"
    cert_subj = (
        hv.self_signed_cert_subj
        or f"/C=US/ST=Missouri/L=Saint Louis/O=IT/CN={hv.domain}"
    )
    # Upstream gates the gnupg2 task on the Debian family (D4); the RedHat
    # dependency list stays exactly ``gitlab_dependencies``.
    extra = (GNUPG2_PACKAGE,) if family == "Ubuntu" else ()
    return GitlabPlan(
        family=family,
        dependencies=tuple(dict.fromkeys(hv.dependencies + extra)),
        package_pin=package_pin(hv),
        script_url=REPO_SCRIPT_URLS[family].format(edition=hv.edition),
        external_url=external_url,
        ssl_certificate=ssl_certificate,
        ssl_certificate_key=ssl_certificate_key,
        cert_subj=cert_subj,
        openssl_command=openssl_command(cert_subj, ssl_certificate, ssl_certificate_key),
        config_content=render_gitlab_rb(
            hv, external_url, ssl_certificate, ssl_certificate_key
        ),
    )


# --- the deploy stages --------------------------------------------------------


def configure(plan: GitlabPlan) -> Deploy:
    """Task 13: the rendered ``gitlab.rb`` install and its metadata companion.

    Both requests carry a name in :data:`NOTIFYING` (D6): Ansible's template
    task reports changed on a content *or* owner/group/mode drift, and either
    queues ``restart gitlab``. ``files.install``'s idempotence is
    content-only -- and its read-back of the root-owned mode-``0600`` file is
    served by the elevated tier (issue #69's self-wrapped compound read), so
    an unchanged config yields no command on a rerun.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            files.install,
            name="Copy GitLab configuration file",
            content=plan.config_content,
            dest=CONFIG_PATH,
            mode="0600",
            user="root",
            group="root",
            sudo=True,
        )
        yield op(
            files.file,
            name="Set GitLab configuration file mode and owner",
            path=CONFIG_PATH,
            mode="0600",
            user="root",
            group="root",
            sudo=True,
        )

    return deploy


def handler(notified: frozenset[str], raise_on_failure: bool) -> Deploy:
    """``handlers/main.yml``: ``restart gitlab`` -- a command handler.

    ``handlers/main.yml`` is ``command: gitlab-ctl reconfigure``, not a
    service action, so there is no handler factory to call: the deploy yields
    the command as an ``act`` barrier and the handler wave holds a barrier
    line and no request line (the nfs precedent). An unnotified host yields
    nothing, so a converged run has no handler wave at all.

    A barrier never raises on a non-zero exit -- the ``Result`` is data -- so
    the body consults :attr:`GitlabVars.restart_handler_failed_when` (D8):
    upstream's ``failed_when`` string coerces to ``False``, so the default is
    "never fail", exactly the role's behaviour; ``True`` re-raises the typed
    :class:`~reemote.hosts.CommandError`.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if host.name not in notified:
            return
        result = yield act(
            Shell, command=RECONFIGURE_COMMAND, name="restart gitlab", sudo=True
        )
        if raise_on_failure and result["exit_status"] != 0:
            raise CommandError(
                host.name,
                RECONFIGURE_COMMAND,
                result["exit_status"],
                result["stderr"],
            )

    return deploy


# --- the role ----------------------------------------------------------------


def gitlab(v: VarsSet[GitlabVars]) -> Deploy:
    """Install and configure GitLab CE on Ubuntu and RedHat hosts, run its handler.

    Args:
        v: The role's resolved variables, one :class:`GitlabVars` per host
            (``resolve_vars(GitlabVars, "gitlab", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs -- no
        other role consumes them -- so it returns ``None`` on every path,
        including the family guard's early return.

    Raises:
        CommandError: The vendor repository script exited non-zero on a host
            that needed it, the ``openssl req`` certificate command failed,
            or -- with ``restart_handler_failed_when`` set -- the
            ``restart gitlab`` handler's reconfigure exited non-zero on a
            notified host. A barrier result is data, so the body turns it
            into the typed error itself; the driver propagates it and closes
            the hosts in its own ``finally``. Bad *variables* are rejected
            earlier, by :func:`~reemote.rolevars.resolve_vars`.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family not in TARGET_FAMILIES:
            return

        plan = gitlab_plan(hv, host.family)
        # The ``include_vars`` dispatch in miniature: upstream's generic
        # ``package`` module resolves to apt on Ubuntu and dnf on RedHat, and
        # only the apt half folds the gated cache refresh (D4).
        if plan.family == "RedHat":
            packages_op = dnf.packages
            deps_kwargs: dict[str, Any] = {}
        else:
            packages_op = apt.packages
            deps_kwargs = {"update": True, "cache_valid_time": APT_CACHE_VALID_TIME}

        # The dependencies install shares wave 0 with the install probe: the
        # probe gates only the repo tasks, never the package list (tasks 3-5;
        # tasks 4 and 5 merged, D4). apt.update's stamp fact gates the folded
        # refresh, so a converged host with a fresh cache emits no apt-get
        # command.
        yield op(
            packages_op,
            name="Install GitLab dependencies",
            packages=plan.dependencies,
            sudo=True,
            **deps_kwargs,
        )
        installed = yield fact(
            FileExists, GITLAB_CTL_PATH, name="Check if GitLab is already installed"
        )

        if not installed:
            # The vendor repository: fetch the script control-side, then run
            # it elevated (tasks 6-7; task 7's register is unused upstream, so
            # nothing is captured). The act closes the wave, so the package
            # install below runs only after the repo exists.
            yield op(
                files.download,
                name="Download GitLab repository installation script",
                url=plan.script_url,
                dest=REPO_SCRIPT_SPOOL,
                sudo=True,
            )
            result = yield act(
                Shell,
                command=f"bash {REPO_SCRIPT_SPOOL}",
                name="Install GitLab repository",
                sudo=True,
            )
            if result["exit_status"] != 0:
                raise CommandError(
                    host.name,
                    f"bash {REPO_SCRIPT_SPOOL}",
                    result["exit_status"],
                    result["stderr"],
                )
            # The edition package (tasks 8-9), pin included; it shares its
            # wave with the bootstrap probe below it, so the probe reads the
            # post-install state (the omnibus postinst bootstraps on its own
            # unless it fails). The async ceiling is dropped (D5).
            yield op(
                packages_op,
                name="Install GitLab",
                packages=plan.package_pin,
                sudo=True,
            )
        bootstrapped = yield fact(
            FileExists, BOOTSTRAPPED_PATH, name="Check if GitLab is already configured"
        )
        if not bootstrapped:
            # Task 10: the first reconfigure, ``failed_when: false`` (D7) --
            # the result is data and the body deliberately does not check it.
            yield act(
                Shell,
                command=RECONFIGURE_COMMAND,
                name="Reconfigure GitLab (first run)",
                sudo=True,
            )

        if hv.create_self_signed_cert:
            # Tasks 11-12: the 0700 ssl directory, and the ``creates=`` gate
            # as a FileExists fact (D9) -- the fact re-probes elevated behind
            # the traversal-denying parent, so a root-only certificate is
            # never reported missing. The act that creates it closes the wave
            # either way, mirroring nfs's command-handler shape.
            yield op(
                files.directory,
                name="Create GitLab SSL configuration folder",
                path=SSL_DIR,
                mode="0700",
                user="root",
                group="root",
                sudo=True,
            )
            cert_present = yield fact(
                FileExists,
                plan.ssl_certificate,
                name="Check if the self-signed certificate exists",
            )
            if not cert_present:
                cert = yield act(
                    Shell,
                    command=plan.openssl_command,
                    name="Create self-signed certificate",
                    sudo=True,
                )
                if cert["exit_status"] != 0:
                    raise CommandError(
                        host.name,
                        plan.openssl_command,
                        cert["exit_status"],
                        cert["stderr"],
                    )

        # Wave: the configure requests, closed by the notification read. The
        # checkpoint must immediately follow the stage it reports on.
        yield from configure(plan)(host)
        wave_report = yield checkpoint("configure done")

        # ``restart gitlab``, only on the hosts this checkpoint selected.
        notified = changed_hosts(wave_report, NOTIFYING)
        yield from handler(notified, hv.restart_handler_failed_when)(host)

    return deploy


__all__ = [
    "GitlabVars",
    "gitlab",
]
