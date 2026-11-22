# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Migration of the nginx Ansible role (``geerlingguy.nginx``) to reemote.

The role at ``https://github.com/geerlingguy/ansible-role-nginx`` installs and
configures nginx on RedHat/CentOS, Debian/Ubuntu, Archlinux, Suse, FreeBSD and
OpenBSD. This program is its **Ubuntu default path** expressed as a reemote
deploy: the ``dirmngr`` prerequisite, the optional official nginx PPA (with the
reinstall-on-PPA-add split), the apt cache refresh and the ``nginx`` package,
the ``sites-enabled`` vhost files rendered from the role's ``vhost.j2``, the
optional default-vhost removal, the legacy ``vhosts.conf`` cleanup, the
rendered ``nginx.conf`` installed behind ``nginx -t`` validation, the service's
running/enabled state, and the role's ``restart nginx`` / ``reload nginx``
handlers. Every role variable is read from the inventory entry's extra keys
(reemote's ``host_vars`` mechanism), so ``nginx_vhosts`` and friends live
beside ``host``/``connection`` in ``inventory.json``.

Target: hosts in the ``"Ubuntu"`` OS family. Every request carries
``when=is_ubuntu``, and a skipped request is never materialized -- the other
hosts in the shared inventory are not even contacted. The role's RedHat, Suse,
Archlinux, FreeBSD and OpenBSD branches are out of scope (a Phase 0 single-OS
decision), so the ``include_vars`` OS dispatch collapses to frozen Debian
constants below. Both Ubuntu opt-in toggles (``nginx_ppa_use``,
``nginx_remove_default_vhost``) ARE implemented: unlike a build-from-source
branch, every task they gate maps to a covered operation.

Why the program is a *sequence* of deploys rather than one
-------------------------------------------------------

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role
has two mutable reads a single deploy may not branch on, and each becomes a
**gather -> snapshot -> factory** step between complete runs:

1. ``deploy_setup``       -- dirmngr + the optional ``ppa:nginx/<version>``
2. ``changed_hosts``      -- the role's ``register: nginx_ppa_added`` +
   ``when: nginx_ppa_added is changed``: the frozen set of hosts whose PPA
   request actually changed drives the reinstall removal in the next deploy
3. ``build_install``      -- the conditional nginx removal, the apt cache
   refresh (``cache_valid_time=86400``) and the nginx package
4. ``gather_fact(Cpus)``  -- the vCPU count the ``nginx.conf`` template's
   ``worker_processes`` default consumes (Ansible's
   ``ansible_facts.processor_vcpus``); read after the install apply
5. ``build_configure``    -- vhosts.yml + the validated ``nginx.conf`` +
   the final service state, all frozen control-side (Jinja2 renders here,
   never inside a deploy)
6. ``build_handlers``     -- ``changed_hosts`` over the configure report's
   two notifying name sets, composed in handler-definition order:
   ``service_handler(action="restarted")`` then
   ``service_handler(action="reloaded")`` (the reload handler's
   ``when: nginx_service_state == "started"`` is a static per-host filter
   applied to the frozen set)

Implementation notes
--------------------

No implementation gap blocks this migration: every task on the Ubuntu path
maps to a covered operation or a documented recipe (the validate hook, the
reload handler factory, ``apt.ppa`` idempotence, ``default_release`` and
``cache_valid_time`` all landed in the library before this migration). One
gap was identified in the *test framework*, not the library runtime: GAP-1
(#18, https://github.com/kimjarvis/reemote/issues/18) --
``SimulatedHost.remove_file`` cleared the stat seams but left the
``remote_files`` bytes, so ``FileContents`` answered stale content after a
simulated ``files.file(present=False)``. Real hosts were unaffected; the
fix has landed (``remove_file``/``remove_link`` now pop ``remote_files``
too, mirroring ``remove_tree``) and the second pass is done: the removal
assertions in ``tests/test_roles_ansible_role_nginx.py`` read
``remote_files`` directly. The deliberate deviations are recorded at the
point of use and in
``ansible-role-nginx.md``; the load-bearing one is ``files.file(present=False,
force=True)`` for the role's ``file: state=absent`` tasks -- reemote's ``File``
fact reads a symlink as "exists and is not a file", so the default
``force=False`` would raise on Ubuntu's symlinked default vhost; ``force=True``
reproduces Ansible's type-agnostic removal (``rm -rf`` on a symlink removes
only the link). The role's third handler, ``validate nginx configuration``,
is never notified by any task -- a dead handler, documented but not
implemented, exactly as Ansible never runs it.

Prerequisites: Jinja2 (``--with jinja2``) renders the role's ``nginx.conf.j2``
and ``vhost.j2`` templates; it is deliberately *not* a reemote dependency.

Run it with::

    uv run --with jinja2 python examples/roles/ansible-role-nginx/ansible-role-nginx.py
"""

import asyncio
import json
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    apt,
    changed_hosts,
    compose,
    files,
    format_log,
    format_plan,
    gather_fact,
    hardware,
    op,
    run,
    service_handler,
    systemd,
    validate_inventory,
)

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path only) ---

#: ``setup-Debian.yml``'s ``apt: update_cache=yes cache_valid_time=86400``.
CACHE_VALID_TIME = 86400

#: ``vars/Debian.yml`` -- the OS dispatch frozen to the single Ubuntu target.
DEBIAN_VARS = {
    "root_group": "root",
    "nginx_conf_path": "/etc/nginx/conf.d",
    "nginx_conf_file_path": "/etc/nginx/nginx.conf",
    "nginx_mime_file_path": "/etc/nginx/mime.types",
    "nginx_pidfile": "/run/nginx.pid",
    "nginx_vhost_path": "/etc/nginx/sites-enabled",
    "nginx_default_vhost_path": "/etc/nginx/sites-enabled/default",
    "__nginx_user": "www-data",
}

#: ``templates/nginx.conf.j2``, verbatim. Rendered control-side in
#: ``configure_plan`` (reemote has no template engine and no controller
#: filesystem paths inside a deploy). Only built-in Jinja2 filters are used
#: (``indent``), so a plain ``Environment`` with Ansible's whitespace defaults
#: reproduces the role's output. The ``{% block %}`` tags are kept so a custom
#: ``nginx_conf_template`` can ``{% extends "nginx.conf.j2" %}`` it, exactly as
#: the role README documents.
NGINX_CONF_TEMPLATE = """user  {{ nginx_user }};

error_log  {{ nginx_error_log }};
pid        {{ nginx_pidfile }};

{% block worker %}
worker_processes  {{ nginx_worker_processes }};
{% endblock %}

{% if nginx_extra_conf_options %}
{{ nginx_extra_conf_options }}
{% endif %}

{% block events %}
events {
    worker_connections  {{ nginx_worker_connections }};
    multi_accept {{ nginx_multi_accept }};
}
{% endblock %}

http {
    {% block http_begin %}{% endblock %}

{% block http_basic %}
    include       {{ nginx_mime_file_path }};
    default_type  application/octet-stream;

    server_names_hash_bucket_size {{ nginx_server_names_hash_bucket_size }};

    client_max_body_size {{ nginx_client_max_body_size }};

    log_format  main  {{ nginx_log_format|indent(23) }};

    access_log  {{ nginx_access_log }};

    sendfile        {{ nginx_sendfile }};
    tcp_nopush      {{ nginx_tcp_nopush }};
    tcp_nodelay     {{ nginx_tcp_nodelay }};

    keepalive_timeout  {{ nginx_keepalive_timeout }};
    keepalive_requests {{ nginx_keepalive_requests }};

    server_tokens {{ nginx_server_tokens }};
{% if nginx_proxy_cache_path %}
    proxy_cache_path {{ nginx_proxy_cache_path }};
{% endif %}
{% endblock %}

{% block http_gzip %}
    # gzip on;
{% endblock %}

{% if nginx_extra_http_options %}
    {{ nginx_extra_http_options|indent(4, False) }}
{% endif %}

{% block http_upstream %}
{% for upstream in nginx_upstreams %}
    upstream {{ upstream.name }} {
{% if upstream.strategy is defined %}
        {{ upstream.strategy }};
{% endif %}
{% for server in upstream.servers %}
        server {{ server }};
{% endfor %}
{% if upstream.keepalive is defined %}
        keepalive {{ upstream.keepalive }};
{% endif %}
    }
{% endfor %}
{% endblock %}

{% block http_includes %}
    include {{ nginx_conf_path }}/*.conf;
{% if nginx_conf_path != nginx_vhost_path %}
    include {{ nginx_vhost_path }}/*;
{% endif %}
{% endblock %}

    {% block http_end %}{% endblock %}
}
"""

#: ``templates/vhost.j2``, verbatim; rendered once per present vhost.
VHOST_TEMPLATE = """{% block server_redirect %}
{% if item.server_name_redirect is defined %}
server {
    listen       {{ item.listen | default('80') }};
{% if nginx_listen_ipv6 %}
    listen       [::]:{{item.listen | default('80') }};
{% endif %}
    server_name  {{ item.server_name_redirect }};
    return       301 $scheme://{{ item.server_name.split(' ')[0] }}$request_uri;
}
{% endif %}
{% endblock %}

server {
    {% block server_begin %}{% endblock %}

    {% block server_basic -%}
    listen {{ item.listen | default('80') }};
{% if nginx_listen_ipv6 %}
    listen       [::]:{{item.listen | default('80') }};
{% endif %}

{% if item.server_name is defined %}
    server_name {{ item.server_name }};
{% endif %}

{% if item.root is defined %}
    root {{ item.root }};
{% endif %}

    index {{ item.index | default('index.html index.htm') }};

{% if item.error_page is defined %}
    error_page {{ item.error_page }};
{% endif %}
{% if item.access_log is defined %}
    access_log {{ item.access_log }};
{% endif %}
{% if item.error_log is defined %}
    error_log {{ item.error_log }} error;
{% endif %}

{% if item.return is defined %}
    return {{ item.return }};
{% endif %}
    {% endblock %}

    {% block server_end %}{% endblock %}

{% if item.extra_parameters is defined %}
    {{ item.extra_parameters|indent(4) }}
{% endif %}
}
"""

#: The role defaults this migration honours (``defaults/main.yml``). The
#: ``nginx_`` prefix is kept so an inventory entry reads like the role's
#: ``host_vars``. Two sentinels: ``nginx_user`` None reproduces the
#: ``set_fact: nginx_user={{ __nginx_user }}`` fallback, and
#: ``nginx_worker_processes`` None reproduces the fact-fed default
#: ``"{{ ansible_facts.processor_vcpus | default(ansible_facts.processor_count) }}"``
#: (the Cpus gather supplies the count, quotes included -- the role's folded
#: scalar carries literal double quotes around the rendered number).
ROLE_DEFAULTS: dict[str, Any] = {
    "nginx_default_release": "",
    "nginx_ppa_use": False,
    "nginx_ppa_version": "stable",
    "nginx_package_name": "nginx",
    "nginx_service_state": "started",
    "nginx_service_enabled": True,
    "nginx_conf_template": "nginx.conf.j2",
    "nginx_vhost_template": "vhost.j2",
    "nginx_user": None,
    "nginx_worker_processes": None,
    "nginx_worker_connections": "1024",
    "nginx_multi_accept": "off",
    "nginx_error_log": "/var/log/nginx/error.log warn",
    "nginx_access_log": "/var/log/nginx/access.log main buffer=16k flush=2m",
    "nginx_sendfile": "on",
    "nginx_tcp_nopush": "on",
    "nginx_tcp_nodelay": "on",
    "nginx_keepalive_timeout": "75",
    "nginx_keepalive_requests": "600",
    "nginx_server_tokens": "on",
    "nginx_client_max_body_size": "64m",
    "nginx_server_names_hash_bucket_size": "64",
    "nginx_proxy_cache_path": "",
    "nginx_extra_conf_options": "",
    "nginx_extra_http_options": "",
    "nginx_remove_default_vhost": False,
    "nginx_listen_ipv6": True,
    "nginx_vhosts": [],
    "nginx_upstreams": [],
    "nginx_log_format": (
        "'$remote_addr - $remote_user [$time_local] \"$request\" '\n"
        "'$status $body_bytes_sent \"$http_referer\" '\n"
        "'\"$http_user_agent\" \"$http_x_forwarded_for\"'"
    ),
    # Accepted for parity, inert: RedHat/Suse-only knobs (scoped out).
    "nginx_yum_repo_enabled": True,
    "nginx_zypper_repo_enabled": True,
}

#: The PPA request's stable name -- the ``register: nginx_ppa_added`` channel.
PPA_NAME = "Add PPA for Nginx"


# --- pure helpers ------------------------------------------------------------



def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data and stays a stable module-level
    function, so both deploy phases reach the same eligibility decision.
    A skipped request is never materialized, so the Alpine and CentOS
    hosts in the shared inventory are not contacted at all.
    """
    return host.family == "Ubuntu"


def vhost_filename(vhost: dict[str, Any]) -> str:
    """The role's ``item.filename|default(item.server_name.split(' ')[0] ~ '.conf')``.

    Computed control-side (the dest of a vhost install/removal), so the
    eager-evaluation trap of Jinja2's ``default()`` -- which would evaluate
    the ``server_name`` fallback even when ``filename`` is set -- never fires.
    """
    filename = vhost.get("filename")
    if filename:
        return str(filename)
    server_name = vhost.get("server_name")
    if not server_name:
        raise ValueError("a vhost needs a server_name or a filename")
    return str(server_name).split(" ")[0] + ".conf"


def _vhost_list(value: Any, host: Host) -> tuple[dict[str, Any], ...]:
    """Validate ``nginx_vhosts`` and freeze it to a tuple of mappings.

    A type gate raises ``TypeError`` (ruff TRY004), a vhost with neither
    ``server_name`` nor ``filename`` raises ``ValueError`` -- both in
    ``main()`` before any host is contacted. The ``state`` field is not
    enum-gated: the role only tests ``!= 'absent'``, so any other spelling
    installs, faithfully.
    """
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{host.name}: nginx_vhosts must be a list of mappings")
    vhosts = []
    for index, vhost in enumerate(value):
        if not isinstance(vhost, dict):
            raise TypeError(f"{host.name}: nginx_vhosts[{index}] must be a mapping")
        if not vhost.get("server_name") and not vhost.get("filename"):
            raise ValueError(
                f"{host.name}: nginx_vhosts[{index}] needs a server_name or filename"
            )
        vhosts.append(vhost)
    return tuple(vhosts)


def _upstream_list(value: Any, host: Host) -> tuple[dict[str, Any], ...]:
    """Validate ``nginx_upstreams`` and freeze it to a tuple of mappings."""
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{host.name}: nginx_upstreams must be a list of mappings")
    upstreams = []
    for index, upstream in enumerate(value):
        if not isinstance(upstream, dict):
            raise TypeError(f"{host.name}: nginx_upstreams[{index}] must be a mapping")
        if not upstream.get("name"):
            raise ValueError(f"{host.name}: nginx_upstreams[{index}] needs a name")
        servers = upstream.get("servers")
        if not isinstance(servers, (list, tuple)) or not servers:
            raise ValueError(
                f"{host.name}: nginx_upstreams[{index}] needs a non-empty servers list"
            )
        upstreams.append(upstream)
    return tuple(upstreams)


def role_settings(host: Host) -> dict[str, Any]:
    """Freeze one host's role variables: defaults overlaid with ``host.extra``.

    Reads only static inventory data, so a deploy body may call it and branch
    on the result -- both phases see the same dict. The role's variable
    resolution (``nginx_user`` falling back to ``__nginx_user``) and its
    implicit type/enum gates happen here, reproducing ``defaults/main.yml``
    plus the ``set_fact`` in ``tasks/main.yml``.
    """
    merged = {**ROLE_DEFAULTS, **host.extra}
    state = str(merged["nginx_service_state"])
    if state not in ("started", "stopped", "restarted", "reloaded"):
        raise ValueError(
            f"{host.name}: nginx_service_state must be started/stopped/restarted/reloaded, "
            f"got {state!r}"
        )
    user = merged["nginx_user"]
    if user is None:
        user = DEBIAN_VARS["__nginx_user"]
    worker_processes = merged["nginx_worker_processes"]
    return {
        "default_release": str(merged["nginx_default_release"]),
        "ppa_use": bool(merged["nginx_ppa_use"]),
        "ppa_version": str(merged["nginx_ppa_version"]),
        "package_name": str(merged["nginx_package_name"]),
        "service_state": state,
        "service_enabled": bool(merged["nginx_service_enabled"]),
        "conf_template": str(merged["nginx_conf_template"]),
        "vhost_template": str(merged["nginx_vhost_template"]),
        "user": str(user),
        "worker_processes": None if worker_processes is None else str(worker_processes),
        "worker_connections": str(merged["nginx_worker_connections"]),
        "multi_accept": str(merged["nginx_multi_accept"]),
        "error_log": str(merged["nginx_error_log"]),
        "access_log": str(merged["nginx_access_log"]),
        "sendfile": str(merged["nginx_sendfile"]),
        "tcp_nopush": str(merged["nginx_tcp_nopush"]),
        "tcp_nodelay": str(merged["nginx_tcp_nodelay"]),
        "keepalive_timeout": str(merged["nginx_keepalive_timeout"]),
        "keepalive_requests": str(merged["nginx_keepalive_requests"]),
        "server_tokens": str(merged["nginx_server_tokens"]),
        "client_max_body_size": str(merged["nginx_client_max_body_size"]),
        "server_names_hash_bucket_size": str(merged["nginx_server_names_hash_bucket_size"]),
        "proxy_cache_path": str(merged["nginx_proxy_cache_path"]),
        "extra_conf_options": str(merged["nginx_extra_conf_options"]),
        "extra_http_options": str(merged["nginx_extra_http_options"]),
        "remove_default_vhost": bool(merged["nginx_remove_default_vhost"]),
        "listen_ipv6": bool(merged["nginx_listen_ipv6"]),
        "vhosts": _vhost_list(merged["nginx_vhosts"], host),
        "upstreams": _upstream_list(merged["nginx_upstreams"], host),
        "log_format": str(merged["nginx_log_format"]),
    }


def _template_environment() -> Any:
    """A Jinja2 environment with Ansible's whitespace defaults.

    Jinja2 is imported *here*, not at module scope: it is not a reemote
    runtime dependency, so importing this module never requires it. It is in
    the project's ``dev`` group (the offline example tests render these
    templates) and ``--with jinja2`` supplies it for a standalone run. The
    ``DictLoader`` maps the bundled template names so a custom template can
    ``{% extends "nginx.conf.j2" %}`` / ``{% extends "vhost.j2" %}`` -- the
    inheritance the role README documents. ``StrictUndefined`` makes an unset
    variable fail loudly rather than render empty.
    """
    import jinja2

    return jinja2.Environment(
        loader=jinja2.DictLoader(
            {"nginx.conf.j2": NGINX_CONF_TEMPLATE, "vhost.j2": VHOST_TEMPLATE}
        ),
        trim_blocks=True,
        lstrip_blocks=False,
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
    )


def _load_template(environment: Any, name: str) -> Any:
    """The bundled template by name, or a controller-side path read here.

    A custom ``nginx_conf_template`` / ``nginx_vhost_template`` / per-vhost
    ``template`` is a control-machine path, read in main()'s render step
    (never inside a deploy); the bundled names need no filesystem read.
    """
    if name in ("nginx.conf.j2", "vhost.j2"):
        return environment.get_template(name)
    return environment.from_string(Path(name).read_text(encoding="utf-8"))


def render_nginx_conf(settings: dict[str, Any], worker_processes: str) -> str:
    """``templates/nginx.conf.j2``, pre-rendered control-side.

    ``worker_processes`` is already the final spelling: the fact-fed default
    arrives quoted (``"4"`` -- the role's folded scalar carries literal double
    quotes), an explicit ``nginx_worker_processes`` override arrives verbatim.
    """
    environment = _template_environment()
    template = _load_template(environment, settings["conf_template"])
    return template.render(
        nginx_user=settings["user"],
        nginx_error_log=settings["error_log"],
        nginx_pidfile=DEBIAN_VARS["nginx_pidfile"],
        nginx_worker_processes=worker_processes,
        nginx_extra_conf_options=settings["extra_conf_options"],
        nginx_worker_connections=settings["worker_connections"],
        nginx_multi_accept=settings["multi_accept"],
        nginx_mime_file_path=DEBIAN_VARS["nginx_mime_file_path"],
        nginx_server_names_hash_bucket_size=settings["server_names_hash_bucket_size"],
        nginx_client_max_body_size=settings["client_max_body_size"],
        nginx_log_format=settings["log_format"],
        nginx_access_log=settings["access_log"],
        nginx_sendfile=settings["sendfile"],
        nginx_tcp_nopush=settings["tcp_nopush"],
        nginx_tcp_nodelay=settings["tcp_nodelay"],
        nginx_keepalive_timeout=settings["keepalive_timeout"],
        nginx_keepalive_requests=settings["keepalive_requests"],
        nginx_server_tokens=settings["server_tokens"],
        nginx_proxy_cache_path=settings["proxy_cache_path"],
        nginx_extra_http_options=settings["extra_http_options"],
        nginx_upstreams=settings["upstreams"],
        nginx_conf_path=DEBIAN_VARS["nginx_conf_path"],
        nginx_vhost_path=DEBIAN_VARS["nginx_vhost_path"],
    )


def render_vhost(settings: dict[str, Any], vhost: dict[str, Any]) -> str:
    """``templates/vhost.j2`` (or the vhost's own ``template``), pre-rendered."""
    environment = _template_environment()
    template = _load_template(
        environment, str(vhost.get("template") or settings["vhost_template"])
    )
    return template.render(item=vhost, nginx_listen_ipv6=settings["listen_ipv6"])


def configure_plan(settings: dict[str, Any], cpus: int) -> dict[str, Any]:
    """Render every config file and freeze the reads the configure deploy needs.

    Control-side work, performed once in ``main``: the gathered ``cpus`` count
    feeds the ``worker_processes`` default (the role's
    ``ansible_facts.processor_vcpus``), each present vhost becomes a
    ``(filename, rendered content)`` pair and each absent vhost a filename,
    and ``nginx.conf`` becomes a plain ``str`` (hashable, equal across phases).
    """
    worker_processes = settings["worker_processes"]
    if worker_processes is None:
        # The role's default is a folded scalar WITH literal double quotes:
        # worker_processes  "4";  -- nginx accepts the quoted number.
        worker_processes = f'"{cpus}"'
    vhosts_install = []
    vhosts_remove = []
    for vhost in settings["vhosts"]:
        filename = vhost_filename(vhost)
        if vhost.get("state", "present") == "absent":
            vhosts_remove.append(filename)
        else:
            vhosts_install.append((filename, render_vhost(settings, vhost)))
    return {
        "remove_default_vhost": settings["remove_default_vhost"],
        "default_vhost_path": DEBIAN_VARS["nginx_default_vhost_path"],
        "vhost_path": DEBIAN_VARS["nginx_vhost_path"],
        "vhosts_install": tuple(vhosts_install),
        "vhosts_remove": tuple(vhosts_remove),
        "conf_content": render_nginx_conf(settings, worker_processes),
        "conf_file_path": DEBIAN_VARS["nginx_conf_file_path"],
        "state": settings["service_state"],
        "enabled": settings["service_enabled"],
    }


def restart_notifying_names(plan: dict[str, Any]) -> tuple[str, ...]:
    """Every configure request name that queues ``restart nginx``.

    Only the default-vhost removal notifies restart in the role; the name is
    present exactly when the frozen plan carries the removal.
    """
    if plan["remove_default_vhost"]:
        return ("Remove default nginx vhost config file",)
    return ()


def reload_notifying_names(plan: dict[str, Any]) -> tuple[str, ...]:
    """Every configure request name that queues ``reload nginx``.

    Enumerated from the same frozen plan the deploy was built from, so the
    notification set matches the requests exactly. The final service task
    carries no ``notify`` in the role, so its name is deliberately absent.
    """
    names = ["Ensure nginx_vhost_path exists"]
    names.extend(
        f"Add managed vhost config file {filename}"
        for filename, _content in plan["vhosts_install"]
    )
    names.extend(
        f"Remove managed vhost config file {filename}" for filename in plan["vhosts_remove"]
    )
    names.append("Remove legacy vhosts.conf file")
    names.append("Copy nginx configuration in place")
    return tuple(names)


# --- deploys -----------------------------------------------------------------


def deploy_setup(host: Host) -> Generator[OperationRequest]:
    """``tasks/setup-Ubuntu.yml``: dirmngr and the optional nginx PPA.

    The PPA branch reads only static ``host.extra`` (``nginx_ppa_use``), so
    both deploy phases yield the same sequence. ``apt.ppa`` is idempotent
    (it reads ``AptSources`` for the PPA's Launchpad archive) and reports
    ``changed`` only on a real add -- which is what makes the role's
    ``register: nginx_ppa_added`` + ``when: nginx_ppa_added is changed``
    reinstall split sound. ``update_cache=True`` refreshes the index on the
    add, exactly as the role's ``apt_repository`` task does.
    """
    settings = role_settings(host)
    yield op(
        apt.packages,
        name="Ensure dirmngr is installed",
        packages=("dirmngr",),
        sudo=True,
        when=is_ubuntu,
    )
    if settings["ppa_use"]:
        yield op(
            apt.ppa,
            name=PPA_NAME,
            src=f"ppa:nginx/{settings['ppa_version']}",
            update_cache=True,
            sudo=True,
            when=is_ubuntu,
        )


def build_install(
    ppa_added: frozenset[str],
) -> Callable[[Host], Generator[OperationRequest]]:
    """``setup-Ubuntu.yml``'s reinstall task + ``tasks/setup-Debian.yml``.

    ``ppa_added`` is the frozen ``changed_hosts`` read of the setup apply
    report -- the role's ``when: nginx_ppa_added is changed`` moved into the
    gap between runs. On a host whose PPA was just added, nginx is removed
    first so the subsequent install picks up the PPA's package; the removal
    is fact-gated (an absent nginx is a no-op). The cache refresh is
    ``apt.update(cache_valid_time=86400)`` -- idempotent within the window
    via the ``AptUpdateStamp`` fact. Deviation: the role's
    ``changed_when: false`` on the cache task has no reemote analogue, but
    the task notifies nothing, so the log's ``changed`` flag has no
    downstream effect (the name is in no notifying set).
    """

    def install(host: Host) -> Generator[OperationRequest]:
        settings = role_settings(host)
        if host.name in ppa_added:
            yield op(
                apt.packages,
                name="Ensure nginx will reinstall if the PPA was just added",
                packages=(settings["package_name"],),
                present=False,
                sudo=True,
                when=is_ubuntu,
            )
        yield op(
            apt.update,
            name="Update apt cache",
            cache_valid_time=CACHE_VALID_TIME,
            sudo=True,
            when=is_ubuntu,
        )
        yield op(
            apt.packages,
            name="Ensure nginx is installed",
            packages=(settings["package_name"],),
            default_release=settings["default_release"] or None,
            sudo=True,
            when=is_ubuntu,
        )

    return install


def build_configure(
    plans: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """``tasks/vhosts.yml`` + the ``nginx.conf`` template + the service task.

    Every read the role takes mid-play (the fact-fed ``worker_processes``,
    the rendered templates) was frozen into the plan control-side, so this
    deploy is a pure function of static input. Each notifying request carries
    a stable ``name=`` enumerated by the two ``*_notifying_names`` helpers.

    Deviation: the role's ``file: state=absent`` tasks map to
    ``files.file(present=False, force=True)``. reemote's ``File`` fact reads a
    symlink as "exists and is not a file" and the default ``force=False``
    would raise -- Ubuntu's default vhost IS a symlink. ``force=True``
    reproduces Ansible's type-agnostic removal: ``rm -rf`` on a symlink
    removes only the link, never its target.
    """

    def configure(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return
        vhost_path = plan["vhost_path"]

        if plan["remove_default_vhost"]:
            yield op(
                files.file,
                name="Remove default nginx vhost config file",
                path=plan["default_vhost_path"],
                present=False,
                force=True,
                sudo=True,
                when=is_ubuntu,
            )
        yield op(
            files.directory,
            name="Ensure nginx_vhost_path exists",
            path=vhost_path,
            mode="0755",
            sudo=True,
            when=is_ubuntu,
        )
        for filename, content in plan["vhosts_install"]:
            yield op(
                files.install,
                name=f"Add managed vhost config file {filename}",
                content=content,
                dest=f"{vhost_path}/{filename}",
                mode="0644",
                user="root",
                group=DEBIAN_VARS["root_group"],
                sudo=True,
                when=is_ubuntu,
            )
        for filename in plan["vhosts_remove"]:
            yield op(
                files.file,
                name=f"Remove managed vhost config file {filename}",
                path=f"{vhost_path}/{filename}",
                present=False,
                force=True,
                sudo=True,
                when=is_ubuntu,
            )
        yield op(
            files.file,
            name="Remove legacy vhosts.conf file",
            path=f"{vhost_path}/vhosts.conf",
            present=False,
            force=True,
            sudo=True,
            when=is_ubuntu,
        )

        # The role's validate: nginx -t -c %s becomes the install's validate
        # hook: the rendered config is spooled, validated elevated, and only
        # installed when nginx -t exits 0 -- a rejected config never lands.
        yield op(
            files.install,
            name="Copy nginx configuration in place",
            content=plan["conf_content"],
            dest=plan["conf_file_path"],
            mode="0644",
            user="root",
            group=DEBIAN_VARS["root_group"],
            validate="nginx -t -c {spool}",
            sudo=True,
            when=is_ubuntu,
        )

        # The final service task (no notify). nginx_service_state maps to the
        # systemd.service verbs; restarted/reloaded are stateless (they always
        # report changed), faithful to Ansible's service module -- the default
        # started is fact-gated and converges.
        state = plan["state"]
        yield op(
            systemd.service,
            name="Ensure nginx service is running as configured",
            service="nginx",
            running=state != "stopped",
            restarted=state == "restarted",
            reloaded=state == "reloaded",
            enabled=plan["enabled"],
            sudo=True,
            when=is_ubuntu,
        )

    return configure


def build_handlers(
    plans: dict[str, Any],
    restart_notified: frozenset[str],
    reload_notified: frozenset[str],
) -> Callable[[Host], Generator[OperationRequest]]:
    """``handlers/main.yml``: ``restart nginx`` then ``reload nginx``.

    Ansible flushes handlers in DEFINITION order, so the composed deploy
    restarts first and reloads second; a host that notified both gets both
    (Ansible does not deduplicate across distinct handlers). The reload
    handler's ``when: nginx_service_state == "started"`` is static per-host
    data, so it filters the frozen notification set here. The third handler,
    ``validate nginx configuration``, is never notified by any task -- dead
    in the role, deliberately absent here. An empty notification set yields
    an empty deploy -- the converged no-op Ansible's handler gives for free.
    """
    reload_hosts = frozenset(
        name for name in reload_notified if plans[name]["state"] == "started"
    )
    return compose(
        service_handler(
            restart_notified,
            ("nginx",),
            action="restarted",
            when=is_ubuntu,
        ),
        service_handler(
            reload_hosts,
            ("nginx",),
            action="reloaded",
            when=is_ubuntu,
        ),
    )


async def execute(inventory: list[dict[str, Any]], deploy: Any, title: str) -> Any:
    """Dry-run then really run one deploy, printing both renderings.

    The dry run is Ansible's ``--check`` as a return value: it stops after
    prepare, so the plan shows what *would* change without touching a host.
    Both runs use the very same deploy object -- for a factory-built deploy
    that means the same frozen snapshot, which is what makes the determinism
    guard hold. A dry preview of an outcome-fed deploy (install, handlers)
    consults the frozen apply outcome of the run before it.
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

        # Deploy 1: dirmngr + the optional PPA.
        setup_report = await execute(
            inventory, deploy_setup, "Deploy 1: Ubuntu setup (dirmngr + PPA)"
        )

        # The register+when split: only hosts whose PPA request really
        # changed reinstall nginx in the next deploy.
        ppa_added = changed_hosts(setup_report, (PPA_NAME,))
        print("\n== PPA added on (reinstall split) ==")
        print(sorted(ppa_added) or "(none)")

        # Deploy 2: the conditional removal, the cache refresh, the package.
        install_report = await execute(
            inventory, build_install(ppa_added), "Deploy 2: install nginx"
        )

        # The vCPU count the worker_processes default consumes, AFTER the
        # install apply. gather_fact does not close its hosts, so the finally
        # does. Offline, tests/test_roles_ansible_role_nginx.py seeds the
        # answer in SimulatedHost.cpus.
        try:
            gathered = await gather_fact(targets, hardware.Cpus)
        finally:
            for host in targets:
                await host.aclose()
        cpus: dict[str, int] = {}
        for host in targets:
            count = gathered[host.name]
            if count is None:
                raise ValueError(
                    f"{host.name}: cannot determine the CPU count for "
                    "nginx_worker_processes"
                )
            cpus[host.name] = count
        print("\n== Probed CPU counts (worker_processes default) ==")
        for name, count in cpus.items():
            print(f"{name}: {count}")

        # Freeze the configure plan: render nginx.conf and every vhost
        # control-side, split the vhosts into installs and removals.
        plans = {
            host.name: configure_plan(settings[host.name], cpus[host.name])
            for host in targets
        }

        # Deploy 3: vhosts + nginx.conf + the service state.
        configure_report = await execute(
            inventory, build_configure(plans), "Deploy 3: configure nginx"
        )

        # The handlers. Every notifying request name is enumerated from the
        # same frozen plans the deploy was built from; changed_hosts reads
        # the configure apply report (the only notifying deploy).
        restart_names = tuple(
            sorted(
                {name for plan in plans.values() for name in restart_notifying_names(plan)}
            )
        )
        reload_names = tuple(
            sorted(
                {name for plan in plans.values() for name in reload_notifying_names(plan)}
            )
        )
        restart_notified = changed_hosts(configure_report, restart_names)
        reload_notified = changed_hosts(configure_report, reload_names)
        print("\n== Handler notification sets ==")
        print(f"restart nginx: {sorted(restart_notified) or '(none)'}")
        print(f"reload nginx:  {sorted(reload_notified) or '(none)'}")
        handler_report = await execute(
            inventory,
            build_handlers(plans, restart_notified, reload_notified),
            "Deploy 4: restart/reload handlers",
        )

        changed = sum(
            1
            for report in (setup_report, install_report, configure_report, handler_report)
            for entry in report.log
            if entry.changed
        )
        print(f"\n== Done: {changed} changed request(s) across the four deploys ==")
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        # The inventory is missing/unreadable/not JSON, a role variable is
        # invalid, or the CPU count could not be read. No host mutation is
        # implied by the variable gates, so the message is the whole report.
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
