# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The Apache role (migrated from ``geerlingguy.apache``).
#
# Installs and configures Apache 2.x on Ubuntu/Debian hosts: the apt cache
# refresh and the ``apache2`` packages, the ``ports.conf`` listen lines, the
# ``mods-enabled``/``conf-enabled`` symlinks, the rendered
# ``sites-available/vhosts.conf`` and its ``sites-enabled`` symlink, the optional
# default-vhost removal, the service's running/enabled state, and the role's
# single ``restart apache`` handler. :func:`apache` is the whole task list, and
# :class:`ApacheVars` is ``defaults/main.yml``, bound from the inventory
# entry's ``apache`` role mapping by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. The role's RHEL, SLES, Solaris and
# Amazon Linux branches are out of scope, so its ``include_vars`` OS dispatch
# collapses to the frozen Debian constants below.
#
# The stream carries three waves **plus one per SSL vhost** (design doc section
# 5.7) -- four with a single certificate:
#
# 0. :func:`install` -- the apt cache refresh and the packages -- closed by the
#    ``apache2 -v`` probe barrier, which only succeeds once the install applied.
# 1..*n* one ``fact(FileExists, path=certificate_file)`` per SSL vhost. A host
#    with no SSL vhost parks on nothing here, so hosts park asymmetrically and the
#    wave count is host-dependent.
# *n*+1 :func:`configure` -- ports, symlinks, the rendered vhosts file, the
#    service state -- closed by ``checkpoint("configure done")``.
# *n*+2 :func:`handler` -- ``Restart``/``Reload apache2 (handler)``, only on the
#    hosts that checkpoint selected.
#
# The three reads the example took *between* its three ``run()`` calls -- the
# version probe, the certificate ``stat`` loop and the configure report -- are
# barriers now, so the vhosts template is rendered inside the body from the values
# those barriers returned. Nothing is frozen in a module-level plan dict, and no
# name set is unioned across the inventory: the per-host body *is* the plan.
#
# :func:`apache` returns :class:`ApacheOutputs` on every path, including the
# family guard's early return, because a composing role captures it with
# ``out = yield from scoped("apache", apache(v))(host)`` (design doc sections 9
# and 10.5).
#
# Deviations from the Ansible role, all deliberate:
#
# * the host-family guard is the body's opening ``if``, not a ``when=`` on every
#   request: a host outside the ``"Ubuntu"`` family returns before it yields
#   anything, so it emits no requests and no log entries at all (the old example
#   logged each as ``[skipped: when guard ...]``).
# * the version probe is an :func:`~reemote.stages.act` barrier, not a fact:
#   ``apache2 -v`` is a command whose answer depends on the install in the same
#   wave, and a barrier never raises on a non-zero exit -- the result is data. The
#   body therefore checks ``exit_status`` and raises :class:`ValueError`, the
#   error the example's ``main()`` raised and the runner maps.
# * the certificate reads yield **one wave each**, so hosts with different
#   certificate counts park asymmetrically. That is the point of the barrier
#   model, but it makes the wave count host-dependent: see the table above. The
#   design sketch gathers them with a generator expression, which Python forbids
#   -- ``yield`` inside one is a ``SyntaxError`` (and so is ``append(yield ...)``)
#   -- so the body builds the list with an explicit loop.
# * the role's ``mode: 0644`` on a *symlink* is dropped --
#   :func:`~reemote.modules.files.link` owns no mode, and a Linux symlink's own
#   mode is always ``0777`` and meaningless (Ansible cannot set it either).
# * ``apache_ssl_no_log`` and ``apache_enablerepo`` are carried as variables and
#   do nothing here: reemote has no per-request log redaction, and the repo knob
#   belongs to the RHEL branch that is out of scope. Both are documented as inert
#   rather than silently omitted, so an existing inventory still binds.
# * ``force={{ ansible_check_mode }}`` on the vhosts symlink becomes the default
#   ``force=False``: a dry run stops before that wave, so check mode has no
#   analogue to reproduce.
# * the handler is per-host and stateless, so the example's ``by_action``
#   partition and its ``compose`` of several ``service_handler`` deploys both go:
#   one body picks its own verb from ``restart_state`` and yields one request.
#   ``restarted``/``reloaded`` always report changed, faithful to Ansible's
#   service module.
# * the role's implicit variable gates become pydantic validation: the three
#   enums are :data:`~typing.Literal` fields, ``servername`` and both certificate
#   paths are required, and a bad value raises ``ValidationError`` (a
#   ``ValueError``, so the runner still maps it) before any host is contacted.
# """

import re
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from ..actions import Shell
from ..core import op
from ..handlers import changed_hosts, service_handler
from ..hosts import Host
from ..modules import apt, files, systemd
from ..modules.files import FileExists
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, act, checkpoint, fact

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path only) ---

#: ``setup-Debian.yml``'s ``apt: update_cache=yes cache_valid_time=3600``.
CACHE_VALID_TIME = 3600

#: ``vars/Debian.yml``, frozen to the single Ubuntu target. The daemon path and
#: name build the version probe; the two roots build every managed path.
DEBIAN_SERVICE = "apache2"
DEBIAN_DAEMON = "apache2"
DEBIAN_DAEMON_PATH = "/usr/sbin/"
SERVER_ROOT = "/etc/apache2"
CONF_PATH = "/etc/apache2"

#: ``__apache_packages`` (Debian), used when :attr:`ApacheVars.packages` is null
#: -- the role's ``set_fact`` fallback for an undefined ``apache_packages``.
DEBIAN_PACKAGES = ("apache2", "apache2-utils")

#: ``vars/apache-24.yml`` / ``vars/apache-22.yml``, selected by the probed
#: version's minor number exactly as the role's two ``include_vars`` tasks do.
APACHE_24_VARS = {"vhosts_version": "2.4", "default_vhost_filename": "000-default.conf"}
APACHE_22_VARS = {"vhosts_version": "2.2", "default_vhost_filename": "000-default"}

#: The name the role's bundled ``templates/vhosts.conf.j2`` is identified by;
#: anything else in :attr:`ApacheVars.vhosts_template` is a controller-side path.
BUNDLED_TEMPLATE = "vhosts.conf.j2"

#: ``templates/vhosts.conf.j2``, verbatim. Rendered control-side by
#: :func:`render_vhosts` (reemote has no template engine and a deploy body has
#: no business reading a host file). Only built-in Jinja2 filters are used
#: (``indent(width=2, first=True)``, ``default()``), so the plain
#: :func:`~reemote.roles._render.jinja2_environment` with Ansible's whitespace
#: defaults reproduces the role's output.
VHOSTS_TEMPLATE = """{{ apache_global_vhost_settings }}

{# Set up VirtualHosts #}
{% for vhost in apache_vhosts %}
<VirtualHost {{ apache_listen_ip }}:{{ apache_listen_port }}>
  ServerName {{ vhost.servername }}
{% if vhost.serveralias is defined %}
  ServerAlias {{ vhost.serveralias }}
{% endif %}
{% if vhost.documentroot is defined %}
  DocumentRoot "{{ vhost.documentroot }}"
{% endif %}

{% if vhost.serveradmin is defined %}
  ServerAdmin {{ vhost.serveradmin }}
{% endif %}
{% if vhost.documentroot is defined %}
  <Directory "{{ vhost.documentroot }}">
    AllowOverride {{ vhost.allow_override | default(apache_allow_override) }}
    Options {{ vhost.options | default(apache_options) }}
{% if apache_vhosts_version == "2.2" %}
    Order allow,deny
    Allow from all
{% else %}
    Require all granted
{% endif %}
  </Directory>
{% endif %}
{% if vhost.extra_parameters is defined %}
{{ vhost.extra_parameters | indent(width=2, first=True) }}
{% endif %}
</VirtualHost>

{% endfor %}

{# Set up SSL VirtualHosts #}
{% for vhost in apache_vhosts_ssl %}
{% if apache_ignore_missing_ssl_certificate or apache_ssl_certificates.results[loop.index0].stat.exists %}
<VirtualHost {{ apache_listen_ip }}:{{ apache_listen_port_ssl }}>
  ServerName {{ vhost.servername }}
{% if vhost.serveralias is defined %}
  ServerAlias {{ vhost.serveralias }}
{% endif %}
{% if vhost.documentroot is defined %}
  DocumentRoot "{{ vhost.documentroot }}"
{% endif %}

  SSLEngine on
  SSLCipherSuite {{ apache_ssl_cipher_suite }}
  SSLProtocol {{ apache_ssl_protocol }}
  SSLHonorCipherOrder On
{% if apache_vhosts_version == "2.4" %}
  SSLCompression off
{% endif %}
  SSLCertificateFile {{ vhost.certificate_file }}
  SSLCertificateKeyFile {{ vhost.certificate_key_file }}
{% if vhost.certificate_chain_file is defined %}
  SSLCertificateChainFile {{ vhost.certificate_chain_file }}
{% endif %}

{% if vhost.serveradmin is defined %}
  ServerAdmin {{ vhost.serveradmin }}
{% endif %}
{% if vhost.documentroot is defined %}
  <Directory "{{ vhost.documentroot }}">
    AllowOverride {{ vhost.allow_override | default(apache_allow_override) }}
    Options {{ vhost.options | default(apache_options) }}
{% if apache_vhosts_version == "2.2" %}
    Order allow,deny
    Allow from all
{% else %}
    Require all granted
{% endif %}
  </Directory>
{% endif %}
{% if vhost.extra_parameters is defined %}
{{ vhost.extra_parameters | indent(width=2, first=True) }}
{% endif %}
</VirtualHost>

{% endif %}
{% endfor %}
"""


# --- variables ---------------------------------------------------------------


class VHost(BaseModel):
    """One plain virtual host: an item of ``apache_vhosts``.

    Only :attr:`servername` is required, which is the role's own gate. The
    optional keys the template reads stay ``None`` so that
    ``model_dump(exclude_none=True)`` omits them and the template's
    ``{% if vhost.documentroot is defined %}`` branches behave exactly like
    Ansible's undefined check -- declaring them with a value default would
    render an empty ``DocumentRoot ""`` for a vhost that has none.

    ``extra="allow"`` keeps any further key the role's templates learn to read,
    and the model is frozen so the containing :class:`ApacheVars` stays
    hashable.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    servername: str
    serveralias: str | None = None
    documentroot: str | None = None
    serveradmin: str | None = None
    allow_override: str | None = None
    options: str | None = None
    extra_parameters: str | None = None

    @field_validator("servername")
    @classmethod
    def _servername_required(cls, value: str) -> str:
        """The role's ``item.servername is defined`` gate, as a validator."""
        if not value:
            raise ValueError("a vhost needs a servername")
        return value


class SslVHost(VHost):
    """One SSL virtual host: an item of ``apache_vhosts_ssl``.

    Both certificate paths are required -- the role's ``a_vhosts_ssl`` loop
    fails without them, here as a ``ValidationError`` raised while the
    inventory is resolved rather than while a deploy is built.
    """

    certificate_file: str
    certificate_key_file: str
    certificate_chain_file: str | None = None

    @field_validator("certificate_file", "certificate_key_file")
    @classmethod
    def _certificate_path_required(cls, value: str) -> str:
        """An empty certificate path is a typo, not "no certificate"."""
        if not value:
            raise ValueError("an SSL vhost needs a certificate path")
        return value


class ApacheVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``apache`` role mapping, so ``listen_port`` there sets
    :attr:`listen_port`. Every Ansible default is carried over, including the
    two inert ones (:attr:`enablerepo`, :attr:`ssl_no_log`) so such a key
    still binds.

    Sequences are ``tuple``, never ``list``: the model is frozen and hashable,
    and a value that reaches :func:`~reemote.core.op` must hash (design doc
    section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: RHEL-only, out of scope: kept so an existing inventory binds and noops.
    enablerepo: str = ""
    listen_ip: str = "*"
    listen_port: int = 80
    listen_port_ssl: int = 443
    create_vhosts: bool = True
    vhosts_filename: str = "vhosts.conf"
    #: ``vhosts.conf.j2`` is the bundled template; anything else is read from
    #: the controller's filesystem by :func:`render_vhosts`.
    vhosts_template: str = BUNDLED_TEMPLATE
    remove_default_vhost: bool = False
    global_vhost_settings: str = "DirectoryIndex index.php index.html\n"
    vhosts: tuple[VHost, ...] = (
        VHost(servername="local.dev", documentroot="/var/www/html"),
    )
    allow_override: str = "All"
    options: str = "-Indexes +FollowSymLinks"
    vhosts_ssl: tuple[SslVHost, ...] = ()
    ignore_missing_ssl_certificate: bool = True
    #: reemote has no per-request log redaction: kept, and inert (see D5).
    ssl_no_log: bool = True
    ssl_protocol: str = "All -SSLv2 -SSLv3"
    ssl_cipher_suite: str = "AES256+EECDH:AES256+EDH"
    mods_enabled: tuple[str, ...] = ("rewrite", "ssl")
    mods_disabled: tuple[str, ...] = ()
    conf_enabled: tuple[str, ...] = ()
    conf_disabled: tuple[str, ...] = ()
    #: The role's ``apache_state``: what the final service task asks for.
    state: Literal["started", "stopped", "restarted", "reloaded"] = "started"
    enabled: bool = True
    #: The verb the handler uses when a notifying request changed something.
    restart_state: Literal["restarted", "reloaded", "started"] = "restarted"
    packages_state: Literal["present", "latest", "absent"] = "present"
    #: ``null`` falls back to :data:`DEBIAN_PACKAGES`, as the role's ``set_fact``
    #: does for an undefined ``apache_packages``.
    packages: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ApacheOutputs:
    """What :func:`apache` publishes to a composing role.

    ``version`` is the probed server version, empty on a host the role skipped;
    ``service`` is the frozen Debian unit name. A return value, not a registry:
    the cross-role channels in design doc sections 9 and 10.4 pass it by value.
    """

    version: str
    service: str = DEBIAN_SERVICE


#: One ``ports.conf`` edit: the regexp, the line, and the frozen transform.
PortRule = tuple[str, str, Callable[[str | None], str]]


@dataclass(frozen=True)
class ApachePlan:
    """One host's resolved configure targets, built by :func:`configure_plan`.

    Every value the configure and handler stages need is decided *before* the
    first request of their wave is built -- the version table, the ports
    transforms, the rendered vhosts text -- so both stages are a pure function
    of this one frozen object. It replaces the example's ``plans`` dict keyed by
    host name: under a per-host body, the plan is a local.
    """

    ports: tuple[PortRule, ...]
    vhosts_content: str
    mods_enabled: tuple[str, ...]
    mods_disabled: tuple[str, ...]
    conf_enabled: tuple[str, ...]
    conf_disabled: tuple[str, ...]
    create_vhosts: bool
    vhosts_filename: str
    remove_default_vhost: bool
    default_vhost_filename: str
    state: str
    enabled: bool
    restart_state: str
    service: str = DEBIAN_SERVICE
    server_root: str = SERVER_ROOT
    conf_path: str = CONF_PATH


# --- pure helpers ------------------------------------------------------------


def parse_apache_version(stdout: str) -> str:
    """The role's ``_apache_version.stdout.split()[2].split('/')[1]``.

    ``apache2 -v`` prints ``Server version: Apache/2.4.58 (Ubuntu)``; the third
    whitespace field is ``Apache/2.4.58`` and the part after the slash is the
    version. A malformed probe is a hard error, exactly as the role's
    ``set_fact`` would fail on an unexpected stdout.
    """
    fields = stdout.split()
    if len(fields) < 3 or "/" not in fields[2]:
        raise ValueError(f"cannot parse Apache version from probe output: {stdout!r}")
    return fields[2].split("/")[1]


def lineinfile_transform(regexp: str, line: str) -> Callable[[str | None], str]:
    """Ansible ``lineinfile {regexp, line, state: present}`` as a pure transform.

    Built once per host, in :func:`configure_plan`, so the callable a request
    carries is stable for the whole run. Semantics, matching the module: when
    the exact ``line`` is already present the content is returned verbatim (no
    change); otherwise the LAST line matching ``regexp`` is replaced, or
    ``line`` is appended when none matched; a write ends with exactly one
    trailing newline. The result is a fixpoint:
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


def ports_items(v: ApacheVars, vhosts_version: str) -> tuple[tuple[str, str], ...]:
    """The ``apache_ports_configuration_items`` for the probed version.

    Reproduces ``vars/apache-24.yml`` (one ``Listen`` line, the IP prefix
    dropped for ``*`` via the role's ``ternary``) and ``vars/apache-22.yml`` (a
    ``Listen`` line plus a ``NameVirtualHost`` line).
    """
    ip = v.listen_ip
    port = v.listen_port
    if vhosts_version == "2.4":
        prefix = "" if ip == "*" else f"{ip}:"
        return ((r"^Listen ", f"Listen {prefix}{port}"),)
    return (
        (r"^Listen ", f"Listen {port}"),
        (r"^#?NameVirtualHost ", f"NameVirtualHost {ip}:{port}"),
    )


def render_vhosts(v: ApacheVars, vhosts_version: str, ssl_exists: tuple[bool, ...]) -> str:
    """``templates/vhosts.conf.j2``, pre-rendered control-side.

    ``ssl_exists`` is the :class:`~reemote.modules.files.FileExists` snapshot,
    aligned with :attr:`ApacheVars.vhosts_ssl` order, reshaped into the
    ``register:`` structure the template reads
    (``apache_ssl_certificates.results[i].stat.exists``). Vhosts are dumped with
    ``exclude_none=True`` so an unset optional key stays *undefined* for the
    template rather than rendering as ``None``.

    A custom ``vhosts_template`` is a controller-side path, read here rather
    than on the host; the bundled template needs no filesystem read. The
    jinja2 import is inside this function so that importing ``reemote.roles``
    stays clean without the ``reemote[templates]`` extra (§17.4).
    """
    from ._render import jinja2_environment

    template_text = (
        VHOSTS_TEMPLATE
        if v.vhosts_template == BUNDLED_TEMPLATE
        else Path(v.vhosts_template).read_text(encoding="utf-8")
    )
    template = jinja2_environment().from_string(template_text)
    return template.render(
        apache_global_vhost_settings=v.global_vhost_settings,
        apache_vhosts=[vhost.model_dump(exclude_none=True) for vhost in v.vhosts],
        apache_vhosts_ssl=[vhost.model_dump(exclude_none=True) for vhost in v.vhosts_ssl],
        apache_listen_ip=v.listen_ip,
        apache_listen_port=v.listen_port,
        apache_listen_port_ssl=v.listen_port_ssl,
        apache_allow_override=v.allow_override,
        apache_options=v.options,
        apache_vhosts_version=vhosts_version,
        apache_ignore_missing_ssl_certificate=v.ignore_missing_ssl_certificate,
        apache_ssl_certificates={
            "results": [{"stat": {"exists": exists}} for exists in ssl_exists]
        },
        apache_ssl_cipher_suite=v.ssl_cipher_suite,
        apache_ssl_protocol=v.ssl_protocol,
    )


def configure_plan(v: ApacheVars, version: str, ssl_exists: tuple[bool, ...]) -> ApachePlan:
    """Render the vhosts file and freeze every read the configure stage needs.

    Control-side work performed inside the body, after the probe and
    certificate barriers: the probed ``version`` selects the 2.2/2.4 variable
    table (the role's two ``include_vars`` tasks), the ports ``lineinfile`` items
    become stable pure transforms, and the Jinja2 template becomes a plain
    ``str``.

    Raises:
        ValueError: The probed version is neither 2.2.x nor 2.4.x, so no
            variable table applies. Raised before the first request of the
            configure wave is built, so nothing is half-applied.
    """
    minor = version.split(".")[1] if "." in version else ""
    if minor == "2":
        table = APACHE_22_VARS
    elif minor == "4":
        table = APACHE_24_VARS
    else:
        raise ValueError(f"unsupported Apache version {version!r}: expected 2.2.x or 2.4.x")
    vhosts_version = table["vhosts_version"]
    return ApachePlan(
        ports=tuple(
            (regexp, line, lineinfile_transform(regexp, line))
            for regexp, line in ports_items(v, vhosts_version)
        ),
        vhosts_content=render_vhosts(v, vhosts_version, ssl_exists),
        mods_enabled=v.mods_enabled,
        mods_disabled=v.mods_disabled,
        conf_enabled=v.conf_enabled,
        conf_disabled=v.conf_disabled,
        create_vhosts=v.create_vhosts,
        vhosts_filename=v.vhosts_filename,
        remove_default_vhost=v.remove_default_vhost,
        default_vhost_filename=table["default_vhost_filename"],
        state=v.state,
        enabled=v.enabled,
        restart_state=v.restart_state,
    )


def notifying_names(plan: ApachePlan) -> tuple[str, ...]:
    """Every configure request name that queues ``restart apache``.

    Enumerated from the same frozen plan the deploy was built from, so the
    notification set matches the requests exactly. The final service task
    carries no ``notify`` in the role, so its name is deliberately absent.
    """
    names = ["Set Apache ports.conf mode"]
    names.extend(f"Configure Apache ports ({regexp})" for regexp, _line, _t in plan.ports)
    names.extend(f"Enable Apache mod {mod}" for mod in plan.mods_enabled)
    names.extend(f"Disable Apache mod {mod}" for mod in plan.mods_disabled)
    names.extend(f"Enable Apache config {conf}" for conf in plan.conf_enabled)
    names.extend(f"Disable Apache config {conf}" for conf in plan.conf_disabled)
    if plan.create_vhosts:
        names.append("Add apache vhosts configuration")
        names.append("Add vhost symlink in sites-enabled")
    if plan.remove_default_vhost:
        names.append("Remove default vhost in sites-enabled")
    return tuple(names)


# --- helper deploys ----------------------------------------------------------


def install(v: ApacheVars) -> Deploy:
    """``tasks/setup-Debian.yml``: the apt cache refresh and the packages.

    Branch-free with respect to facts: the package list and the
    present/latest/absent flag come from the resolved variables. The cache
    refresh is ``apt.update(cache_valid_time=3600)`` -- idempotent within the
    window via the ``AptUpdateStamp`` fact, so a converged re-run reports no
    change (Ansible's ``update_cache=yes cache_valid_time=3600``). Neither task
    notifies in the role.
    """
    packages = v.packages or DEBIAN_PACKAGES

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            apt.update,
            name="Update apt cache",
            cache_valid_time=CACHE_VALID_TIME,
            sudo=True,
        )
        yield op(
            apt.packages,
            name="Ensure Apache is installed on Debian",
            packages=packages,
            present=v.packages_state != "absent",
            latest=v.packages_state == "latest",
            sudo=True,
        )

    return deploy


def configure(plan: ApachePlan) -> Deploy:
    """``tasks/configure-Debian.yml`` plus the final service task.

    Every read the role takes mid-play (the version probe, the certificate
    ``stat``) already reached the body as a barrier, so this deploy is a pure
    function of the frozen plan. Each notifying request carries a stable
    ``name=`` enumerated by :func:`notifying_names`.
    """
    ports_conf = f"{plan.server_root}/ports.conf"

    def deploy(host: Host) -> Generator[StageItem, Any]:
        # lineinfile ports.conf (with_items apache_ports_configuration_items).
        # files.transform owns the content edit; the paired files.file
        # converges the mode:0644 the lineinfile task sets, because transform
        # idempotence is content-only (a mode-only drift would not re-install).
        for regexp, _line, transform in plan.ports:
            yield op(
                files.transform,
                name=f"Configure Apache ports ({regexp})",
                path=ports_conf,
                transform=transform,
                mode="0644",
                sudo=True,
            )
        yield op(
            files.file,
            name="Set Apache ports.conf mode",
            path=ports_conf,
            mode="0644",
            sudo=True,
        )

        # Enable/disable mods and conf via symlinks (see D4 on their modes).
        # Removals use force=True so a regular file at the path is removed too,
        # not just a symlink.
        for mod in plan.mods_enabled:
            yield op(
                files.link,
                name=f"Enable Apache mod {mod}",
                path=f"{plan.server_root}/mods-enabled/{mod}.load",
                target=f"{plan.server_root}/mods-available/{mod}.load",
                sudo=True,
            )
        for mod in plan.mods_disabled:
            yield op(
                files.link,
                name=f"Disable Apache mod {mod}",
                path=f"{plan.server_root}/mods-enabled/{mod}.load",
                present=False,
                force=True,
                sudo=True,
            )
        for conf in plan.conf_enabled:
            yield op(
                files.link,
                name=f"Enable Apache config {conf}",
                path=f"{plan.server_root}/conf-enabled/{conf}.conf",
                target=f"{plan.server_root}/conf-available/{conf}.conf",
                sudo=True,
            )
        for conf in plan.conf_disabled:
            yield op(
                files.link,
                name=f"Disable Apache config {conf}",
                path=f"{plan.server_root}/conf-enabled/{conf}.conf",
                present=False,
                force=True,
                sudo=True,
            )

        # The vhosts template, rendered control-side in configure_plan, and its
        # sites-enabled symlink. The role's force={{ ansible_check_mode }} is
        # False in a real run, so the symlink uses the default (see D6).
        if plan.create_vhosts:
            yield op(
                files.install,
                name="Add apache vhosts configuration",
                content=plan.vhosts_content,
                dest=f"{plan.conf_path}/sites-available/{plan.vhosts_filename}",
                mode="0644",
                user="root",
                group="root",
                sudo=True,
            )
            yield op(
                files.link,
                name="Add vhost symlink in sites-enabled",
                path=f"{plan.conf_path}/sites-enabled/{plan.vhosts_filename}",
                target=f"{plan.conf_path}/sites-available/{plan.vhosts_filename}",
                sudo=True,
            )
        if plan.remove_default_vhost:
            yield op(
                files.link,
                name="Remove default vhost in sites-enabled",
                path=f"{plan.conf_path}/sites-enabled/{plan.default_vhost_filename}",
                present=False,
                force=True,
                sudo=True,
            )

        # The final service task (no notify). apache_state maps to the
        # systemd.service verbs; restarted/reloaded are stateless (they always
        # report changed), faithful to Ansible's service module -- the default
        # started is fact-gated and converges.
        state = plan.state
        yield op(
            systemd.service,
            name="Ensure Apache has selected state and enabled on boot",
            service=plan.service,
            running=state != "stopped",
            restarted=state == "restarted",
            reloaded=state == "reloaded",
            enabled=plan.enabled,
            sudo=True,
        )

    return deploy


def handler(plan: ApachePlan, notified: frozenset[str]) -> Deploy:
    """``handlers/main.yml``: ``restart apache`` with ``state={{ apache_restart_state }}``.

    A per-host body needs none of the example's machinery: no ``by_action``
    partition of the notified set and no ``compose`` of one handler per action,
    because this host has exactly one ``restart_state``. ``started`` maps to
    ``service_handler``'s ``"running"`` verb; an empty ``notified`` set yields no
    request, which is the converged no-op Ansible's handler gives for free.

    ``notified`` is read from the wave the checkpoint closed, so on interleaved
    hosts it may name machines this body does not act on --
    :func:`~reemote.handlers.service_handler` branches on ``host.name`` itself,
    which is what keeps the wave a host's own.
    """
    action = "running" if plan.restart_state == "started" else plan.restart_state
    return service_handler(notified, (plan.service,), action=action)


# --- the role ----------------------------------------------------------------


def apache(v: VarsSet[ApacheVars]) -> Deploy:
    """Install and configure Apache on Ubuntu hosts, and run its handler.

    Args:
        v: The role's resolved variables, one :class:`ApacheVars` per host
            (``resolve_vars(ApacheVars, "apache", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves` that returns
        :class:`ApacheOutputs` for every host it runs -- including a host outside
        the target family, whose outputs carry an empty version.

    Raises:
        ValueError: The version probe exited non-zero (the binary is missing or
            unreadable), its output is not a version banner, or the probed
            version has no variable table. Each is raised inside the body, after
            the install wave applied and before the configure wave is built.
    """

    def deploy(host: Host) -> Generator[StageItem, Any, ApacheOutputs]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            # The outputs still have to be published: a composing role captures
            # this return value with `yield from` (§18.2).
            return ApacheOutputs(version="")

        # Wave 0: the packages. The probe barrier below closes the wave, which
        # is what makes them installed before it runs.
        yield from install(hv)(host)

        result = yield act(
            Shell,
            command=f"{DEBIAN_DAEMON_PATH}{DEBIAN_DAEMON} -v",
            name="apache -v",
        )
        # A barrier never raises on a non-zero exit -- the Result is data -- so
        # the example's main() check moves here.
        if result["exit_status"] != 0:
            raise ValueError(
                f"{host.name}: Apache version probe failed "
                f"(exit {result['exit_status']}): {result['stderr'].strip()}"
            )
        version = parse_apache_version(result["stdout"])

        # One wave per certificate. A generator expression cannot hold these
        # yields -- `yield` inside one is a SyntaxError -- so it is a loop.
        ssl_exists: list[bool] = []
        for vhost in hv.vhosts_ssl:
            exists = yield fact(FileExists, path=vhost.certificate_file)
            ssl_exists.append(bool(exists))

        plan = configure_plan(hv, version, tuple(ssl_exists))

        # Wave n+1: configure, then read its own outcome. The checkpoint must
        # immediately follow the stage it reports on; one placed after the
        # handler wave would read the handler's entries and notify nobody.
        yield from configure(plan)(host)
        wave_report = yield checkpoint("configure done")

        notified = changed_hosts(wave_report, notifying_names(plan))

        # Wave n+2: the handler, only on the hosts the checkpoint selected.
        yield from handler(plan, notified)(host)

        return ApacheOutputs(version=version)

    return deploy


__all__ = [
    "ApacheOutputs",
    "ApacheVars",
    "apache",
]
