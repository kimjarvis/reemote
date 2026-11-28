# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The Kibana role (migrated from ``geerlingguy.kibana``).
#
# Installs Kibana on Ubuntu hosts: the ``apt-transport-https``/``gnupg2``
# dependencies, the Elasticsearch signing key downloaded into
# ``/etc/apt/trusted.gpg.d``, the ``artifacts.elastic.co`` apt repository (with
# its change-gated index refresh), the ``kibana`` package, the started/enabled
# service, and the rendered ``/etc/kibana/kibana.yml``; a config change queues
# the role's one handler, ``restart kibana``. :func:`kibana` is the whole task
# list and the handler, :class:`KibanaVars` is ``defaults/main.yml``, bound from
# the inventory entry's ``kibana`` role mapping by
# :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. The role's RedHat branch
# (``setup-RedHat.yml``'s ``rpm_key`` and its ``kibana.repo.j2`` yum template)
# is out of scope, so the ``include_tasks`` OS dispatch collapses to the
# Debian request set below and an off-target host returns before its first
# ``yield``, emitting no requests, no barriers and no log lines at all.
#
# The stream is two waves and one checkpoint (the redis/memcached shape):
#
# 0. :func:`configure` -- the dependency install, the key download, the
#    repository (its ``update_cache`` refresh rides the change), the package,
#    the started/enabled service, and the rendered config plus its metadata
#    companion -- closed by ``checkpoint("configure done")``.
# 1. :func:`handler` -- ``restart kibana``, only on the hosts that checkpoint
#    selected. An empty notification set yields no request, the converged no-op
#    Ansible's handler gives for free.
#
# **No facts, acts or controls at all**: nothing in the role's Debian path
# reads remote state between tasks -- the ``get_url``/``apt_repository`` pair
# are operations whose own fact gates (content equality, repository identity)
# live inside their commands, so no barrier is needed to feed them. The
# plan is entirely static: the version string selects the repository line and
# the template's ``elasticsearch.url``/``hosts`` branch, exactly as Ansible's
# two ``{% if %}`` guards did, and the render happens control-side inside the
# body.
#
# Prerequisites: the config render uses Jinja2 (the ``{% if %}`` guards of
# ``templates/kibana.yml.j2``), imported only through
# :mod:`reemote.roles._render` inside :func:`render_kibana_conf`, so importing
# this module never requires it -- rendering the config needs the
# ``reemote[templates]`` extra.
#
# Deviations from the Ansible role, all deliberate:
#
# * ``get_url force: true`` becomes a content-idempotent ``files.download``
#   (D1): Ansible re-fetched and re-wrote the key on every run; reemote fetches
#   the URL *control-side*, compares the bytes against the destination and
#   installs only on a real difference, so a converged pass writes nothing. The
#   key is deliberately not checksum-pinned -- Elastic rotates the artifact, a
#   pinned digest would fail the deploy on the first rotation.
# * ``apt_repository``'s derived ``sources.list.d`` file name becomes an
#   explicit one (D2): the module wrote the line into a file named after a
#   mangling of the repo string; the role passes ``filename="kibana-<version>"``
#   so the drop-in is stable and self-describing. ``update_cache: true`` maps
#   to ``apt.repo(update_cache=True)``, whose refresh is change-gated -- a
#   converged repository never runs ``apt-get update``.
# * a ``files.file`` metadata companion is added, and its name notifies (D3):
#   ``files.install``'s idempotence is content-only, so an owner/mode ``0644``
#   drift on unchanged content would never converge, while Ansible's template
#   module reports a change on owner/group/mode drift too (the java/redis
#   companion precedent).
# * ``kibana_config_template`` keeps its swap knob in controller-side form
#   (D4): the literal ``"kibana.yml.j2"`` renders the bundled template text
#   below; any other value is a *controller* filesystem path read and rendered
#   there (the apache vhosts-template precedent). Ansible read it from the
#   role's own ``templates/`` directory on the controller, which is what this
#   preserves.
# * the RedHat path is scoped out (D5): a Phase 0 single-OS decision. Its
#   ``kibana_version`` still drives the Ubuntu repository line and template
#   branch, so nothing is silently dropped from the variable surface.
# * type gates move to :class:`KibanaVars` and the family guard to the body's
#   opening ``if`` (D6): a wrongly typed variable is a pydantic rejection at
#   ``resolve_vars`` time, before any host is contacted, not a render-time
#   surprise. Pydantic's lax mode still coerces a numeric string
#   (``"5601"``) into the :class:`int` port field -- the repo-wide convention
#   (redis' ``port`` behaves the same) -- and the render is identical either
#   way.
# """

from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..handlers import changed_hosts, restart_handler
from ..hosts import Host
from ..modules import apt, files, systemd
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, checkpoint

# --- role constants (defaults/main.yml + tasks/setup-Debian.yml, Ubuntu path) --

#: ``setup-Debian.yml``'s ``apt: name=[apt-transport-https, gnupg2]``: the
#: https-transport and the ``gpg`` the Elastic key handling needs. One request.
DEPENDENCIES = ("apt-transport-https", "gnupg2")

#: ``setup-Debian.yml``'s ``get_url: url=`` -- the armored Elasticsearch signing
#: key, fetched control-side and installed verbatim (D1).
KEY_URL = "https://artifacts.elastic.co/GPG-KEY-elasticsearch"

#: The ``get_url: dest=``: the role drops the *armored* key into apt's legacy
#: trusted directory (``.asc`` there is read as armored), so
#: ``files.download``'s text path preserves it byte-for-byte.
KEY_PATH = "/etc/apt/trusted.gpg.d/elasticsearch.asc"
KEY_MODE = "0644"

#: ``apt_repository: repo=``'s URL base; the version string selects the
#: repository, exactly as the role's ``{{ kibana_version }}`` interpolation.
REPO_URL_BASE = "https://artifacts.elastic.co/packages"

#: The service unit the ``service`` task and the ``restart kibana`` handler
#: act on -- the role hard-codes the name, so it is a constant, not a variable.
SERVICE = "kibana"

#: ``defaults/main.yml``'s ``kibana_config_template`` sentinel: that literal
#: renders :data:`KIBANA_CONF_TEMPLATE`; anything else is a controller-side
#: template path read by :func:`render_kibana_conf` (D4, the apache precedent).
BUNDLED_TEMPLATE = "kibana.yml.j2"

#: ``templates/kibana.yml.j2``, verbatim. Rendered control-side by
#: :func:`render_kibana_conf`; only ``{% if %}`` guards appear, and
#: :func:`~reemote.roles._render.jinja2_environment` reproduces Ansible's
#: whitespace defaults, so the output matches the role's. The context keeps the
#: original ``kibana_*`` names because the bundled template does.
KIBANA_CONF_TEMPLATE = """\
# Kibana is served by a back end server. This setting specifies the port to use.
server.port: {{ kibana_server_port }}

# Specifies the address to which the Kibana server will bind. IP addresses and host names are both valid values.
# The default is 'localhost', which usually means remote machines will not be able to connect.
# To allow connections from remote users, set this parameter to a non-loopback address.
server.host: "{{ kibana_server_host }}"

# Enables you to specify a path to mount Kibana at if you are running behind a proxy.
# Use the `server.rewriteBasePath` setting to tell Kibana if it should remove the basePath
# from requests it receives, and to prevent a deprecation warning at startup.
# This setting cannot end in a slash.
#server.basePath: ""

# Specifies whether Kibana should rewrite requests that are prefixed with
# `server.basePath` or require that they are rewritten by your reverse proxy.
# This setting was effectively always `false` before Kibana 6.3 and will
# default to `true` starting in Kibana 7.0.
#server.rewriteBasePath: false

# The maximum payload size in bytes for incoming server requests.
#server.maxPayloadBytes: 1048576

# The Kibana server's name.  This is used for display purposes.
#server.name: "your-hostname"

# The URL of the Elasticsearch instance to use for all your queries.
{% if kibana_version == "6.x" %}
elasticsearch.url: {{ kibana_elasticsearch_url }}
{% else %}
elasticsearch.hosts: {{ kibana_elasticsearch_url }}
{% endif %}

# When this setting's value is true Kibana uses the hostname specified in the server.host
# setting. When the value of this setting is false, Kibana uses the hostname of the host
# that connects to this Kibana instance.
#elasticsearch.preserveHost: true

# Kibana uses an index in Elasticsearch to store saved searches, visualizations and
# dashboards. Kibana creates a new index if the index doesn't already exist.
#kibana.index: ".kibana"

# The default application to load.
#kibana.defaultAppId: "home"

# If your Elasticsearch is protected with basic authentication, these settings provide
# the username and password that the Kibana server uses to perform maintenance on the Kibana
# index at startup. Your Kibana users still need to authenticate with Elasticsearch, which
# is proxied through the Kibana server.
{% if kibana_elasticsearch_username and kibana_elasticsearch_password %}
elasticsearch.username: "{{ kibana_elasticsearch_username }}"
elasticsearch.password: "{{ kibana_elasticsearch_password }}"
{% else %}
#elasticsearch.username: "user"
#elasticsearch.password: "pass"
{% endif %}

# Enables SSL and paths to the PEM-format SSL certificate and SSL key files, respectively.
# These settings enable SSL for outgoing requests from the Kibana server to the browser.
#server.ssl.enabled: false
#server.ssl.certificate: /path/to/your/server.crt
#server.ssl.key: /path/to/your/server.key

# Optional settings that provide the paths to the PEM-format SSL certificate and key files.
# These files validate that your Elasticsearch backend uses the same key files.
#elasticsearch.ssl.certificate: /path/to/your/client.crt
#elasticsearch.ssl.key: /path/to/your/client.key

# Optional setting that enables you to specify a path to the PEM file for the certificate
# authority for your Elasticsearch instance.
#elasticsearch.ssl.certificateAuthorities: [ "/path/to/your/CA.pem" ]

# To disregard the validity of SSL certificates, change this setting's value to 'none'.
#elasticsearch.ssl.verificationMode: full

# Time in milliseconds to wait for Elasticsearch to respond to pings. Defaults to the value of
# the elasticsearch.requestTimeout setting.
#elasticsearch.pingTimeout: 1500

# Time in milliseconds to wait for responses from the back end or Elasticsearch. This value
# must be a positive integer.
#elasticsearch.requestTimeout: 30000

# List of Kibana client-side headers to send to Elasticsearch. To send *no* client-side
# headers, set this value to [] (an empty list).
#elasticsearch.requestHeadersWhitelist: [ authorization ]

# Header names and values that are sent to Elasticsearch. Any custom headers cannot be overwritten
# by client-side headers, regardless of the elasticsearch.requestHeadersWhitelist configuration.
#elasticsearch.customHeaders: {}

# Time in milliseconds for Elasticsearch to wait for responses from shards. Set to 0 to disable.
#elasticsearch.shardTimeout: 30000

# Time in milliseconds to wait for Elasticsearch at Kibana startup before retrying.
#elasticsearch.startupTimeout: 5000

# Logs queries sent to Elasticsearch. Requires logging.verbose set to true.
#elasticsearch.logQueries: false

# Specifies the path where Kibana creates the process ID file.
#pid.file: /var/run/kibana.pid

# Enables you specify a file where Kibana stores log output.
#logging.dest: stdout

# Set the value of this setting to true to suppress all logging output.
#logging.silent: false

# Set the value of this setting to true to suppress all logging output other than error messages.
#logging.quiet: false

# Set the value of this setting to true to log all events, including system usage information
# and all requests.
#logging.verbose: false

# Set the interval in milliseconds to sample system and process performance
# metrics. Minimum is 100ms. Defaults to 5000.
#ops.interval: 5000

# The default locale. This locale can be used in certain circumstances to substitute any missing
# translations.
#i18n.defaultLocale: "en"
"""

#: The request names that queue ``restart kibana`` -- the role's one ``notify:``
#: channel, which sits on the template task. The metadata companion joins it
#: because Ansible's template module reports a change on owner/group/mode drift
#: as well (D3). The dependency, key, repository, package and service tasks
#: carry no ``notify`` in the role, so their names are deliberately absent.
NOTIFY_RESTART: frozenset[str] = frozenset(
    {
        "Copy Kibana configuration",
        "Set Kibana configuration attributes",
    }
)


# --- variables ---------------------------------------------------------------


class KibanaVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``kibana`` role mapping, so ``server_port`` there sets
    :attr:`server_port`. Every Ansible default is carried over; the only
    renaming is the documented ``kibana_*`` prefix drop.

    The model is frozen and hashable, so a value that reaches
    :func:`~reemote.core.op` hashes (design doc section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``kibana_version``: the Elastic repository channel (``7.x``, ``8.x``,
    #: ``6.x``...). It selects the repository line *and* the template's
    #: ``elasticsearch.url``/``elasticsearch.hosts`` branch, exactly as in the
    #: role. Pinning a concrete package version is :attr:`package`'s job.
    version: str = "7.x"
    #: ``kibana_package``: the apt package name. The README documents pinning a
    #: version *through the name* (``kibana=7.10.2``); an exact pin converges,
    #: an apt wildcard (``kibana=7.10.*``) cannot be version-compared by
    #: ``apt.packages`` and re-installs every pass -- the README's own hint.
    package: str = "kibana"
    #: ``kibana_package_state``: what to do with the package. ``absent`` maps
    #: to ``apt.packages(present=False)``; the service task still runs, faithful
    #: to the role (which never gates it on the package state).
    package_state: Literal["present", "latest", "absent"] = "present"
    #: ``kibana_service_state``: the service verb. ``started``/``stopped`` are
    #: fact-gated and converge; ``restarted``/``reloaded`` are stateless and
    #: always report a change, faithful to Ansible's service module.
    service_state: Literal["started", "stopped", "restarted", "reloaded"] = "started"
    #: ``kibana_service_enabled``: whether the unit starts on boot.
    enabled: bool = True
    #: ``kibana_config_template``: the bundled sentinel renders the embedded
    #: template; any other value is a controller-side path (D4).
    config_template: str = BUNDLED_TEMPLATE
    #: ``kibana_config_file_path``: where the rendered config is installed.
    config_file_path: str = "/etc/kibana/kibana.yml"
    #: ``kibana_server_port``: the ``server.port`` setting.
    server_port: int = 5601
    #: ``kibana_server_host``: the ``server.host`` bind address.
    server_host: str = "0.0.0.0"
    #: ``kibana_elasticsearch_url``: the backend URL (``elasticsearch.hosts``,
    #: or ``elasticsearch.url`` on the ``6.x`` channel).
    elasticsearch_url: str = "http://localhost:9200"
    #: ``kibana_elasticsearch_username``: empty renders no auth pair, exactly as
    #: the template's ``{% if %}`` falsiness gate does.
    elasticsearch_username: str = ""
    #: ``kibana_elasticsearch_password``: both must be non-empty for the pair
    #: to render, as the same ``{% if %}`` requires.
    elasticsearch_password: str = ""

    @field_validator("version", "package", "config_file_path", "server_host", "elasticsearch_url")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """An empty channel/package/path/host/url is a typo, not "unset"."""
        if not value:
            raise ValueError("must be a non-empty string")
        return value


@dataclass(frozen=True)
class KibanaPlan:
    """One host's resolved deploy target, built by the body.

    The role's resolved artifacts are the rendered configuration text plus the
    repository line and its file name; the key URL/path and the service name
    are frozen constants. It replaces a per-host plan dict: under a per-host
    body the plan is a local, rendered after ``hv`` resolves and before the
    request wave is built.
    """

    config_content: str
    config_file_path: str
    package: str
    package_state: str
    service_state: str
    enabled: bool
    repo_src: str
    repo_filename: str


# --- pure helpers ------------------------------------------------------------


def render_kibana_conf(hv: KibanaVars) -> str:
    """``templates/kibana.yml.j2``, pre-rendered control-side (recipe 4).

    The environment reproduces Ansible's whitespace handling byte-for-byte
    (``trim_blocks=True``, ``lstrip_blocks=False``, ``keep_trailing_newline``,
    ``StrictUndefined``) and the context carries the role's original
    ``kibana_*`` names, so the two ``{% if %}`` guards decide
    ``elasticsearch.url`` vs ``elasticsearch.hosts`` and the basic-auth pair
    exactly as the role's did. A custom :attr:`KibanaVars.config_template` is
    a controller-side path, read here rather than on the host; the bundled
    template needs no filesystem read (D4). The jinja2 import is inside this
    function so that importing ``reemote.roles`` stays clean without the
    ``reemote[templates]`` extra (§17.4).
    """
    from ._render import jinja2_environment

    template_text = (
        KIBANA_CONF_TEMPLATE
        if hv.config_template == BUNDLED_TEMPLATE
        else Path(hv.config_template).read_text(encoding="utf-8")
    )
    environment = jinja2_environment()
    template = environment.from_string(template_text)
    return template.render(
        kibana_version=hv.version,
        kibana_server_port=hv.server_port,
        kibana_server_host=hv.server_host,
        kibana_elasticsearch_url=hv.elasticsearch_url,
        kibana_elasticsearch_username=hv.elasticsearch_username,
        kibana_elasticsearch_password=hv.elasticsearch_password,
    )


def kibana_plan(hv: KibanaVars) -> KibanaPlan:
    """Freeze the reads the deploy needs: the render, the repo line, the flags.

    Control-side work inside the body, before the request wave is built: the
    Jinja2 template becomes a plain ``str`` (hashable, identical across waves)
    and the version string becomes the concrete repository line and drop-in
    name (D2).
    """
    return KibanaPlan(
        config_content=render_kibana_conf(hv),
        config_file_path=hv.config_file_path,
        package=hv.package,
        package_state=hv.package_state,
        service_state=hv.service_state,
        enabled=hv.enabled,
        repo_src=f"deb {REPO_URL_BASE}/{hv.version}/apt stable main",
        repo_filename=f"kibana-{hv.version}",
    )


# --- the deploy stages --------------------------------------------------------


def configure(plan: KibanaPlan) -> Deploy:
    """``tasks/setup-Debian.yml`` plus ``tasks/main.yml``, in the role's order.

    Branch-free: the plan is entirely static, so this is one pure request wave
    (the redis precedent). The order is the role's own -- dependencies, key,
    repository, package, service, config -- and every task lands on a shipped
    operation. The notifying requests carry the stable names
    :data:`NOTIFY_RESTART` enumerates; the other five tasks carry no ``notify``
    in the role, so their names are deliberately absent from the set.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        # setup-Debian.yml task 1: apt name=[apt-transport-https, gnupg2].
        yield op(
            apt.packages,
            name="Ensure dependencies are installed",
            packages=DEPENDENCIES,
            sudo=True,
        )
        # setup-Debian.yml task 2: get_url of the armored Elastic key into
        # trusted.gpg.d (D1: content-idempotent, control-side fetch; the key
        # needs a login-readable 0644 so its read-back converges).
        yield op(
            files.download,
            name="Add Elasticsearch apt key",
            url=KEY_URL,
            dest=KEY_PATH,
            mode=KEY_MODE,
            sudo=True,
        )
        # setup-Debian.yml task 3: apt_repository ... update_cache=true. The
        # explicit drop-in name replaces the module's derived one (D2); the
        # refresh is change-gated inside the operation.
        yield op(
            apt.repo,
            name="Add Kibana repository",
            src=plan.repo_src,
            filename=plan.repo_filename,
            update_cache=True,
            sudo=True,
        )
        # main.yml: package name={{ kibana_package }} state={{ ... }}. present/
        # latest/absent map onto the two flags; the task carries no update
        # because the repository request already refreshed the cache.
        yield op(
            apt.packages,
            name="Install Kibana",
            packages=(plan.package,),
            present=plan.package_state != "absent",
            latest=plan.package_state == "latest",
            sudo=True,
        )
        # main.yml: service name=kibana state={{ kibana_service_state }}
        # enabled={{ kibana_service_enabled }}. The started/stopped axes are
        # fact-gated and converge; restarted/reloaded are stateless, faithful
        # to Ansible's service module. No notify in the role.
        state = plan.service_state
        yield op(
            systemd.service,
            name="Ensure Kibana is started and enabled at boot",
            service=SERVICE,
            running=state != "stopped",
            restarted=state == "restarted",
            reloaded=state == "reloaded",
            enabled=plan.enabled,
            sudo=True,
        )
        # main.yml: template kibana.yml.j2 (owner/group root, mode 0644, notify
        # restart kibana), pre-rendered control-side (recipe 4). The packaged
        # unit reads world-readable config, so root:root 0644 is faithful and
        # needs no redis-style ownership fix.
        yield op(
            files.install,
            name="Copy Kibana configuration",
            content=plan.config_content,
            dest=plan.config_file_path,
            user="root",
            group="root",
            mode="0644",
            sudo=True,
        )
        # The paired files.file converges the root:root 0644 metadata the
        # template task enforces on every run (D3). It notifies too, because
        # Ansible's template reports a change on owner/group/mode drift as well.
        yield op(
            files.file,
            name="Set Kibana configuration attributes",
            path=plan.config_file_path,
            user="root",
            group="root",
            mode="0644",
            sudo=True,
        )

    return deploy


def handler(notified: frozenset[str]) -> Deploy:
    """``handlers/main.yml``: ``restart kibana`` (the role's one handler).

    ``restart_handler`` closes over the notification set the checkpoint read
    produced and branches on ``host.name`` itself, so an unnotified host yields
    nothing and an empty set is the converged no-op. ``restarted=True`` restarts
    a running unit and starts a stopped one -- Ansible's ``state: restarted``
    -- and the request keeps the factory's default label
    ``Restart kibana (handler)``.
    """
    return restart_handler(notified, SERVICE)


# --- the role ----------------------------------------------------------------


def kibana(v: VarsSet[KibanaVars]) -> Deploy:
    """Install and configure Kibana on Ubuntu hosts, and run its handler.

    Args:
        v: The role's resolved variables, one :class:`KibanaVars` per host
            (``resolve_vars(KibanaVars, "kibana", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs -- no
        other role consumes them -- so it returns ``None`` on every path,
        including the family guard's early return.

    Raises:
        ImportError: The ``reemote[templates]`` extra is missing. The render
            happens inside the body, while the first request wave is built, so
            the failure arrives at the moment the consumer needs the extra.
            Otherwise the role has no in-body error path -- no barrier to
            misread, no version to gate on -- and bad *variables* are rejected
            earlier, by :func:`~reemote.rolevars.resolve_vars`.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        # Wave 0: the whole Debian task list -- static plan, no fact barriers --
        # closed by the role's only barrier. The checkpoint must immediately
        # follow the stage it reports on; the handler wave's entries would be
        # invisible to a second checkpoint, and nothing here needs one.
        yield from configure(kibana_plan(hv))(host)
        wave_report = yield checkpoint("configure done")

        # Wave 1: the handler, only on the hosts this checkpoint selected.
        notified = changed_hosts(wave_report, NOTIFY_RESTART)
        yield from handler(notified)(host)

    return deploy


__all__ = [
    "KibanaVars",
    "kibana",
]
