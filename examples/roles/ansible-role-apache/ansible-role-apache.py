# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Migration of the Apache Ansible role (``geerlingguy.apache``) to reemote.

The role at ``https://github.com/geerlingguy/ansible-role-apache`` installs and
configures Apache 2.x on RHEL/CentOS, Debian/Ubuntu, SLES and Solaris. This
program is its **Ubuntu/Debian default path** expressed as a reemote deploy:
the apt cache refresh and the ``apache2`` packages, the ``ports.conf`` listen
lines, the ``mods-enabled``/``conf-enabled`` symlinks, the rendered
``sites-available/vhosts.conf`` and its ``sites-enabled`` symlink, the optional
default-vhost removal, the service's running/enabled state, and the role's
single ``restart apache`` handler. Every role variable is read from the
inventory entry's extra keys (reemote's ``host_vars`` mechanism), so
``apache_listen_port`` and friends live beside ``host``/``connection`` in
``inventory.json``.

Target: hosts in the ``"Ubuntu"`` OS family. Every request carries
``when=is_ubuntu``, and a skipped request is never materialized -- the other
hosts in the shared inventory are not even contacted. The role's RHEL, SLES,
Solaris and Amazon Linux branches are out of scope (a Phase 0 single-OS
decision), so the ``include_vars`` OS dispatch collapses to frozen Debian
constants below.

Why the program is a *sequence* of deploys rather than one
-------------------------------------------------------

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role
reads the installed Apache version with a ``command:`` probe and ``stat``s the
SSL certificates, then feeds both into the vhosts template -- mutable reads a
single deploy may not branch on. Each becomes a **gather -> snapshot ->
factory** step: read between two complete runs, freeze the answer into a
per-host dict, close a deploy factory over it. The barriers below are
load-bearing, not stylistic:

1. ``deploy_install``      -- apt cache refresh + the apache2 packages
2. ``gather_action(Shell)``-- ``/usr/sbin/apache2 -v`` version probe (after the
   install apply, so the binary exists); the parsed version selects the
   ``apache-22.yml`` / ``apache-24.yml`` variable table
3. ``gather_fact(FileExists)`` -- the SSL certificate existence reads the
   template's ``apache_ssl_certificates.results[i].stat.exists`` consumes
4. ``build_configure``     -- ports.conf, mod/conf symlinks, the pre-rendered
   vhosts file, the default-vhost removal, the final service state
5. ``build_handler``       -- ``changed_hosts`` over the configure report's
   notifying names, split by ``apache_restart_state`` into
   ``service_handler(action="restarted"|"reloaded")``

Implementation notes
--------------------

The one gap proven while mapping the role was a *test-framework* one:
``SimulatedHost`` exposed no ``_run_shell`` action transport seam (#16), so
the ``Shell`` version probe could not be gathered offline. It is closed --
the class now answers ``gather_action``'s ``Shell``/``Script`` gathers from
a seeded ``shell_results`` map (``tests/test_simulated_host.py`` proves the
seam), and ``tests/test_roles_ansible_role_apache.py`` seeds the
``apache2 -v`` record there instead of its former ``_run_shell`` shim.

Everything else mapped to a covered operation or a documented recipe; the
deliberate deviations (symlink modes dropped, ``no_log`` boundary, the
control-side Jinja2 render, the stateless ``restarted``/``reloaded`` handler)
are recorded at the point of use and in ``ansible-role-apache.md``.

Prerequisites: Jinja2 (``--with jinja2``) renders the role's
``vhosts.conf.j2`` template; it is deliberately *not* a reemote dependency.

Run it with::

    uv run --with jinja2 python examples/roles/ansible-role-apache/ansible-role-apache.py
"""

import asyncio
import json
import re
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    FileExists,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    Shell,
    apt,
    changed_hosts,
    compose,
    files,
    format_log,
    format_plan,
    gather_action,
    gather_fact,
    op,
    run,
    service_handler,
    systemd,
    validate_inventory,
)

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path only) ---

#: ``setup-Debian.yml``'s ``apt: update_cache=yes cache_valid_time=3600``.
CACHE_VALID_TIME = 3600

#: ``vars/Debian.yml`` -- the OS dispatch frozen to the single Ubuntu target.
DEBIAN_VARS = {
    "service": "apache2",
    "daemon": "apache2",
    "daemon_path": "/usr/sbin/",
    "server_root": "/etc/apache2",
    "conf_path": "/etc/apache2",
}
#: ``__apache_packages`` (Debian), used when ``apache_packages`` is undefined.
DEBIAN_PACKAGES = ("apache2", "apache2-utils")

#: ``vars/apache-24.yml`` / ``vars/apache-22.yml`` -- selected by the probed
#: version's minor number, exactly as the role's two ``include_vars`` tasks do.
APACHE_24_VARS = {"vhosts_version": "2.4", "default_vhost_filename": "000-default.conf"}
APACHE_22_VARS = {"vhosts_version": "2.2", "default_vhost_filename": "000-default"}

#: ``templates/vhosts.conf.j2``, verbatim. Rendered control-side in
#: ``configure_plan`` (reemote has no template engine and no controller
#: filesystem paths inside a deploy). Only built-in Jinja2 filters are used
#: (``indent(width=2, first=True)``, ``default()``), so a plain ``Environment``
#: with Ansible's whitespace defaults reproduces the role's output.
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

#: The role defaults this migration honours (``defaults/main.yml``). The
#: ``apache_`` prefix is kept so an inventory entry reads like the role's
#: ``host_vars``. ``apache_packages`` defaults to None -- the role's
#: ``set_fact`` falls back to ``__apache_packages`` when it is undefined.
ROLE_DEFAULTS: dict[str, Any] = {
    "apache_enablerepo": "",
    "apache_listen_ip": "*",
    "apache_listen_port": 80,
    "apache_listen_port_ssl": 443,
    "apache_create_vhosts": True,
    "apache_vhosts_filename": "vhosts.conf",
    "apache_vhosts_template": "vhosts.conf.j2",
    "apache_remove_default_vhost": False,
    "apache_global_vhost_settings": "DirectoryIndex index.php index.html\n",
    "apache_vhosts": [{"servername": "local.dev", "documentroot": "/var/www/html"}],
    "apache_allow_override": "All",
    "apache_options": "-Indexes +FollowSymLinks",
    "apache_vhosts_ssl": [],
    "apache_ignore_missing_ssl_certificate": True,
    "apache_ssl_no_log": True,
    "apache_ssl_protocol": "All -SSLv2 -SSLv3",
    "apache_ssl_cipher_suite": "AES256+EECDH:AES256+EDH",
    "apache_mods_enabled": ["rewrite", "ssl"],
    "apache_mods_disabled": [],
    "apache_conf_enabled": [],
    "apache_conf_disabled": [],
    "apache_state": "started",
    "apache_enabled": True,
    "apache_restart_state": "restarted",
    "apache_packages_state": "present",
    "apache_packages": None,
}

#: The single handler's name (``handlers/main.yml``: ``restart apache``).
HANDLER_NAME = "restart apache"


# --- pure helpers ------------------------------------------------------------



def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data and stays a stable module-level
    function, so both deploy phases reach the same eligibility decision.
    A skipped request is never materialized, so the Alpine and CentOS
    hosts in the shared inventory are not contacted at all.
    """
    return host.family == "Ubuntu"


def _vhost_list(value: Any, key: str, host: Host) -> tuple[dict[str, Any], ...]:
    """Validate a vhost list and freeze it to a tuple of mappings.

    The role's ``apache_vhosts`` / ``apache_vhosts_ssl`` are lists of dicts;
    a type gate raises ``TypeError`` (ruff TRY004), a missing required field
    raises ``ValueError`` -- both in ``main()`` before any host is contacted.
    """
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{host.name}: {key} must be a list of mappings")
    vhosts = []
    for index, vhost in enumerate(value):
        if not isinstance(vhost, dict):
            raise TypeError(f"{host.name}: {key}[{index}] must be a mapping")
        if not vhost.get("servername"):
            raise ValueError(f"{host.name}: {key}[{index}] needs a servername")
        vhosts.append(vhost)
    return tuple(vhosts)


def role_settings(host: Host) -> dict[str, Any]:
    """Freeze one host's role variables: defaults overlaid with ``host.extra``.

    Reads only static inventory data, so a deploy body may call it and branch
    on the result -- both phases see the same dict. The role's variable
    resolution (``apache_packages`` falling back to ``__apache_packages``) and
    its implicit type/enum gates happen here, reproducing ``defaults/main.yml``
    plus the ``set_fact`` in ``tasks/main.yml``.
    """
    merged = {**ROLE_DEFAULTS, **host.extra}
    packages = merged["apache_packages"]
    if packages is None:
        packages = DEBIAN_PACKAGES
    state = str(merged["apache_state"])
    if state not in ("started", "stopped", "restarted", "reloaded"):
        raise ValueError(
            f"{host.name}: apache_state must be started/stopped/restarted/reloaded, "
            f"got {state!r}"
        )
    restart_state = str(merged["apache_restart_state"])
    if restart_state not in ("restarted", "reloaded", "started"):
        raise ValueError(
            f"{host.name}: apache_restart_state must be restarted/reloaded/started, "
            f"got {restart_state!r}"
        )
    packages_state = str(merged["apache_packages_state"])
    if packages_state not in ("present", "latest", "absent"):
        raise ValueError(
            f"{host.name}: apache_packages_state must be present/latest/absent, "
            f"got {packages_state!r}"
        )
    vhosts = _vhost_list(merged["apache_vhosts"], "apache_vhosts", host)
    vhosts_ssl = _vhost_list(merged["apache_vhosts_ssl"], "apache_vhosts_ssl", host)
    for index, vhost in enumerate(vhosts_ssl):
        for field in ("certificate_file", "certificate_key_file"):
            if not vhost.get(field):
                raise ValueError(
                    f"{host.name}: apache_vhosts_ssl[{index}] needs a {field}"
                )
    return {
        "listen_ip": str(merged["apache_listen_ip"]),
        "listen_port": int(merged["apache_listen_port"]),
        "listen_port_ssl": int(merged["apache_listen_port_ssl"]),
        "create_vhosts": bool(merged["apache_create_vhosts"]),
        "vhosts_filename": str(merged["apache_vhosts_filename"]),
        "vhosts_template": str(merged["apache_vhosts_template"]),
        "remove_default_vhost": bool(merged["apache_remove_default_vhost"]),
        "global_vhost_settings": str(merged["apache_global_vhost_settings"]),
        "vhosts": vhosts,
        "allow_override": str(merged["apache_allow_override"]),
        "options": str(merged["apache_options"]),
        "vhosts_ssl": vhosts_ssl,
        "ignore_missing_ssl_certificate": bool(
            merged["apache_ignore_missing_ssl_certificate"]
        ),
        "ssl_protocol": str(merged["apache_ssl_protocol"]),
        "ssl_cipher_suite": str(merged["apache_ssl_cipher_suite"]),
        "mods_enabled": tuple(merged["apache_mods_enabled"] or ()),
        "mods_disabled": tuple(merged["apache_mods_disabled"] or ()),
        "conf_enabled": tuple(merged["apache_conf_enabled"] or ()),
        "conf_disabled": tuple(merged["apache_conf_disabled"] or ()),
        "state": state,
        "enabled": bool(merged["apache_enabled"]),
        "restart_state": restart_state,
        "packages_state": packages_state,
        "packages": tuple(packages),
    }


def parse_apache_version(stdout: str) -> str:
    """The role's ``_apache_version.stdout.split()[2].split('/')[1]``.

    ``apache2 -v`` prints ``Server version: Apache/2.4.58 (Ubuntu)``; the
    third whitespace field is ``Apache/2.4.58`` and the part after the slash
    is the version. A malformed probe is a hard error, exactly as the role's
    ``set_fact`` would fail on an unexpected stdout.
    """
    fields = stdout.split()
    if len(fields) < 3 or "/" not in fields[2]:
        raise ValueError(f"cannot parse Apache version from probe output: {stdout!r}")
    return fields[2].split("/")[1]


def lineinfile_transform(regexp: str, line: str) -> Callable[[str | None], str]:
    """Ansible ``lineinfile {regexp, line, state: present}`` as a pure transform.

    The factory is called ONCE control-side (in ``configure_plan``) so the
    returned callable is a stable, hashable object across both deploy phases.
    Semantics, matching the module: when the exact ``line`` is already present
    the content is returned verbatim (no change); otherwise the LAST line
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


def ports_items(settings: dict[str, Any], vhosts_version: str) -> tuple[tuple[str, str], ...]:
    """The ``apache_ports_configuration_items`` for the probed version.

    Reproduces ``vars/apache-24.yml`` (one ``Listen`` line, the IP prefix
    dropped for ``*`` via the role's ``ternary``) and ``vars/apache-22.yml``
    (a ``Listen`` line plus a ``NameVirtualHost`` line).
    """
    ip = settings["listen_ip"]
    port = settings["listen_port"]
    if vhosts_version == "2.4":
        prefix = "" if ip == "*" else f"{ip}:"
        return ((r"^Listen ", f"Listen {prefix}{port}"),)
    return (
        (r"^Listen ", f"Listen {port}"),
        (r"^#?NameVirtualHost ", f"NameVirtualHost {ip}:{port}"),
    )


def render_vhosts(
    settings: dict[str, Any], vhosts_version: str, ssl_exists: tuple[bool, ...]
) -> str:
    """``templates/vhosts.conf.j2``, pre-rendered control-side.

    Jinja2 is imported *here*, not at module scope: it is not a reemote
    runtime dependency, so importing this module never requires it. It is in
    the project's ``dev`` group (the offline example tests render this
    template) and ``--with jinja2`` supplies it for a standalone run. The
    environment uses Ansible's whitespace defaults (``trim_blocks=True``,
    ``lstrip_blocks=False``, ``keep_trailing_newline=True``) and
    ``StrictUndefined`` so an unset variable fails loudly rather than rendering
    empty. ``ssl_exists`` is the frozen ``FileExists`` snapshot, aligned with
    ``apache_vhosts_ssl`` order, reshaped into the ``register:`` structure the
    template reads (``apache_ssl_certificates.results[i].stat.exists``).
    """
    import jinja2

    template_name = settings["vhosts_template"]
    # A custom ``apache_vhosts_template`` is a controller-side path, read here
    # in main()'s render step (never inside a deploy); the bundled template is
    # the role default and needs no filesystem read.
    template_text = (
        VHOSTS_TEMPLATE
        if template_name == "vhosts.conf.j2"
        else Path(template_name).read_text(encoding="utf-8")
    )
    environment = jinja2.Environment(
        trim_blocks=True,
        lstrip_blocks=False,
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
    )
    template = environment.from_string(template_text)
    return template.render(
        apache_global_vhost_settings=settings["global_vhost_settings"],
        apache_vhosts=settings["vhosts"],
        apache_vhosts_ssl=settings["vhosts_ssl"],
        apache_listen_ip=settings["listen_ip"],
        apache_listen_port=settings["listen_port"],
        apache_listen_port_ssl=settings["listen_port_ssl"],
        apache_allow_override=settings["allow_override"],
        apache_options=settings["options"],
        apache_vhosts_version=vhosts_version,
        apache_ignore_missing_ssl_certificate=settings["ignore_missing_ssl_certificate"],
        apache_ssl_certificates={
            "results": [{"stat": {"exists": exists}} for exists in ssl_exists]
        },
        apache_ssl_cipher_suite=settings["ssl_cipher_suite"],
        apache_ssl_protocol=settings["ssl_protocol"],
    )


def configure_plan(
    settings: dict[str, Any], version: str, ssl_exists: tuple[bool, ...]
) -> dict[str, Any]:
    """Render the vhosts file and freeze every read the configure deploy needs.

    Control-side work, performed once in ``main``: the probed ``version``
    selects the 2.2/2.4 variable table (the role's two ``include_vars`` tasks),
    the ports ``lineinfile`` items become stable pure transforms, and the
    Jinja2 template becomes a plain ``str`` (hashable, equal across phases).
    """
    minor = version.split(".")[1] if "." in version else ""
    if minor == "2":
        table = APACHE_22_VARS
    elif minor == "4":
        table = APACHE_24_VARS
    else:
        raise ValueError(
            f"unsupported Apache version {version!r}: expected 2.2.x or 2.4.x"
        )
    vhosts_version = table["vhosts_version"]
    return {
        "server_root": DEBIAN_VARS["server_root"],
        "conf_path": DEBIAN_VARS["conf_path"],
        "ports": tuple(
            (regexp, line, lineinfile_transform(regexp, line))
            for regexp, line in ports_items(settings, vhosts_version)
        ),
        "mods_enabled": settings["mods_enabled"],
        "mods_disabled": settings["mods_disabled"],
        "conf_enabled": settings["conf_enabled"],
        "conf_disabled": settings["conf_disabled"],
        "create_vhosts": settings["create_vhosts"],
        "vhosts_filename": settings["vhosts_filename"],
        "vhosts_content": render_vhosts(settings, vhosts_version, ssl_exists),
        "remove_default_vhost": settings["remove_default_vhost"],
        "default_vhost_filename": table["default_vhost_filename"],
        "service": DEBIAN_VARS["service"],
        "state": settings["state"],
        "enabled": settings["enabled"],
        "restart_state": settings["restart_state"],
    }


def notifying_names(plan: dict[str, Any]) -> tuple[str, ...]:
    """Every configure request name that queues ``restart apache``.

    Enumerated from the same frozen plan the deploy was built from, so the
    notification set matches the requests exactly. The final service task
    carries no ``notify`` in the role, so its name is deliberately absent.
    """
    names = ["Set Apache ports.conf mode"]
    names.extend(f"Configure Apache ports ({regexp})" for regexp, _line, _t in plan["ports"])
    names.extend(f"Enable Apache mod {mod}" for mod in plan["mods_enabled"])
    names.extend(f"Disable Apache mod {mod}" for mod in plan["mods_disabled"])
    names.extend(f"Enable Apache config {conf}" for conf in plan["conf_enabled"])
    names.extend(f"Disable Apache config {conf}" for conf in plan["conf_disabled"])
    if plan["create_vhosts"]:
        names.append("Add apache vhosts configuration")
        names.append("Add vhost symlink in sites-enabled")
    if plan["remove_default_vhost"]:
        names.append("Remove default vhost in sites-enabled")
    return tuple(names)


# --- deploys -----------------------------------------------------------------


def deploy_install(host: Host) -> Generator[OperationRequest]:
    """``tasks/setup-Debian.yml``: the apt cache refresh and the packages.

    Branch-free with respect to facts: the package list and the
    present/latest/absent flag come from static ``host.extra``. The cache
    refresh is ``apt.update(cache_valid_time=3600)`` -- idempotent within the
    window via the ``AptUpdateStamp`` fact, so a converged re-run reports no
    change (Ansible's ``update_cache=yes cache_valid_time=3600``). Neither
    task notifies in the role.
    """
    settings = role_settings(host)
    yield op(
        apt.update,
        name="Update apt cache",
        cache_valid_time=CACHE_VALID_TIME,
        sudo=True,
        when=is_ubuntu,
    )
    yield op(
        apt.packages,
        name="Ensure Apache is installed on Debian",
        packages=settings["packages"],
        present=settings["packages_state"] != "absent",
        latest=settings["packages_state"] == "latest",
        sudo=True,
        when=is_ubuntu,
    )


def build_configure(
    plans: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """``tasks/configure-Debian.yml`` plus the final service task.

    Every read the role takes mid-play (the version probe, the certificate
    ``stat``) was frozen into the plan control-side, so this deploy is a pure
    function of static input. Each notifying request carries a stable
    ``name=`` enumerated by ``notifying_names``.
    """

    def configure(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return
        server_root = plan["server_root"]
        conf_path = plan["conf_path"]
        ports_conf = f"{server_root}/ports.conf"

        # lineinfile ports.conf (with_items apache_ports_configuration_items).
        # files.transform owns the content edit; the paired files.file
        # converges the mode:0644 the lineinfile task sets, because transform
        # idempotence is content-only (a mode-only drift would not re-install).
        for regexp, _line, transform in plan["ports"]:
            yield op(
                files.transform,
                name=f"Configure Apache ports ({regexp})",
                path=ports_conf,
                transform=transform,
                mode="0644",
                sudo=True,
                when=is_ubuntu,
            )
        yield op(
            files.file,
            name="Set Apache ports.conf mode",
            path=ports_conf,
            mode="0644",
            sudo=True,
            when=is_ubuntu,
        )

        # Enable/disable mods and conf via symlinks. Deviation: the role's
        # mode:0644 on a symlink is dropped -- files.link owns no mode, and a
        # Linux symlink's own mode is always 0777 and meaningless (Ansible
        # cannot set it either). Removals use force=True so a regular file at
        # the path is removed too, not just a symlink.
        for mod in plan["mods_enabled"]:
            yield op(
                files.link,
                name=f"Enable Apache mod {mod}",
                path=f"{server_root}/mods-enabled/{mod}.load",
                target=f"{server_root}/mods-available/{mod}.load",
                sudo=True,
                when=is_ubuntu,
            )
        for mod in plan["mods_disabled"]:
            yield op(
                files.link,
                name=f"Disable Apache mod {mod}",
                path=f"{server_root}/mods-enabled/{mod}.load",
                present=False,
                force=True,
                sudo=True,
                when=is_ubuntu,
            )
        for conf in plan["conf_enabled"]:
            yield op(
                files.link,
                name=f"Enable Apache config {conf}",
                path=f"{server_root}/conf-enabled/{conf}.conf",
                target=f"{server_root}/conf-available/{conf}.conf",
                sudo=True,
                when=is_ubuntu,
            )
        for conf in plan["conf_disabled"]:
            yield op(
                files.link,
                name=f"Disable Apache config {conf}",
                path=f"{server_root}/conf-enabled/{conf}.conf",
                present=False,
                force=True,
                sudo=True,
                when=is_ubuntu,
            )

        # The vhosts template, pre-rendered control-side, and its sites-enabled
        # symlink. The role's force={{ ansible_check_mode }} is False in a real
        # run, so the symlink uses the default force=False (check mode has no
        # analogue -- a dry run is run(..., dry=True)).
        if plan["create_vhosts"]:
            yield op(
                files.install,
                name="Add apache vhosts configuration",
                content=plan["vhosts_content"],
                dest=f"{conf_path}/sites-available/{plan['vhosts_filename']}",
                mode="0644",
                user="root",
                group="root",
                sudo=True,
                when=is_ubuntu,
            )
            yield op(
                files.link,
                name="Add vhost symlink in sites-enabled",
                path=f"{conf_path}/sites-enabled/{plan['vhosts_filename']}",
                target=f"{conf_path}/sites-available/{plan['vhosts_filename']}",
                sudo=True,
                when=is_ubuntu,
            )
        if plan["remove_default_vhost"]:
            yield op(
                files.link,
                name="Remove default vhost in sites-enabled",
                path=f"{conf_path}/sites-enabled/{plan['default_vhost_filename']}",
                present=False,
                force=True,
                sudo=True,
                when=is_ubuntu,
            )

        # The final service task (no notify). apache_state maps to the
        # systemd.service verbs; restarted/reloaded are stateless (they always
        # report changed), faithful to Ansible's service module -- the default
        # started is fact-gated and converges.
        state = plan["state"]
        yield op(
            systemd.service,
            name="Ensure Apache has selected state and enabled on boot",
            service=plan["service"],
            running=state != "stopped",
            restarted=state == "restarted",
            reloaded=state == "reloaded",
            enabled=plan["enabled"],
            sudo=True,
            when=is_ubuntu,
        )

    return configure


def build_handler(
    plans: dict[str, Any], notified: frozenset[str]
) -> Callable[[Host], Generator[OperationRequest]]:
    """``handlers/main.yml``: ``restart apache`` with ``state={{ apache_restart_state }}``.

    The notified hosts are partitioned by each host's ``apache_restart_state``
    and each partition gets a ``service_handler`` with the matching verb, so a
    host asking for ``reloaded`` is reloaded while one asking for ``restarted``
    is restarted. An empty notification set yields an empty deploy -- the
    converged no-op Ansible's handler gives for free.
    """
    by_action: dict[str, list[str]] = {}
    for name in sorted(notified):
        action = plans[name]["restart_state"]
        action = "running" if action == "started" else action
        by_action.setdefault(action, []).append(name)
    return compose(
        *(
            service_handler(
                frozenset(names),
                (DEBIAN_VARS["service"],),
                action=action,
                when=is_ubuntu,
            )
            for action, names in sorted(by_action.items())
        )
    )


async def execute(inventory: list[dict[str, Any]], deploy: Any, title: str) -> Any:
    """Dry-run then really run one deploy, printing both renderings.

    The dry run is Ansible's ``--check`` as a return value: it stops after
    prepare, so the plan shows what *would* change without touching a host.
    Both runs use the very same deploy object -- for a factory-built deploy
    that means the same frozen snapshot and the same identity-stable
    transforms, which is what makes the determinism guard hold.
    """
    plans, _dry, _hosts = await run(inventory, deploy, dry=True)
    print(f"\n== {title} -- plan (prepare phase, --dry) ==")
    print(format_plan(plans))
    _plans, report, _hosts = await run(inventory, deploy)
    print(f"\n== {title} -- run log (apply phase) ==")
    assert report is not None
    print(format_log(report))
    return report


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        hosts = [Host.from_inventory(entry) for entry in inventory]
        targets = [host for host in hosts if is_ubuntu(host)]
        skipped = [host.name for host in hosts if not is_ubuntu(host)]
        if skipped:
            print("\n== Skipped (when guard is_ubuntu): " + ", ".join(skipped) + " ==")
        if not targets:
            print("\nNo host in the Ubuntu family; nothing to deploy.")
            return

        # The role's variable resolution and its implicit gates, before any
        # host is contacted.
        settings = {host.name: role_settings(host) for host in targets}

        # Deploy 1: the apt cache refresh and the apache2 packages.
        await execute(inventory, deploy_install, "Deploy 1: install Apache")

        # The version probe, AFTER the install apply so the binary exists.
        # gather_action closes its own hosts. On the real transport this is a
        # plain SSH command; offline, tests/test_roles_ansible_role_apache.py
        # seeds the answer in SimulatedHost.shell_results (the #16 action seam).
        probe = f"{DEBIAN_VARS['daemon_path']}{DEBIAN_VARS['daemon']} -v"
        results = await gather_action(targets, Shell, command=probe)
        versions = {}
        for host in targets:
            # gather_action returns a Result TypedDict -- a plain dict, so
            # the fields are read by key, not attribute.
            result = results[host.name]
            if result["exit_status"] != 0:
                raise ValueError(
                    f"{host.name}: Apache version probe failed "
                    f"(exit {result['exit_status']}): {result['stderr'].strip()}"
                )
            versions[host.name] = parse_apache_version(result["stdout"])
        print("\n== Probed Apache versions ==")
        for name, version in versions.items():
            print(f"{name}: {version}")

        # The certificate existence reads the vhosts template consumes. The
        # role's stat loop runs one item per SSL vhost (none by default), so
        # the gather only happens when there are SSL vhosts. gather_fact does
        # not close its hosts, so the finally does.
        ssl_exists: dict[str, tuple[bool, ...]] = {}
        try:
            for host in targets:
                paths = [
                    vhost["certificate_file"]
                    for vhost in settings[host.name]["vhosts_ssl"]
                ]
                exists = []
                for path in paths:
                    gathered = await gather_fact([host], FileExists, path)
                    exists.append(bool(gathered[host.name]))
                ssl_exists[host.name] = tuple(exists)
        finally:
            for host in targets:
                await host.aclose()

        # Freeze the configure plan: select the version table, build the ports
        # transforms, render the vhosts file control-side.
        plans = {
            host.name: configure_plan(
                settings[host.name], versions[host.name], ssl_exists[host.name]
            )
            for host in targets
        }
        configure = build_configure(plans)
        configure_report = await execute(
            inventory, configure, "Deploy 2: configure Apache"
        )

        # The handler. Every notifying request name is enumerated from the
        # same frozen plans the deploy was built from; changed_hosts reads the
        # configure apply report (the only notifying deploy).
        names = tuple(
            sorted({name for plan in plans.values() for name in notifying_names(plan)})
        )
        notified = changed_hosts(configure_report, names)
        print("\n== Handler notification set (restart apache) ==")
        print(sorted(notified) or "(none)")
        handlers = build_handler(plans, notified)
        handler_report = await execute(inventory, handlers, "Deploy 3: restart handler")

        changed = sum(
            1
            for report in (configure_report, handler_report)
            for entry in report.log
            if entry.changed
        )
        print(f"\n== Done: {changed} changed request(s) in configure + handler ==")
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        # The inventory is missing/unreadable/not JSON, a role variable is
        # invalid, or the version probe could not be parsed. No host mutation
        # is implied by the variable gates, so the message is the whole report.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema; the
        # message names the offending entry by its list index.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # Every host-level failure is a typed error naming the host and the
        # failing command. Print that, no traceback, and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
