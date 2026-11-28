# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The Varnish role (migrated from ``geerlingguy.varnish``).
#
# Installs and configures Varnish Cache on Ubuntu hosts: the packagecloud
# repository dependencies, the vendor key and repository, the ``varnish``
# package plus the optional VMOD package, the ``/etc/systemd/system`` directory,
# the rendered sysvinit fragment ``/etc/default/varnish``, the rendered
# ``varnish.service`` unit override, the optional default VCL, the admin secret,
# the enabled services, and the role's ``restart varnish`` handler, with
# upstream's ``reload systemd`` folded into it (D4). :func:`varnish` is the whole
# task list and :class:`VarnishVars` is ``defaults/main.yml``, bound from the
# inventory entry's ``varnish`` role mapping by
# :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. A host outside the family returns
# before its first ``yield`` and emits no requests, no barriers and no log lines
# at all. The role's RedHat branch and its Ubuntu 16.04 ("xenial") branches are
# out of scope, so its ``include_vars`` OS dispatch collapses to the frozen
# ``vars/Debian.yml`` constants below and three of its tasks -- the
# ``varnish.params`` template and the two xenial-gated service loops -- drop out
# of the stream.
#
# The stream is three waves on a changed host, two once converged:
#
# 0. ``fact(LinuxDistribution)`` -- ``release_meta.VERSION_CODENAME`` feeds the
#    repository's ``Suites`` and ``release_meta``/``name`` its URL segment, so
#    the whole request wave below is a pure function of the frozen plan.
# 1. :func:`setup` (dependencies, repository, packages), :func:`configure` (the
#    unit directory and the four rendered files with their metadata companions)
#    and :func:`services` (the started/enabled loop) -- all in the role's own
#    order, closed by ``checkpoint("configure done")``. No read sits between the
#    three stages, so they share one wave: the driver applies each request
#    before pulling the next, which is what lets the repository precede the
#    package install inside a single wave.
# 2. :func:`~reemote.handlers.restart_handler` -- one ``Restart varnish
#    (handler)`` request per host a notifying request changed, carrying
#    ``daemon_reload=True`` so the state-gated ``daemon-reload`` behind
#    upstream's separate ``reload systemd`` handler precedes the restart. The
#    wave disappears once converged.
#
# **One checkpoint, one handler read.** Ansible flushed both handlers at the end
# of the play, so a single report is the faithful place to read the notification
# set; unlike the docker role there is no mid-play ``flush_handlers`` to widen it
# across two reports.
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: ``get_url`` of the armored key into ``/etc/apt/trusted.gpg.d`` plus the
#   one-line ``apt_repository`` task become **one** ``apt.sources_file`` request
#   with a URL :samp:`signed_by` (issue #17). It installs the key as
#   ``/etc/apt/keyrings/varnishcache_<repo>.asc`` (``0644``, content-idempotent)
#   and writes the equivalent deb822 ``.sources`` file. ``trusted.gpg.d`` is the
#   mechanism upstream's own comment calls deprecated -- and the deb822 form is
#   what the user approved for this migration.
# * D2: the index refresh rides ``update_cache=True``, so it chains onto a
#   *real* sources change (write or removal, issue #14) instead of the
#   ``apt_repository`` module's unconditional refresh.
# * D3: the templates' ``is version(...)`` gate is Ansible's ``version`` test,
#   which plain Jinja2 does not ship. It is registered on the render environment
#   as a numeric-tuple comparison (:data:`VERSION_COMPARATORS`), the
#   :mod:`reemote.roles.zabbix_agent` precedent -- a template detail, not a
#   library gap.
# * D4: the role's two handlers collapse into **one** request. Upstream notifies
#   ``reload systemd`` from the unit template task alone and ``restart varnish``
#   from all four, so a host that reloads is always a host that restarts: the
#   role emits a single
#   ``restart_handler(notified, SERVICE, daemon_reload=True)``. Its
#   ``SystemdNeedDaemonReload`` gate (issues #50 and #75) yields
#   ``daemon-reload`` before ``restart`` exactly while the unit's loaded
#   fragment is stale *or* outranked by a fragment on disk -- the freshly written
#   ``/etc/systemd/system/varnish.service`` included -- and yields neither once
#   converged. Before #75 the gate saw only staleness, so the folded form
#   installed the override and restarted from the packaged fragment; the role
#   then carried the reload as its own unconditional ``systemd.daemon_reload``
#   request, which no longer buys anything and is gone.
# * D5: ``varnish_enabled_services`` is **honored** (the user's choice). Both
#   upstream tasks gate on ``ansible_facts.os_family != 'Debian'`` -- a
#   contradiction for every Debian-family host -- so as written the loop is dead
#   code on Ubuntu and only the packaged unit's own enablement makes Varnish
#   start. The migration implements what the variable says it means: one
#   ``systemd.service(running=True, enabled=True)`` request per listed service.
# * D6: each rendered file gets a paired ``files.file`` metadata companion.
#   ``files.install`` is idempotent on *content only*, while Ansible's
#   ``template`` reports a change on owner/group/mode drift too; the companion
#   converges those bits and carries its own notifying name.
# * D7: the host-family guard is the body's opening ``if``, not a ``when=`` on
#   every request, and the role's variable gates become pydantic validation --
#   the port numbers, the non-empty names, the absolute ``config_path`` and the
#   tuple-normalized sequences all raise ``ValidationError`` (a ``ValueError``)
#   before any host is contacted.
# * ``set_fact: varnish_packagecloud_repo`` becomes the pure
#   :func:`packagecloud_repo` helper, and the two ``include_vars`` OS dispatch
#   files become :data:`SYSVINIT_CONFIG_PATH` / :data:`SYSTEMD_UNIT_DIR`.
# """

from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core import op
from ..handlers import changed_hosts, restart_handler
from ..hosts import Host
from ..modules import apt, files, systemd
from ..modules.server import LinuxDistribution
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, checkpoint, fact

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path only) ---

#: The vendor root of both derived URLs. ``varnish_apt_repo`` adds the
#: ``/<distribution>/`` segment and the release's ``gpgkey`` does not carry it.
PACKAGECLOUD_BASE_URL = "https://packagecloud.io/varnishcache"

#: ``setup-Debian.yml``'s ``apt: name=[apt-transport-https, gnupg2]``. Kept even
#: though the deb822 text is generated control-side, so the host matches the
#: role's post-state (the docker precedent).
DEPENDENCY_PACKAGES = ("apt-transport-https", "gnupg2")

#: ``vars/Debian.yml``, frozen to the single Ubuntu target: the sysvinit
#: fragment's directory and the directory the unit override is written into.
SYSVINIT_CONFIG_PATH = "/etc/default"
SYSTEMD_UNIT_DIR = "/etc/systemd/system"

#: The two derived destinations the role's two always-running template tasks
#: write to: ``{{ varnish_sysvinit_config_path }}/varnish`` and
#: ``{{ varnish_systemd_override_path }}/varnish.service``.
SYSVINIT_PATH = f"{SYSVINIT_CONFIG_PATH}/varnish"
UNIT_PATH = f"{SYSTEMD_UNIT_DIR}/varnish.service"

#: The unit name both the service loop and the handler act on. Upstream's
#: ``restart varnish`` handler names it literally, whatever
#: :attr:`VarnishVars.enabled_services` holds.
SERVICE = "varnish"

#: The name :attr:`VarnishVars.default_vcl_template_path` uses for the bundled
#: :data:`DEFAULT_VCL_TEMPLATE`; anything else is a controller-side path.
BUNDLED_VCL_TEMPLATE = "default.vcl.j2"

#: The request names that queue the role's handlers (``handlers/main.yml``). The
#: four ``template`` tasks notify; the directory, the packages, the repository
#: and the service loop do not, so their names are deliberately absent from
#: :func:`notifying_names`.
NOTIFY_SYSVINIT = "Copy Varnish configuration (sysvinit)"
NOTIFY_SYSVINIT_ATTRS = "Set Varnish configuration attributes (sysvinit)"
NOTIFY_UNIT = "Copy override for systemd unit"
NOTIFY_UNIT_ATTRS = "Set Varnish systemd unit attributes"
NOTIFY_VCL = "Copy Varnish default VCL"
NOTIFY_VCL_ATTRS = "Set Varnish default VCL attributes"
NOTIFY_SECRET = "Copy varnish secret"
NOTIFY_SECRET_ATTRS = "Set varnish secret attributes"

#: ``templates/varnish.j2``, verbatim. Rendered control-side by
#: :func:`render_sysvinit` -- reemote has no template operation. It uses the
#: ``version`` test (D3) and no ``ansible_managed``, so nothing needs freezing;
#: :func:`~reemote.roles._render.jinja2_environment` supplies Ansible's
#: whitespace defaults, which is what makes the output byte-identical.
SYSVINIT_TEMPLATE = r"""# Configuration file for varnish
#
# /etc/init.d/varnish expects the variable $DAEMON_OPTS to be set from this
# shell script fragment.
#

# Start varnish (only applies to Debian-based hosts)
START=yes

# Maximum number of open files (for ulimit -n)
NFILES={{ varnish_limit_nofile }}

# Locked shared memory (for ulimit -l)
# Default log size is 82MB + header
MEMLOCK=82000

# Maximum number of threads (for ulimit -u)
NPROCS="unlimited"

# Maximum size of corefile (for ulimit -c). Default in Fedora is 0
# DAEMON_COREFILE_LIMIT="unlimited"

# Set this to 1 to make init script reload try to switch vcl without restart.
# To make this work, you need to set the following variables
# explicit: VARNISH_VCL_CONF, VARNISH_ADMIN_LISTEN_ADDRESS,
# VARNISH_ADMIN_LISTEN_PORT, VARNISH_SECRET_FILE, or in short,
# use Alternative 3, Advanced configuration, below
RELOAD_VCL=1

{% if varnish_pidfile %}
# Varnish PID file
PIDFILE="{{ varnish_pidfile }}"
{% endif %}

# This file contains 4 alternatives, please use only one.

## Alternative 1, Minimal configuration, no VCL
#
# Listen on port 6081, administration on localhost:6082, and forward to
# content server on localhost:8080.  Use a fixed-size cache file.
#
#DAEMON_OPTS="-a :6081 \
#             -T localhost:6082 \
#             -b localhost:8080 \
#             -u varnish -g varnish \
#             -s file,/var/lib/varnish/varnish_storage.bin,1G"


## Alternative 2, Configuration with VCL
#
# Listen on port 6081, administration on localhost:6082, and forward to
# one content server selected by the vcl file, based on the request.  Use a
# fixed-size cache file.
#
#DAEMON_OPTS="-a :6081 \
#             -T localhost:6082 \
#             -f /etc/varnish/default.vcl \
#             -u varnish -g varnish \
#             -S /etc/varnish/secret \
#             -s file,/var/lib/varnish/varnish_storage.bin,1G"


## Alternative 3, Advanced configuration
#
# See varnishd(1) for more information.
#
# # Main configuration file. You probably want to change it :)
VARNISH_VCL_CONF={{ varnish_config_path }}/default.vcl
#
# # Default address and port to bind to
# # Blank address means all IPv4 and IPv6 interfaces, otherwise specify
# # a host name, an IPv4 dotted quad, or an IPv6 address in brackets.
VARNISH_LISTEN_ADDRESS={{ varnish_listen_address }}
VARNISH_LISTEN_PORT={{ varnish_listen_port }}
#
# # Telnet admin interface listen address and port
VARNISH_ADMIN_LISTEN_ADDRESS={{ varnish_admin_listen_host }}
VARNISH_ADMIN_LISTEN_PORT={{ varnish_admin_listen_port }}
#
# # Shared secret file for admin interface
VARNISH_SECRET_FILE={{ varnish_config_path }}/secret
#
# # The minimum number of worker threads to start
VARNISH_MIN_THREADS=50
#
# # The Maximum number of worker threads to start
VARNISH_MAX_THREADS=1000
#
# # Idle timeout for worker threads
VARNISH_THREAD_TIMEOUT=120
#
# # Backend storage specification
VARNISH_STORAGE="{{ varnish_storage }}"
#
# # Default TTL used when the backend does not specify one
VARNISH_TTL=120
#
# # DAEMON_OPTS is used by the init script.  If you add or remove options, make
# # sure you update this section, too.
DAEMON_OPTS="-a ${VARNISH_LISTEN_ADDRESS}:${VARNISH_LISTEN_PORT} \
             -f ${VARNISH_VCL_CONF} \
             -T ${VARNISH_ADMIN_LISTEN_ADDRESS}:${VARNISH_ADMIN_LISTEN_PORT} \
             -t ${VARNISH_TTL} \
             -p thread_pool_min=${VARNISH_MIN_THREADS} \
             -p thread_pool_max=${VARNISH_MAX_THREADS} \
             -p thread_pool_timeout=${VARNISH_THREAD_TIMEOUT} \
{% if varnish_version is version('4.1', '<') %}
             -u varnish -g varnish \
{% endif %}
{% if varnishd_extra_options %}
             {{ varnishd_extra_options }}
{% endif %}
             -S ${VARNISH_SECRET_FILE} \
             -s ${VARNISH_STORAGE}"
#


## Alternative 4, Do It Yourself. See varnishd(1) for more information.
#
# DAEMON_OPTS=""
"""

#: ``templates/varnish.service.j2``, verbatim -- the whole packaged unit
#: rewritten in ``/etc/systemd/system`` (upstream overrides the unit rather than
#: dropping a file into ``varnish.service.d``). Its write is what makes the
#: handler's ``daemon_reload`` pending (D4).
UNIT_TEMPLATE = r"""[Unit]
Description=Varnish Cache, a high-performance HTTP accelerator
After=network-online.target

[Service]
Type=forking
KillMode=process

{% if varnish_pidfile %}
PIDFile={{ varnish_pidfile }}
{% endif %}

# Maximum number of open files (for ulimit -n)
LimitNOFILE={{ varnish_limit_nofile }}

# Locked shared memory - should suffice to lock the shared memory log
# (varnishd -l argument)
# Default log size is 80MB vsl + 1M vsm + header -> 82MB
# unit is bytes
LimitMEMLOCK=85983232

# Enable this to avoid "fork failed" on reload.
TasksMax=infinity

# Maximum size of the corefile.
LimitCORE=infinity

ExecStart=/usr/sbin/varnishd -a {{ varnish_listen_address }}:{{ varnish_listen_port }} -T {{ varnish_admin_listen_host }}:{{ varnish_admin_listen_port }}{% if varnish_pidfile %} -P {{ varnish_pidfile }}{% endif %} -f {{ varnish_config_path }}/default.vcl -S {{ varnish_config_path }}/secret -s {{ varnish_storage }} {{ varnishd_extra_options }}
ExecReload=/usr/sbin/varnishreload

Restart=on-failure

[Install]
WantedBy=multi-user.target
"""

#: ``templates/default.vcl.j2``, verbatim. Reads the ``varnish_backends`` and
#: ``varnish_vhosts`` mappings, which the role leaves *undefined* by default --
#: :func:`render_default_vcl` therefore omits those context keys entirely rather
#: than passing ``None``, so the ``is defined`` gates behave as under Ansible
#: against a strict-undefined environment.
DEFAULT_VCL_TEMPLATE = r"""{% if varnish_version is version('4.0', '>=') %}
vcl 4.0;
{% endif %}

# This is a basic VCL configuration file for varnish.  See the vcl(7)
# man page for details on VCL syntax and semantics.
#
# Default backend definition.  Set this to point to your content
# server.
backend default {
  .host = "{{ varnish_default_backend_host }}";
  .port = "{{ varnish_default_backend_port }}";
}

{% if varnish_backends is defined %}
# Other backend servers.
{% for backend, value in varnish_backends.items() | list %}
backend {{ backend }} {
  .host = "{{ value.host }}";
  .port = "{{ value.port }}";
}
{% endfor %}
{% endif %}

sub vcl_recv {
{% if varnish_vhosts is defined %}
  # Varnish vhosts.
{% for vhost, value in varnish_vhosts.items() | list if varnish_vhosts is defined %}
  {% if loop.first %}
  if (req.http.host == "{{ vhost }}" || req.http.host == "www.{{ vhost }}") {
  {% else %}
  elseif (req.http.host == "{{ vhost }}" || req.http.host == "www.{{ vhost }}") {
  {% endif %}
    set req.backend_hint = {{ value.backend }};
  }
{% endfor %}
{% endif %}
}
"""


# --- variables ---------------------------------------------------------------


def coerce_port(value: Any) -> Any:
    """Accept ``"8080"`` as well as ``8080`` for the role's port variables.

    Upstream declares its ports as strings (``varnish_listen_port: "80"``), and
    an inventory is free to write either spelling; both render identically as
    ``{{ ... }}``.
    """
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


class VarnishBackend(BaseModel):
    """One extra backend: a value of ``varnish_backends``.

    The role's commented example is ``{host: 10.0.2.2, port: 80}``, and the
    template reads ``value.host`` / ``value.port``. The model is frozen so the
    containing :class:`VarnishVars` stays hashable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str
    port: int

    @field_validator("host")
    @classmethod
    def _host_non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("a backend needs a host")
        return value

    @field_validator("port", mode="before")
    @classmethod
    def _port_range(cls, value: Any) -> Any:
        return coerce_port(value)

    @field_validator("port")
    @classmethod
    def _port_is_sane(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return value


class VarnishVHost(BaseModel):
    """One name-based vhost: a value of ``varnish_vhosts``.

    Only the backend name is read -- the template builds the whole
    ``if/elseif req.http.host`` chain from the mapping's keys.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    backend: str

    @field_validator("backend")
    @classmethod
    def _backend_non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("a vhost needs a backend name")
        return value


class VarnishVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``varnish`` role mapping, so ``listen_port`` there sets
    :attr:`listen_port`. Every Ubuntu-path default is carried over, including the
    two RedHat-only variables' absence (:attr:`yum_repo_baseurl` and
    :attr:`packagecloud_repo_yum_priority` are deliberately not modelled --
    the yum branch is out of scope).

    Sequences are ``tuple``, never ``list``: the model is frozen and every value
    that reaches :func:`~reemote.core.op` must hash (design doc section 8.1).
    :attr:`backends` and :attr:`vhosts` are the two mappings, and they only ever
    reach a request after :func:`render_default_vcl` has turned them into a
    ``str``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``varnish_package_name``: the package the role installs.
    package_name: str = "varnish"
    #: ``varnish_modules_package_name``: an optional VMOD package; empty skips.
    modules_package_name: str = ""
    #: ``varnish_version``: drives ``varnish_packagecloud_repo`` *and* the
    #: templates' ``is version(...)`` gates. The vendor only publishes a subset
    #: of releases for a given Ubuntu codename (``varnish66`` has no ``noble``),
    #: so a modern release usually needs a newer value here.
    version: str = "6.6"

    # --- the VCL ------------------------------------------------------------
    #: ``varnish_use_default_vcl``: when false, no ``default.vcl`` is written
    #: (and it does not notify).
    use_default_vcl: bool = True
    #: ``varnish_default_vcl_template_path``: the bundled ``default.vcl.j2``, or
    #: a controller-side path read by :func:`render_default_vcl`.
    default_vcl_template_path: str = BUNDLED_VCL_TEMPLATE
    #: ``varnish_default_backend_host`` / ``varnish_default_backend_port``.
    default_backend_host: str = "127.0.0.1"
    default_backend_port: int = 8080
    #: ``varnish_backends``: the extra backends, keyed by backend name.
    backends: dict[str, VarnishBackend] = Field(default_factory=dict)
    #: ``varnish_vhosts``: hostname -> backend, rendered into the
    #: ``req.http.host`` chain of ``sub vcl_recv``.
    vhosts: dict[str, VarnishVHost] = Field(default_factory=dict)

    # --- listening and administration ---------------------------------------
    #: ``varnish_listen_address``: empty means every interface.
    listen_address: str = ""
    #: ``varnish_listen_port``: the front-end port -- ``80`` upstream, which
    #: collides with an existing web server on a shared host.
    listen_port: int = 80
    #: ``varnish_admin_listen_host`` / ``varnish_admin_listen_port``: the
    #: ``varnishadm`` endpoint.
    admin_listen_host: str = "127.0.0.1"
    admin_listen_port: int = 6082
    #: ``varnish_secret``: the ``-S`` admin authentication secret (``secret.j2``
    #: is the variable plus a newline).
    secret: str = "14bac2e6-1e34-4770-8078-974373b76c90"

    # --- paths and daemon tuning --------------------------------------------
    #: ``varnish_config_path``: holds ``default.vcl`` and ``secret``.
    config_path: str = "/etc/varnish"
    #: ``varnish_limit_nofile``: the unit's ``LimitNOFILE`` and NFILES.
    limit_nofile: int = 131072
    #: ``varnish_storage``: varnishd's ``-s`` argument.
    storage: str = "file,/var/lib/varnish/varnish_storage.bin,256M"
    #: ``varnish_pidfile``: empty drops the ``PIDFile``/``-P`` lines.
    pidfile: str = "/run/varnishd.pid"
    #: ``varnishd_extra_options``: appended to the ``ExecStart`` line.
    varnishd_extra_options: str = ""

    # --- services and repository --------------------------------------------
    #: ``varnish_enabled_services``: started *and* enabled (see D5).
    enabled_services: tuple[str, ...] = ("varnish",)
    #: ``varnish_apt_use_packagecloud``: when false, neither the dependencies
    #: nor the repository are managed and the distro's own package is used.
    apt_use_packagecloud: bool = True
    #: ``varnish_packagecloud_repo``: ``None`` derives ``varnish<flattened>``,
    #: as the role's ``set_fact`` does.
    packagecloud_repo: str | None = None
    #: The full repository base URL: ``None`` derives
    #: ``<base>/<repo>/<distribution>`` from the read distribution.
    apt_repo_url: str | None = None
    #: The armored key URL: ``None`` derives ``<base>/<repo>/gpgkey`` -- no
    #: distribution segment, exactly as upstream's ``get_url`` builds it.
    apt_gpg_key: str | None = None

    @field_validator("enabled_services", mode="before")
    @classmethod
    def _normalize_sequence(cls, value: Any) -> Any:
        """A list becomes a tuple; a non-sequence falls through for pydantic."""
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @field_validator("enabled_services")
    @classmethod
    def _services_non_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not all(isinstance(item, str) and item for item in value):
            raise ValueError("enabled_services must hold non-empty strings")
        return value

    @field_validator("listen_port", "admin_listen_port", "default_backend_port", mode="before")
    @classmethod
    def _coerce_port(cls, value: Any) -> Any:
        return coerce_port(value)

    @field_validator("listen_port", "admin_listen_port", "default_backend_port")
    @classmethod
    def _port_in_range(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return value

    @field_validator("package_name", "version", "secret", "default_vcl_template_path")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """An empty name is a typo, not "unset"."""
        if not value:
            raise ValueError("must be a non-empty string")
        return value

    @field_validator("config_path")
    @classmethod
    def _absolute_config_path(cls, value: str) -> str:
        """Two managed paths are derived by concatenating onto it."""
        if not value.startswith("/"):
            raise ValueError("config_path must be an absolute path")
        if value.endswith("/"):
            raise ValueError("config_path must not end with a slash")
        return value

    @field_validator("limit_nofile")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("limit_nofile must be positive")
        return value

    @field_validator("backends", "vhosts")
    @classmethod
    def _keys_non_empty(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not all(key for key in value):
            raise ValueError("keys must be non-empty names")
        return value


@dataclass(frozen=True)
class VarnishPlan:
    """One host's resolved targets, built by :func:`varnish_plan`.

    Everything except the release codename and the distribution segment is
    decided from the resolved variables; those two arrive from the
    ``LinuxDistribution`` barrier and the plan is frozen right after, so every
    deploy below is a pure function of this one object. The four rendered files
    are already ``str`` here -- the render happens once, inside the body, before
    the wave is built.
    """

    codename: str
    repo_name: str
    repo_url: str
    gpg_key_url: str
    sources_filename: str
    use_packagecloud: bool
    package_name: str
    modules_package_name: str
    sysvinit_content: str
    unit_content: str
    vcl_content: str | None
    secret_content: str
    vcl_path: str
    secret_path: str
    enabled_services: tuple[str, ...]
    notify_names: tuple[str, ...] = field(default=())


# --- pure helpers ------------------------------------------------------------


def packagecloud_repo(version: str) -> str:
    """``set_fact: varnish_packagecloud_repo`` -- ``6.6`` becomes ``varnish66``."""
    return f"varnish{version.replace('.', '')}"


def distribution_codename(info: dict[str, Any]) -> str:
    """``ansible_facts.distribution_release`` from a LinuxDistribution read.

    Ubuntu's ``/etc/os-release`` carries ``VERSION_CODENAME`` (e.g. ``noble``);
    the fact upper-cases the os-release keys into ``release_meta``. A missing
    codename is a hard error -- the repository's ``Suites`` would be empty and
    apt would reject the source.
    """
    codename = (info.get("release_meta") or {}).get("VERSION_CODENAME")
    if not codename:
        raise ValueError(
            "cannot read VERSION_CODENAME from the distribution facts "
            f"(release_meta={info.get('release_meta')!r}); the packagecloud "
            "Varnish repository needs the Ubuntu release codename for its Suites"
        )
    return str(codename)


def distribution_segment(info: dict[str, Any]) -> str:
    """``ansible_facts.distribution | lower`` -- the URL's OS segment."""
    return str(info.get("name") or "").lower() or "ubuntu"


def version_operands(text: Any) -> tuple[int, ...]:
    """The numeric tuple of a version string, dotted/underscored/dashed alike.

    ``varnish_version`` is compared against the ``'4.0'``/``'4.1'`` literals in
    the templates; Ansible's own comparison is ``LooseVersion``-based, and for
    these inputs a plain tuple of ints -- zero-padded left to right -- gives the
    same verdict.
    """
    parts = str(text).replace("_", ".").replace("-", ".").split(".")
    return tuple(int(part) for part in parts if part.isdigit())


#: The operators the ``version`` test can be asked for (D3).
VERSION_COMPARATORS: dict[str, Callable[[tuple[int, ...], tuple[int, ...]], bool]] = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "ge": lambda a, b: a >= b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "lt": lambda a, b: a < b,
}


def version_compare(value: Any, comparison: Any, eqln: str = ">=") -> bool:
    """Ansible's ``version`` test, restricted to numeric versions (D3).

    Raises:
        ValueError: An unknown comparison operator -- the template's own typo,
            surfaced rather than silently rendering the wrong branch.
    """
    try:
        compare = VERSION_COMPARATORS[eqln]
    except KeyError:
        raise ValueError(f"unknown version comparison operator {eqln!r}") from None
    left, right = version_operands(value), version_operands(comparison)
    width = max(len(left), len(right))
    return compare(
        left + (0,) * (width - len(left)),
        right + (0,) * (width - len(right)),
    )


def _environment() -> Any:
    """``jinja2_environment`` plus the templates' Ansible-only ``version`` test.

    The import lives inside this function so ``import reemote.roles`` stays
    clean without the ``reemote[templates]`` extra (design doc section 17.4).
    """
    from ._render import jinja2_environment

    env = jinja2_environment()
    # ``varnish_version is version('4.1', '<')`` is a *test*, resolved against
    # ``env.tests`` -- plain Jinja2 ships no ``version`` test (D3).
    env.tests["version"] = version_compare
    return env


def _daemon_context(v: VarnishVars) -> dict[str, Any]:
    """The context both daemon-facing templates read (their variables overlap)."""
    return {
        "varnish_version": v.version,
        "varnish_limit_nofile": v.limit_nofile,
        "varnish_pidfile": v.pidfile,
        "varnish_config_path": v.config_path,
        "varnish_listen_address": v.listen_address,
        "varnish_listen_port": v.listen_port,
        "varnish_admin_listen_host": v.admin_listen_host,
        "varnish_admin_listen_port": v.admin_listen_port,
        "varnish_storage": v.storage,
        "varnishd_extra_options": v.varnishd_extra_options,
    }


def render_sysvinit(v: VarnishVars) -> str:
    """``templates/varnish.j2``, pre-rendered control-side (recipe 4)."""
    return _environment().from_string(SYSVINIT_TEMPLATE).render(**_daemon_context(v))


def render_unit(v: VarnishVars) -> str:
    """``templates/varnish.service.j2``, pre-rendered control-side."""
    return _environment().from_string(UNIT_TEMPLATE).render(**_daemon_context(v))


def render_default_vcl(v: VarnishVars) -> str:
    """``templates/{{ varnish_default_vcl_template_path }}``, control-side.

    The two optional mappings are *omitted* from the context when empty, which is
    what keeps the template's ``{% if varnish_backends is defined %}`` gates
    behaving as under Ansible against a strict-undefined environment. A
    non-bundled ``default_vcl_template_path`` is a controller-side path, read
    here rather than on the host.
    """
    template_text = (
        DEFAULT_VCL_TEMPLATE
        if v.default_vcl_template_path == BUNDLED_VCL_TEMPLATE
        else Path(v.default_vcl_template_path).read_text(encoding="utf-8")
    )
    context: dict[str, Any] = {
        "varnish_version": v.version,
        "varnish_default_backend_host": v.default_backend_host,
        "varnish_default_backend_port": v.default_backend_port,
    }
    if v.backends:
        context["varnish_backends"] = {
            name: {"host": backend.host, "port": backend.port}
            for name, backend in v.backends.items()
        }
    if v.vhosts:
        context["varnish_vhosts"] = {
            name: {"backend": vhost.backend} for name, vhost in v.vhosts.items()
        }
    return _environment().from_string(template_text).render(**context)


def render_secret(v: VarnishVars) -> str:
    """``templates/secret.j2`` -- the secret and one newline; plain Python."""
    return f"{v.secret}\n"


def notifying_names(plan_fields: dict[str, Any]) -> tuple[str, ...]:
    """Every request name that queues ``restart varnish`` for one host.

    Enumerated from the same frozen inputs the deploys were built from, so the
    notification set matches the requests exactly. The default VCL pair joins the
    set only when :attr:`VarnishVars.use_default_vcl` writes the file at all --
    the role's own ``when:``.
    """
    names = [NOTIFY_SYSVINIT, NOTIFY_SYSVINIT_ATTRS, NOTIFY_UNIT, NOTIFY_UNIT_ATTRS]
    if plan_fields["vcl_content"] is not None:
        names.extend((NOTIFY_VCL, NOTIFY_VCL_ATTRS))
    names.extend((NOTIFY_SECRET, NOTIFY_SECRET_ATTRS))
    return tuple(names)


def varnish_plan(hv: VarnishVars, info: dict[str, Any]) -> VarnishPlan:
    """Freeze the per-host targets from the resolved vars and the distribution.

    Reproduces the role's variable derivations: ``varnish_packagecloud_repo``
    from ``varnish_version``, the repository URL from that repo name plus the
    lower-cased distribution, the key URL from the repo name alone, and the three
    Jinja2 renders plus the secret.

    Raises:
        ValueError: The distribution read carries no ``VERSION_CODENAME``.
        ImportError: The ``reemote[templates]`` extra is missing -- the render
            happens here, inside the body, before the first request is built.
    """
    codename = distribution_codename(info)
    repo_name = hv.packagecloud_repo or packagecloud_repo(hv.version)
    segment = distribution_segment(info)
    repo_url = hv.apt_repo_url or f"{PACKAGECLOUD_BASE_URL}/{repo_name}/{segment}"
    gpg_key_url = hv.apt_gpg_key or f"{PACKAGECLOUD_BASE_URL}/{repo_name}/gpgkey"
    vcl_content = render_default_vcl(hv) if hv.use_default_vcl else None
    fields = {"vcl_content": vcl_content}
    return VarnishPlan(
        codename=codename,
        repo_name=repo_name,
        repo_url=repo_url,
        gpg_key_url=gpg_key_url,
        sources_filename=f"varnishcache_{repo_name}",
        use_packagecloud=hv.apt_use_packagecloud,
        package_name=hv.package_name,
        modules_package_name=hv.modules_package_name,
        sysvinit_content=render_sysvinit(hv),
        unit_content=render_unit(hv),
        vcl_content=vcl_content,
        secret_content=render_secret(hv),
        vcl_path=f"{hv.config_path}/default.vcl",
        secret_path=f"{hv.config_path}/secret",
        enabled_services=tuple(hv.enabled_services),
        notify_names=notifying_names(fields),
    )


# --- deploys -----------------------------------------------------------------


def setup(plan: VarnishPlan) -> Deploy:
    """``tasks/setup-Debian.yml``: dependencies, repository, packages.

    The key and the repository are one ``apt.sources_file`` request whose URL
    ``signed_by`` installs the repo-named keyring itself (D1) and whose
    ``update_cache`` chains the index refresh onto a real change (D2) -- the
    refresh must precede the package install, and it does, because the driver
    applies each request before pulling the next. Both blocks are gated on
    ``varnish_apt_use_packagecloud`` as the role's own ``when:`` is.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if plan.use_packagecloud:
            yield op(
                apt.packages,
                name="Ensure dependencies are present",
                packages=DEPENDENCY_PACKAGES,
                sudo=True,
            )
            yield op(
                apt.sources_file,
                name="Add packagecloud.io Varnish apt repository",
                filename=plan.sources_filename,
                types=("deb",),
                uris=(plan.repo_url,),
                suites=(plan.codename,),
                components=("main",),
                signed_by=plan.gpg_key_url,
                update_cache=True,
                sudo=True,
            )
        yield op(
            apt.packages,
            name="Ensure Varnish is installed",
            packages=(plan.package_name,),
            sudo=True,
        )
        if plan.modules_package_name:
            yield op(
                apt.packages,
                name="Ensure Varnish VMODs are installed",
                packages=(plan.modules_package_name,),
                sudo=True,
            )

    return deploy


def configure(plan: VarnishPlan) -> Deploy:
    """``tasks/main.yml``'s directory and four template tasks, in the role's order.

    Branch-free with respect to facts -- the codename it needed is already frozen
    into the plan -- so it is one pure request stage (the kibana/redis shape).
    Each notifying request carries a stable ``name=`` enumerated by
    :func:`notifying_names`; each ``files.install`` is paired with the
    ``files.file`` that converges the ``root:root 0644`` the Ansible template
    enforces (D6). The ``varnish.params`` task is absent: its ``when:`` selects
    xenial and RedHat 7 only, out of this migration's scope.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            files.directory,
            name="Ensure Varnish config path exists",
            path=SYSTEMD_UNIT_DIR,
            mode="0755",
            sudo=True,
        )
        yield op(
            files.install,
            name=NOTIFY_SYSVINIT,
            content=plan.sysvinit_content,
            dest=SYSVINIT_PATH,
            user="root",
            group="root",
            mode="0644",
            sudo=True,
        )
        yield op(
            files.file,
            name=NOTIFY_SYSVINIT_ATTRS,
            path=SYSVINIT_PATH,
            user="root",
            group="root",
            mode="0644",
            sudo=True,
        )
        yield op(
            files.install,
            name=NOTIFY_UNIT,
            content=plan.unit_content,
            dest=UNIT_PATH,
            user="root",
            group="root",
            mode="0644",
            sudo=True,
        )
        yield op(
            files.file,
            name=NOTIFY_UNIT_ATTRS,
            path=UNIT_PATH,
            user="root",
            group="root",
            mode="0644",
            sudo=True,
        )
        if plan.vcl_content is not None:
            yield op(
                files.install,
                name=NOTIFY_VCL,
                content=plan.vcl_content,
                dest=plan.vcl_path,
                user="root",
                group="root",
                mode="0644",
                sudo=True,
            )
            yield op(
                files.file,
                name=NOTIFY_VCL_ATTRS,
                path=plan.vcl_path,
                user="root",
                group="root",
                mode="0644",
                sudo=True,
            )
        yield op(
            files.install,
            name=NOTIFY_SECRET,
            content=plan.secret_content,
            dest=plan.secret_path,
            user="root",
            group="root",
            mode="0644",
            sudo=True,
        )
        yield op(
            files.file,
            name=NOTIFY_SECRET_ATTRS,
            path=plan.secret_path,
            user="root",
            group="root",
            mode="0644",
            sudo=True,
        )

    return deploy


def services(plan: VarnishPlan) -> Deploy:
    """``varnish_enabled_services``, honored rather than dropped (D5).

    One ``systemd.service(running=True, enabled=True)`` request per listed unit --
    the state axes are fact-gated, so a converged host reports no change. These
    tasks carry no ``notify`` in the role, so none of their names enters
    :func:`notifying_names`.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for service in plan.enabled_services:
            yield op(
                systemd.service,
                name=f"Ensure {service} is started and enabled at boot",
                service=service,
                running=True,
                enabled=True,
                sudo=True,
            )

    return deploy


# --- the role ----------------------------------------------------------------


def varnish(v: VarsSet[VarnishVars]) -> Deploy:
    """Install and configure Varnish on Ubuntu hosts, and run the role's handler.

    Args:
        v: The role's resolved variables, one :class:`VarnishVars` per host
            (``resolve_vars(VarnishVars, "varnish", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs -- no
        other role consumes them -- so it returns ``None`` on every path,
        including the family guard's early return.

    Raises:
        ValueError: The distribution read carries no ``VERSION_CODENAME``, or a
            role variable is invalid. The variable errors arrive during
            :func:`~reemote.rolevars.resolve_vars`, before any host is contacted.
        ImportError: The ``reemote[templates]`` extra is missing. The render
            happens inside the body, right after the distribution barrier, so the
            failure arrives at the moment the consumer needs the extra.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        # Wave 0: the release codename for the repository's Suites and the
        # lower-cased distribution for its URL segment. The barrier closes
        # before the request wave is built, so every request below is a pure
        # function of the frozen plan.
        info = yield fact(LinuxDistribution)
        plan = varnish_plan(hv, info)

        # Wave 1: the role's whole task list, in its own order -- the vendor
        # repository and the packages, the unit directory and the four rendered
        # files, then the enabled services. No read sits between the stages, so
        # they share one wave in yield order.
        yield from setup(plan)(host)
        yield from configure(plan)(host)
        yield from services(plan)(host)
        configure_report = yield checkpoint("configure done")

        # Wave 2: the role's handlers as one request (D4). Every one of the
        # notifying names queues the restart, and ``daemon_reload=True`` folds
        # upstream's ``reload systemd`` into it -- the gate is state-based, so the
        # reload lands exactly while the loaded fragment is stale or outranked by
        # the override on disk (issue #75) and not otherwise. A converged host
        # appears in the report nowhere, so the wave disappears.
        notified = changed_hosts(configure_report, plan.notify_names)
        yield from restart_handler(notified, SERVICE, daemon_reload=True)(host)

    return deploy


__all__ = [
    "VarnishVars",
    "varnish",
]
