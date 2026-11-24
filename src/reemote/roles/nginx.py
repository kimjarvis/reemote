# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The nginx role (migrated from ``geerlingguy.nginx``).
# 
# Installs and configures nginx on Ubuntu/Debian hosts: the ``dirmngr``
# prerequisite, the optional official nginx PPA (with the reinstall-on-PPA-add
# split), the apt cache refresh and the ``nginx`` package, the ``sites-enabled``
# vhost files rendered from the role's ``vhost.j2``, the optional default-vhost
# removal, the legacy ``vhosts.conf`` cleanup, the rendered ``nginx.conf``
# installed behind ``nginx -t`` validation, the service's running/enabled state,
# and the role's ``restart nginx`` / ``reload nginx`` handlers. :func:`nginx` is
# the whole task list, and :class:`NginxVars` is ``defaults/main.yml``, bound from
# the inventory's ``nginx_``-prefixed keys by
# :func:`~reemote.rolevars.resolve_vars`.
# 
# Target: hosts in the ``"Ubuntu"`` OS family. The role's RedHat, Suse, Archlinux,
# FreeBSD and OpenBSD branches are out of scope, so its ``include_vars`` OS
# dispatch collapses to the frozen Debian constants below. Both Ubuntu opt-in
# toggles (``nginx_ppa_use``, ``nginx_remove_default_vhost``) ARE implemented:
# unlike a build-from-source branch, every task they gate maps to a covered
# operation.
# 
# The stream carries four waves (design doc section 5.7):
# 
# 0. :func:`setup` -- dirmngr and the optional ``ppa:nginx/<version>`` -- closed
#    by ``checkpoint("setup done")``, which the PPA gate reads (the role's
#    ``register: nginx_ppa_added`` + ``when: nginx_ppa_added is changed``).
# 1. :func:`install` -- the conditional nginx removal, the apt cache refresh and
#    the package -- closed by ``fact(Cpus)``, the vCPU count the ``nginx.conf``
#    template's ``worker_processes`` default consumes.
# 2. :func:`configure` -- vhosts, the validated ``nginx.conf``, the service state
#    -- closed by ``checkpoint("configure done")``, which BOTH handler channels
#    read: restart and reload draw two disjoint name sets from the one report.
# 3. :func:`handler` -- ``Restart nginx (handler)`` then ``Reload nginx
#    (handler)``, in handler-definition order, only on the hosts the checkpoint
#    selected.
# 
# Three ``changed_hosts`` reads over two checkpoints (§18.3): the PPA gate reads
# the *setup* report; restart and reload both read the *configure* report. A third
# checkpoint is deliberately absent -- the second one is read twice.
# 
# :func:`nginx` returns ``None``: no composing role in the corpus consumes an
# output, so there is no outputs dataclass (§17.2).
# 
# Deviations from the Ansible role, all deliberate:
# 
# * the host-family guard is the body's opening ``if``, not a ``when=`` on every
#   request: a host outside the ``"Ubuntu"`` family returns before it yields
#   anything, so it emits no requests and no log entries at all.
# * ``file: state=absent`` maps to ``files.file(present=False, force=True)``.
#   reemote's ``File`` fact reads a symlink as "exists and is not a file", so the
#   default ``force=False`` would raise on Ubuntu's symlinked default vhost;
#   ``force=True`` reproduces Ansible's type-agnostic removal (``rm -rf`` on a
#   symlink removes only the link, never its target).
# * the role's ``changed_when: false`` on the apt-cache task has no reemote
#   analogue, but the task notifies nothing, so the log's ``changed`` flag has no
#   downstream effect (the name is in no notifying set).
# * the role's third handler, ``validate nginx configuration``, is never notified
#   by any task -- dead in the role, deliberately absent here, exactly as Ansible
#   never runs it.
# * the reload handler's ``when: nginx_service_state == "started"`` is a static
#   per-host filter: in the per-host body it narrows the notified set before the
#   ``service_handler`` is built, rather than filtering a frozen whole-inventory
#   set between runs.
# * the role's implicit variable gates become pydantic validation: the service
#   state is a :data:`~typing.Literal`, a vhost needs ``server_name`` or
#   ``filename``, an upstream needs ``name`` and a non-empty ``servers`` list, and
#   a bad value raises ``ValidationError`` (a ``ValueError``, so the runner maps
#   it) before any host is contacted.
# * ``nginx_yum_repo_enabled`` and ``nginx_zypper_repo_enabled`` are carried as
#   variables and do nothing here: they belong to the RedHat/Suse branches that
#   are out of scope. Both are documented as inert rather than silently omitted,
#   so an existing inventory still binds.
# """

from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..core import op
from ..handlers import changed_hosts, service_handler
from ..hosts import Host
from ..modules import apt, files, hardware, systemd
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, checkpoint, fact

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

#: The PPA request's stable name -- the ``register: nginx_ppa_added`` channel.
#: Kept unprefixed and stable because the setup checkpoint now selects on it
#: (§7.3): a rename silently empties the reinstall gate.
PPA_NAME = "Add PPA for Nginx"

#: The names the bundled templates are identified by; anything else in
#: :attr:`NginxVars.conf_template` / :attr:`NginxVars.vhost_template` / a
#: vhost's ``template`` is a controller-side path.
BUNDLED_CONF_TEMPLATE = "nginx.conf.j2"
BUNDLED_VHOST_TEMPLATE = "vhost.j2"

#: ``templates/nginx.conf.j2``, verbatim. Rendered control-side by
#: :func:`render_nginx_conf`. Only built-in Jinja2 filters are used
#: (``indent``), so the plain :func:`~reemote.roles._render.jinja2_environment`
#: with Ansible's whitespace defaults reproduces the role's output. The
#: ``{% block %}`` tags are kept so a custom ``nginx_conf_template`` can
#: ``{% extends "nginx.conf.j2" %}`` it, exactly as the role README documents.
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


# --- variables ---------------------------------------------------------------


class VHost(BaseModel):
    """One ``nginx_vhosts`` item: the keys ``vhost.j2`` reads.

    The role's own gate is ``server_name`` *or* ``filename`` (the dest of the
    install/removal is ``item.filename | default(item.server_name.split(' ')[0]
    ~ '.conf')``), reproduced by :func:`vhost_filename` and the model validator
    below. ``state`` is deliberately not enum-gated: the role only tests
    ``!= 'absent'``, so any other spelling installs, faithfully.

    ``return`` is a Python keyword, so the field is :attr:`return_code` with an
    alias; ``model_dump(by_alias=True)`` restores the name the template reads.
    ``extra="allow"`` keeps any further key the template might learn to read,
    and the model is frozen so the containing :class:`NginxVars` stays hashable.
    """

    model_config = ConfigDict(frozen=True, extra="allow", populate_by_name=True)

    server_name: str | None = None
    filename: str | None = None
    listen: str = "80"
    root: str | None = None
    index: str = "index.html index.htm"
    error_page: str | None = None
    access_log: str | None = None
    error_log: str | None = None
    return_code: str | None = Field(None, alias="return")
    extra_parameters: str | None = None
    server_name_redirect: str | None = None
    state: str = "present"
    #: A custom per-vhost template; ``None`` falls back to
    #: :attr:`NginxVars.vhost_template`.
    template: str | None = None

    @model_validator(mode="after")
    def _needs_server_name_or_filename(self) -> "VHost":
        """The role's ``item.server_name`` / ``item.filename`` gate."""
        if not self.server_name and not self.filename:
            raise ValueError("a vhost needs a server_name or a filename")
        return self


class Upstream(BaseModel):
    """One ``nginx_upstreams`` item: the keys ``nginx.conf.j2`` reads.

    ``name`` and a non-empty ``servers`` list are the role's own gates;
    ``strategy`` and ``keepalive`` are the template's ``is defined`` branches,
    so ``None`` omits the line rather than rendering an empty directive.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    name: str
    servers: tuple[str, ...]
    strategy: str | None = None
    keepalive: str | int | None = None

    @field_validator("name")
    @classmethod
    def _name_required(cls, value: str) -> str:
        if not value:
            raise ValueError("an upstream needs a name")
        return value

    @field_validator("servers")
    @classmethod
    def _servers_required(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("an upstream needs a non-empty servers list")
        return value


class NginxVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names carry no ``nginx_`` prefix -- the prefix passed to
    :func:`~reemote.rolevars.resolve_vars` binds ``nginx_worker_connections`` in
    the inventory to :attr:`worker_connections`, so an inventory written for the
    role needs no change. Every Ansible default is carried over, including the
    two inert ones (:attr:`yum_repo_enabled`, :attr:`zypper_repo_enabled`) so
    such a key still binds.

    Two sentinels: :attr:`user` ``None`` reproduces the
    ``set_fact: nginx_user={{ __nginx_user }}`` fallback, and
    :attr:`worker_processes` ``None`` reproduces the fact-fed default
    ``"{{ ansible_facts.processor_vcpus }}"`` -- the Cpus barrier supplies the
    count, quotes included (the role's folded scalar carries literal double
    quotes around the rendered number; drop them and ``nginx.conf`` drifts on
    every run).

    The values that reach a template verbatim are ``str``, with
    ``coerce_numbers_to_str`` so an inventory writing ``nginx_keepalive_timeout:
    75`` renders ``75`` exactly as Jinja would render the integer. Sequences are
    ``tuple``, never ``list``: the model is frozen, and a value that reaches
    :func:`~reemote.core.op` must hash (design doc section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", coerce_numbers_to_str=True)

    #: RedHat/Suse-only, out of scope: kept so an existing inventory binds.
    yum_repo_enabled: bool = True
    zypper_repo_enabled: bool = True

    # --- setup: the PPA and the package ------------------------------------
    default_release: str = ""
    ppa_use: bool = False
    ppa_version: str = "stable"
    package_name: str = "nginx"

    # --- service -----------------------------------------------------------
    service_state: Literal["started", "stopped", "restarted", "reloaded"] = "started"
    service_enabled: bool = True

    # --- templates ---------------------------------------------------------
    #: ``nginx.conf.j2`` is the bundled template; anything else is read from
    #: the controller's filesystem by :func:`render_nginx_conf`.
    conf_template: str = BUNDLED_CONF_TEMPLATE
    #: ``vhost.j2`` is the bundled template; a vhost's own ``template`` wins.
    vhost_template: str = BUNDLED_VHOST_TEMPLATE

    # --- nginx.conf --------------------------------------------------------
    #: ``None`` falls back to ``www-data`` (``vars/Debian.yml``'s
    #: ``__nginx_user``), as the role's ``set_fact`` does.
    user: str | None = None
    #: ``None`` falls back to the gathered CPU count, quoted: ``"4"``.
    worker_processes: str | None = None
    worker_connections: str = "1024"
    multi_accept: str = "off"
    error_log: str = "/var/log/nginx/error.log warn"
    access_log: str = "/var/log/nginx/access.log main buffer=16k flush=2m"
    sendfile: str = "on"
    tcp_nopush: str = "on"
    tcp_nodelay: str = "on"
    keepalive_timeout: str = "75"
    keepalive_requests: str = "600"
    server_tokens: str = "on"
    client_max_body_size: str = "64m"
    server_names_hash_bucket_size: str = "64"
    proxy_cache_path: str = ""
    extra_conf_options: str = ""
    extra_http_options: str = ""
    log_format: str = (
        "'$remote_addr - $remote_user [$time_local] \"$request\" '\n"
        "'$status $body_bytes_sent \"$http_referer\" '\n"
        "'\"$http_user_agent\" \"$http_x_forwarded_for\"'"
    )

    # --- vhosts ------------------------------------------------------------
    remove_default_vhost: bool = False
    listen_ipv6: bool = True
    vhosts: tuple[VHost, ...] = ()
    upstreams: tuple[Upstream, ...] = ()


@dataclass(frozen=True)
class NginxPlan:
    """One host's resolved configure targets, built by :func:`configure_plan`.

    Every value the configure and handler stages need is decided *before* the
    first request of their wave is built -- the rendered ``nginx.conf``, the
    vhost installs and removals, the service verbs -- so both stages are a pure
    function of this one frozen object. It replaces the example's ``plans`` dict
    keyed by host name: under a per-host body, the plan is a local.
    """

    remove_default_vhost: bool
    default_vhost_path: str
    vhost_path: str
    vhosts_install: tuple[tuple[str, str], ...]
    vhosts_remove: tuple[str, ...]
    conf_content: str
    conf_file_path: str
    state: str
    enabled: bool


# --- pure helpers ------------------------------------------------------------


def vhost_filename(vhost: VHost) -> str:
    """The role's ``item.filename|default(item.server_name.split(' ')[0] ~ '.conf')``.

    Computed control-side (the dest of a vhost install/removal), so the
    eager-evaluation trap of Jinja2's ``default()`` -- which would evaluate the
    ``server_name`` fallback even when ``filename`` is set -- never fires.
    """
    if vhost.filename:
        return vhost.filename
    # The model validator guarantees server_name is set when filename is not.
    assert vhost.server_name is not None
    return vhost.server_name.split(" ")[0] + ".conf"


def _template_environment() -> Any:
    """A Jinja2 environment whose ``DictLoader`` carries the bundled templates.

    The ``DictLoader`` is the custom-template override mechanism (§18.3): a
    ``nginx_conf_template`` / ``vhost_template`` naming anything other than the
    two bundled names is a controller-side path read by :func:`_load_template`,
    while a custom template that ``{% extends "nginx.conf.j2" %}`` resolves
    against this loader -- the inheritance the role README documents. The
    ``Environment`` settings come from
    :func:`~reemote.roles._render.jinja2_environment` so the whitespace and
    ``StrictUndefined`` choices stay identical across the rendering roles. The
    jinja2 import is inside this function so that importing ``reemote.roles``
    stays clean without the ``reemote[templates]`` extra (§17.4).
    """
    from ._render import jinja2, jinja2_environment

    return jinja2_environment(
        loader=jinja2.DictLoader(
            {
                BUNDLED_CONF_TEMPLATE: NGINX_CONF_TEMPLATE,
                BUNDLED_VHOST_TEMPLATE: VHOST_TEMPLATE,
            }
        )
    )


def _load_template(environment: Any, name: str) -> Any:
    """The bundled template by name, or a controller-side path read here.

    A custom template is a control-machine path, read in the render step (never
    inside a deploy body); the bundled names need no filesystem read.
    """
    if name in (BUNDLED_CONF_TEMPLATE, BUNDLED_VHOST_TEMPLATE):
        return environment.get_template(name)
    return environment.from_string(Path(name).read_text(encoding="utf-8"))


def render_nginx_conf(v: NginxVars, worker_processes: str) -> str:
    """``templates/nginx.conf.j2``, pre-rendered control-side.

    ``worker_processes`` is already the final spelling: the fact-fed default
    arrives quoted (``"4"`` -- the role's folded scalar carries literal double
    quotes), an explicit :attr:`NginxVars.worker_processes` override arrives
    verbatim. :attr:`NginxVars.user` ``None`` resolves to ``www-data`` here,
    reproducing the role's ``set_fact`` fallback.
    """
    environment = _template_environment()
    template = _load_template(environment, v.conf_template)
    return template.render(
        nginx_user=v.user or DEBIAN_VARS["__nginx_user"],
        nginx_error_log=v.error_log,
        nginx_pidfile=DEBIAN_VARS["nginx_pidfile"],
        nginx_worker_processes=worker_processes,
        nginx_extra_conf_options=v.extra_conf_options,
        nginx_worker_connections=v.worker_connections,
        nginx_multi_accept=v.multi_accept,
        nginx_mime_file_path=DEBIAN_VARS["nginx_mime_file_path"],
        nginx_server_names_hash_bucket_size=v.server_names_hash_bucket_size,
        nginx_client_max_body_size=v.client_max_body_size,
        nginx_log_format=v.log_format,
        nginx_access_log=v.access_log,
        nginx_sendfile=v.sendfile,
        nginx_tcp_nopush=v.tcp_nopush,
        nginx_tcp_nodelay=v.tcp_nodelay,
        nginx_keepalive_timeout=v.keepalive_timeout,
        nginx_keepalive_requests=v.keepalive_requests,
        nginx_server_tokens=v.server_tokens,
        nginx_proxy_cache_path=v.proxy_cache_path,
        nginx_extra_http_options=v.extra_http_options,
        nginx_upstreams=[
            upstream.model_dump(exclude_none=True) for upstream in v.upstreams
        ],
        nginx_conf_path=DEBIAN_VARS["nginx_conf_path"],
        nginx_vhost_path=DEBIAN_VARS["nginx_vhost_path"],
    )


def render_vhost(v: NginxVars, vhost: VHost) -> str:
    """``templates/vhost.j2`` (or the vhost's own ``template``), pre-rendered.

    The vhost is dumped with ``by_alias=True`` so :attr:`VHost.return_code`
    reaches the template as ``item.return``, and ``exclude_none=True`` so an
    unset optional key stays *undefined* for the template's ``is defined``
    branches rather than rendering as ``None``.
    """
    environment = _template_environment()
    template = _load_template(
        environment, vhost.template or v.vhost_template
    )
    return template.render(
        item=vhost.model_dump(exclude_none=True, by_alias=True),
        nginx_listen_ipv6=v.listen_ipv6,
    )


def configure_plan(v: NginxVars, cpus: int) -> NginxPlan:
    """Render every config file and freeze the reads the configure stage needs.

    Control-side work performed inside the body, after the ``fact(Cpus)``
    barrier: the gathered count feeds the ``worker_processes`` default (the
    role's ``ansible_facts.processor_vcpus``), each present vhost becomes a
    ``(filename, rendered content)`` pair and each absent vhost a filename, and
    ``nginx.conf`` becomes a plain ``str``.

    The ``None -> f'"{cpus}"'`` quoting reproduces the role's folded-scalar
    default; drop the quotes and ``nginx.conf`` drifts on every run (§18.3).
    """
    worker_processes = v.worker_processes
    if worker_processes is None:
        worker_processes = f'"{cpus}"'
    vhosts_install: list[tuple[str, str]] = []
    vhosts_remove: list[str] = []
    for vhost in v.vhosts:
        filename = vhost_filename(vhost)
        if vhost.state == "absent":
            vhosts_remove.append(filename)
        else:
            vhosts_install.append((filename, render_vhost(v, vhost)))
    return NginxPlan(
        remove_default_vhost=v.remove_default_vhost,
        default_vhost_path=DEBIAN_VARS["nginx_default_vhost_path"],
        vhost_path=DEBIAN_VARS["nginx_vhost_path"],
        vhosts_install=tuple(vhosts_install),
        vhosts_remove=tuple(vhosts_remove),
        conf_content=render_nginx_conf(v, worker_processes),
        conf_file_path=DEBIAN_VARS["nginx_conf_file_path"],
        state=v.service_state,
        enabled=v.service_enabled,
    )


def restart_notifying_names(plan: NginxPlan) -> tuple[str, ...]:
    """Every configure request name that queues ``restart nginx``.

    Only the default-vhost removal notifies restart in the role; the name is
    present exactly when the frozen plan carries the removal.
    """
    if plan.remove_default_vhost:
        return ("Remove default nginx vhost config file",)
    return ()


def reload_notifying_names(plan: NginxPlan) -> tuple[str, ...]:
    """Every configure request name that queues ``reload nginx``.

    Enumerated from the same frozen plan the deploy was built from, so the
    notification set matches the requests exactly. The final service task
    carries no ``notify`` in the role, so its name is deliberately absent.
    """
    names = ["Ensure nginx_vhost_path exists"]
    names.extend(
        f"Add managed vhost config file {filename}"
        for filename, _content in plan.vhosts_install
    )
    names.extend(
        f"Remove managed vhost config file {filename}" for filename in plan.vhosts_remove
    )
    names.append("Remove legacy vhosts.conf file")
    names.append("Copy nginx configuration in place")
    return tuple(names)


# --- helper deploys ----------------------------------------------------------


def setup(v: NginxVars) -> Deploy:
    """``tasks/setup-Ubuntu.yml``: dirmngr and the optional nginx PPA.

    The PPA branch reads only static variables (``ppa_use``), so the request
    sequence is host-determined. ``apt.ppa`` is idempotent (it reads
    ``AptSources`` for the PPA's Launchpad archive) and reports ``changed``
    only on a real add -- which is what makes the role's
    ``register: nginx_ppa_added`` + ``when: nginx_ppa_added is changed``
    reinstall split sound when the setup checkpoint reads it.
    ``update_cache=True`` refreshes the index on the add, exactly as the role's
    ``apt_repository`` task does.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            apt.packages,
            name="Ensure dirmngr is installed",
            packages=("dirmngr",),
            sudo=True,
        )
        if v.ppa_use:
            yield op(
                apt.ppa,
                name=PPA_NAME,
                src=f"ppa:nginx/{v.ppa_version}",
                update_cache=True,
                sudo=True,
            )

    return deploy


def install(v: NginxVars, ppa_added: frozenset[str]) -> Deploy:
    """``setup-Ubuntu.yml``'s reinstall task + ``tasks/setup-Debian.yml``.

    ``ppa_added`` is the ``changed_hosts`` read of the setup wave's report --
    the role's ``when: nginx_ppa_added is changed`` moved into the body. On a
    host whose PPA was just added, nginx is removed first so the subsequent
    install picks up the PPA's package; the removal is fact-gated (an absent
    nginx is a no-op). The cache refresh is ``apt.update(cache_valid_time=
    86400)`` -- idempotent within the window via the ``AptUpdateStamp`` fact.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if host.name in ppa_added:
            yield op(
                apt.packages,
                name="Ensure nginx will reinstall if the PPA was just added",
                packages=(v.package_name,),
                present=False,
                sudo=True,
            )
        yield op(
            apt.update,
            name="Update apt cache",
            cache_valid_time=CACHE_VALID_TIME,
            sudo=True,
        )
        yield op(
            apt.packages,
            name="Ensure nginx is installed",
            packages=(v.package_name,),
            default_release=v.default_release or None,
            sudo=True,
        )

    return deploy


def configure(plan: NginxPlan) -> Deploy:
    """``tasks/vhosts.yml`` + the ``nginx.conf`` template + the service task.

    Every read the role takes mid-play (the fact-fed ``worker_processes``, the
    rendered templates) was frozen into the plan inside the body, so this deploy
    is a pure function of static input. Each notifying request carries a stable
    ``name=`` enumerated by the two ``*_notifying_names`` helpers.

    The role's ``validate: nginx -t -c %s`` becomes the install's ``validate``
    hook: the rendered config is spooled, validated elevated, and only installed
    when ``nginx -t`` exits 0 -- a rejected config never lands.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        vhost_path = plan.vhost_path

        if plan.remove_default_vhost:
            yield op(
                files.file,
                name="Remove default nginx vhost config file",
                path=plan.default_vhost_path,
                present=False,
                force=True,
                sudo=True,
            )
        yield op(
            files.directory,
            name="Ensure nginx_vhost_path exists",
            path=vhost_path,
            mode="0755",
            sudo=True,
        )
        for filename, content in plan.vhosts_install:
            yield op(
                files.install,
                name=f"Add managed vhost config file {filename}",
                content=content,
                dest=f"{vhost_path}/{filename}",
                mode="0644",
                user="root",
                group=DEBIAN_VARS["root_group"],
                sudo=True,
            )
        for filename in plan.vhosts_remove:
            yield op(
                files.file,
                name=f"Remove managed vhost config file {filename}",
                path=f"{vhost_path}/{filename}",
                present=False,
                force=True,
                sudo=True,
            )
        yield op(
            files.file,
            name="Remove legacy vhosts.conf file",
            path=f"{vhost_path}/vhosts.conf",
            present=False,
            force=True,
            sudo=True,
        )
        yield op(
            files.install,
            name="Copy nginx configuration in place",
            content=plan.conf_content,
            dest=plan.conf_file_path,
            mode="0644",
            user="root",
            group=DEBIAN_VARS["root_group"],
            validate="nginx -t -c {spool}",
            sudo=True,
        )

        # The final service task (no notify). nginx_service_state maps to the
        # systemd.service verbs; restarted/reloaded are stateless (they always
        # report changed), faithful to Ansible's service module -- the default
        # started is fact-gated and converges.
        state = plan.state
        yield op(
            systemd.service,
            name="Ensure nginx service is running as configured",
            service="nginx",
            running=state != "stopped",
            restarted=state == "restarted",
            reloaded=state == "reloaded",
            enabled=plan.enabled,
            sudo=True,
        )

    return deploy


def handler(
    plan: NginxPlan,
    restart_notified: frozenset[str],
    reload_notified: frozenset[str],
) -> Deploy:
    """``handlers/main.yml``: ``restart nginx`` then ``reload nginx``.

    Ansible flushes handlers in DEFINITION order, so the composed deploy
    restarts first and reloads second; a host that notified both gets both
    (Ansible does not deduplicate across distinct handlers). The reload
    handler's ``when: nginx_service_state == "started"`` is static per-host
    data: in the per-host body it narrows the notified set before the
    ``service_handler`` is built. The third handler, ``validate nginx
    configuration``, is never notified by any task -- dead in the role,
    deliberately absent here. An empty notification set yields no request --
    the converged no-op Ansible's handler gives for free.
    """
    reload_hosts = reload_notified if plan.state == "started" else frozenset()
    restart = service_handler(restart_notified, ("nginx",), action="restarted")
    reload = service_handler(reload_hosts, ("nginx",), action="reloaded")

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield from restart(host)
        yield from reload(host)

    return deploy


# --- the role ----------------------------------------------------------------


def nginx(v: VarsSet[NginxVars]) -> Deploy:
    """Install and configure nginx on Ubuntu hosts, and run its handlers.

    Args:
        v: The role's resolved variables, one :class:`NginxVars` per host
            (``resolve_vars(NginxVars, "nginx_", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves` that returns ``None`` for every
        host it runs -- no composing role in the corpus consumes an output
        (§17.2).

    Raises:
        ValueError: The CPU count could not be determined for the
            ``worker_processes`` default. Raised inside the body, after the
            install wave applied and before the configure wave is built, so
            nothing is half-configured.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return None

        # Wave 0: dirmngr + the optional PPA, closed by the checkpoint the
        # reinstall gate reads. The gate is the role's
        # `register: nginx_ppa_added` + `when: nginx_ppa_added is changed`.
        yield from setup(hv)(host)
        setup_report = yield checkpoint("setup done")
        ppa_added = changed_hosts(setup_report, (PPA_NAME,))

        # Wave 1: the conditional removal, the cache refresh, the package.
        # The fact(Cpus) barrier below closes this wave, which is what makes
        # the packages installed before the count is read.
        yield from install(hv, ppa_added)(host)

        cpus = yield fact(hardware.Cpus)
        if cpus is None:
            raise ValueError(
                f"{host.name}: cannot determine the CPU count for "
                "nginx_worker_processes"
            )

        # Wave 2: vhosts + the validated nginx.conf + the service state,
        # closed by the checkpoint BOTH handler channels read (§18.3): two
        # disjoint name sets over the one report, never a third checkpoint.
        plan = configure_plan(hv, cpus)
        yield from configure(plan)(host)
        configure_report = yield checkpoint("configure done")
        restart_notified = changed_hosts(
            configure_report, restart_notifying_names(plan)
        )
        reload_notified = changed_hosts(configure_report, reload_notifying_names(plan))

        # Wave 3: restart then reload, in handler-definition order, only on
        # the hosts the checkpoint selected.
        yield from handler(plan, restart_notified, reload_notified)(host)

        return None

    return deploy


__all__ = ["NginxVars", "nginx"]
