# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The Elasticsearch role (migrated from ``geerlingguy.elasticsearch``).
#
# Installs Elasticsearch on Ubuntu and RedHat hosts: on Debian the
# ``apt-transport-https``/``gnupg2`` dependencies, the Elasticsearch signing key
# downloaded into ``/etc/apt/trusted.gpg.d``, the ``artifacts.elastic.co`` apt
# repository (with its change-gated index refresh); on RedHat the ``rpm_key``
# import and the ``dnf.repo`` file of ``setup-RedHat.yml``. Family-neutral from
# there: the ``elasticsearch`` package, the rendered
# ``/etc/elasticsearch/elasticsearch.yml`` and the version-appropriate JVM heap
# file (root:elasticsearch, mode ``0660``, each with its metadata companion), the
# started/enabled service, and a TCP wait for the HTTP port; a config change
# queues the role's one handler, ``restart elasticsearch``. :func:`elasticsearch`
# is the whole task list and the handler, :class:`ElasticsearchVars` is
# ``defaults/main.yml``, bound from the inventory entry's ``elasticsearch`` role
# mapping by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` or ``"RedHat"`` OS family -- both
# ``setup-*.yml`` branches of the role are mapped, the ``include_tasks`` OS
# dispatch becoming the family branch inside :func:`install`. There is no
# Alpine branch in the role, so an off-target host returns before its first
# ``yield``, emitting no requests, no barriers and no log lines at all.
#
# The stream is two waves and one checkpoint on Ubuntu (the kibana shape, plus
# the tail the kibana role does not have):
#
# 0. :func:`install` then :func:`configure` -- the dependency install, the key
#    download, the repository (its ``update_cache`` refresh rides the change),
#    the package, and the two rendered config files with their metadata
#    companions -- closed by ``checkpoint("configure done")``.
# 1. :func:`handler` (``restart elasticsearch``, only on the notified hosts),
#    then :func:`start` -- the service task and the port wait, in that order
#    within the wave. Ansible ran ``meta: flush_handlers`` between the template
#    tasks and the service task, so the restart precedes the start on a changed
#    host and the ``when=``-gated version branch lives inside the plan, not on
#    the requests.
#
# On RedHat one ``control`` barrier leads, so the stream is three waves: the
# key fetch closes an empty wave 0 (the ``repo_epel`` shape), then the same
# install/configure requests and ``checkpoint("configure done")`` fill wave 1,
# and wave 2 is the same handler/service/wait tail.
#
# **No facts or acts at all**: nothing reads remote state between tasks -- the
# ``get_url``/``apt_repository`` pair and the ``rpm_key``/repo-template pair are
# operations whose own fact gates (content equality, repository identity,
# keyring membership) live inside their commands, and the version branch is
# decided by the :attr:`ElasticsearchVars.version` string alone. On Ubuntu the
# checkpoint is the entire barrier surface; the RedHat path adds only the
# control-side key fetch (D9), whose input is a frozen constant URL.
#
# Prerequisites: the config renders use Jinja2 (``{{ }}`` interpolation in
# ``templates/elasticsearch.yml.j2`` and the two heap templates), imported only
# through :mod:`reemote.roles._render` inside the render functions, so importing
# this module never requires it -- rendering needs the ``reemote[templates]``
# extra. Elasticsearch itself needs a Java runtime: the Ansible README defers
# that to ``geerlingguy.java``, and the migrated answer is to compose the
# shipped :func:`~reemote.roles.java.java` role in the parent deploy (D8) -- the
# role never installed Java itself, so neither does this one.
#
# Deviations from the Ansible role, all deliberate:
#
# * ``get_url force: true`` becomes a content-idempotent ``files.download``
#   (D1): Ansible re-fetched and re-wrote the key on every run; reemote fetches
#   the URL *control-side*, compares the bytes against the destination and
#   installs only on a real difference, so a converged pass writes nothing. The
#   key is deliberately not checksum-pinned -- Elastic rotates the artifact, a
#   pinned digest would fail the deploy on the first rotation.
# * ``apt_repository`` without a ``name``/``filename`` appended its line to
#   ``/etc/apt/sources.list``, and ``apt.repo`` without ``filename`` does
#   exactly the same (D2): unlike the kibana role's derived drop-in name, the
#   mapping here is faithful, and ``update_cache: true`` maps to the
#   change-gated ``apt.repo(update_cache=True)`` refresh.
# * ``ansible_managed`` is frozen to the literal ``Ansible managed`` (D3):
#   Ansible's ansible.cfg default is a *timestamped* string, which would
#   rewrite ``elasticsearch.yml`` on every run and fire the restart handler
#   forever (the redis/memcached precedent).
# * ``files.file`` metadata companions are added, and their names notify (D4):
#   ``files.install``'s idempotence is content-only, so a ``root:elasticsearch``
#   ``0660`` drift on unchanged content would never converge, while Ansible's
#   template module reports a change on owner/group/mode drift too (the
#   java/redis/kibana companion precedent). The ``jvm.options.d`` parent the
#   ``7.x`` destination needs is created by the elevated install's own
#   ``mkdir -p``, so no directory task exists in either role.
# * the ``elasticsearch_version[0] | int`` branch becomes a control-side
#   ``if`` over a parsed major (D5): :class:`ElasticsearchVars` rejects a
#   version whose first character is not a digit at ``resolve_vars`` time --
#   where Ansible's ``| int`` coerced such a string to ``0`` and silently took
#   the ``< 7`` branch -- and the parse stays faithful to the ``[0]`` index
#   (``"10.x"`` reads the major as ``1`` and still selects the ``< 7``
#   branch, preserving the role's own filter's quirk).
# * ``wait_for host=… port=… delay=3 timeout=300`` becomes
#   ``server.wait(host=…, port=…, timeout=300.0)`` (D6): there is no initial
#   ``delay`` knob -- the poll starts immediately at ``interval`` spacing, which
#   only makes the converged check earlier, never later. Because a wait is
#   stateless (it always reports changed), the wait's request name is
#   deliberately **absent** from :data:`NOTIFY_RESTART`: a converged pass still
#   logs the wait as changed, and it must not restart the service forever.
# * ``setup-RedHat.yml`` is migrated as the family branch inside
#   :func:`install` (D7): its ``rpm_key``/repo-template pair become
#   ``dnf.key``/``dnf.repo`` (D9, D10 below), and ``main.yml``'s generic
#   ``package`` task runs through ``dnf.packages`` on those hosts instead of
#   ``apt.packages``. The configure, service and wait tasks need no branch --
#   they were family-neutral in the role too.
# * the Java prerequisite stays with the caller (D8), as in the README.
# * Ansible's ``rpm_key: key=<URL>`` becomes a ``control`` fetch of the armored
#   key plus ``dnf.key(src=stream, keyid="d88e42b4")`` (D9): reemote's
#   ``dnf.key`` takes a control-side stream, not a URL the target reads -- the
#   ``repo_epel`` recipe. The pinned short id is the *first* key of the
#   published file (which actually carries two); it makes the import idempotent
#   through the ``RpmGpgKeys`` fact, and both keys still land in the rpm
#   keyring exactly as ``rpm --import`` put them there. Re-pinning is on the
#   operator if Elastic rotates the signing key: the role never checksum-pins
#   the artifact itself (D1).
# * ``template elasticsearch.repo.j2`` becomes ``dnf.repo``'s constructed file
#   (D10): ``dnf.repo`` couples the section id and the filename, so the file is
#   ``/etc/yum.repos.d/elasticsearch-<version>.repo`` where upstream wrote
#   ``elasticsearch.repo`` with an ``[elasticsearch-<version>]`` section -- the
#   section id, what ``dnf repolist`` shows, is preserved. The template's
#   ``type=rpm-md`` is dnf's default repo type and ``autorefresh=1`` a yum-era
#   option dnf ignores, so neither line is rendered; and no metadata refresh is
#   issued because the role's RedHat path ran none -- ``dnf`` picks the repo up
#   on its next transaction on its own.
# """

import io
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..handlers import changed_hosts, restart_handler
from ..hosts import Host
from ..modules import apt, dnf, files, server, systemd
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, checkpoint, control

# --- role constants (defaults/main.yml + tasks/setup-Debian.yml / setup-RedHat.yml) --

#: The families with a ``setup-*.yml`` branch in the role: both are migrated,
#: and any other family (there is no Alpine branch) is skipped whole by the
#: body's guard.
TARGET_FAMILIES = ("Ubuntu", "RedHat")

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
#: repository channel, exactly as the role's ``{{ elasticsearch_version }}``
#: interpolation, and the line lands in ``/etc/apt/sources.list`` (D2) -- the
#: file ``apt.repo`` manages when no ``filename`` is given, the same one the
#: unnamed ``apt_repository`` task appended to.
REPO_URL_BASE = "https://artifacts.elastic.co/packages"
SOURCES_PATH = "/etc/apt/sources.list"

#: The short id of the *first* key in the published armored file (full
#: fingerprint ``46095ACC8548582C1A2699A9D27D666CD88E42B4``; the file carries a
#: second key too and ``rpm --import`` records both). Pinned so the RedHat
#: ``dnf.key`` import is idempotent through the ``RpmGpgKeys`` fact (D9).
ELASTIC_KEY_ID = "d88e42b4"

#: The service unit the ``service`` task and the ``restart elasticsearch``
#: handler act on -- the role hard-codes the name, so it is a constant.
SERVICE = "elasticsearch"

#: The two ``template`` tasks' destinations. ``elasticsearch.yml`` is shared by
#: both version branches; the JVM file is ``jvm.options`` below 7 and the
#: drop-in ``jvm.options.d/heap.options`` from 7 on, per ``tasks/main.yml``.
CONF_PATH = "/etc/elasticsearch/elasticsearch.yml"
JVM_LEGACY_PATH = "/etc/elasticsearch/jvm.options"
HEAP_PATH = "/etc/elasticsearch/jvm.options.d/heap.options"

#: The template tasks' ``owner: root group: elasticsearch mode: 0660``: the
#: packaged unit runs as the ``elasticsearch`` user, and the group read bit is
#: how it gets its config.
CONF_MODE = "0660"
CONF_USER = "root"
CONF_GROUP = "elasticsearch"

#: ``wait_for: timeout=300`` (D6). The ``delay: 3`` has no reemote equivalent.
WAIT_TIMEOUT = 300.0

#: ``templates/elasticsearch.yml.j2``, verbatim except the frozen
#: ``ansible_managed`` header (D3). ``{{ }}`` interpolation only -- no guards
#: or loops -- but the literal ``${...}``/``%p`` free text in the sibling
#: templates is why the render stays Jinja2 rather than an f-string (which
#: would have to double every brace). Rendered control-side by
#: :func:`render_elasticsearch_yml` with Ansible's whitespace defaults.
ELASTICSEARCH_CONF_TEMPLATE = """\
# Ansible managed
# ======================== Elasticsearch Configuration =========================
#
# NOTE: Elasticsearch comes with reasonable defaults for most settings.
#       Before you set out to tweak and tune the configuration, make sure you
#       understand what are you trying to accomplish and the consequences.
#
# The primary way of configuring a node is via this file. This template lists
# the most important settings you may want to configure for a production cluster.
#
# Please consult the documentation for further information on configuration options:
# https://www.elastic.co/guide/en/elasticsearch/reference/index.html
#
# ---------------------------------- Cluster -----------------------------------
#
# Use a descriptive name for your cluster:
#
#cluster.name: my-application
#
# ------------------------------------ Node ------------------------------------
#
# Use a descriptive name for the node:
#
#node.name: node-1
#
# Add custom attributes to the node:
#
#node.attr.rack: r1
#
# ----------------------------------- Paths ------------------------------------
#
# Path to directory where to store the data (separate multiple locations by comma):
#
path.data: /var/lib/elasticsearch
#
# Path to log files:
#
path.logs: /var/log/elasticsearch
#
# ----------------------------------- Memory -----------------------------------
#
# Lock the memory on startup:
#
#bootstrap.memory_lock: true
#
# Make sure that the heap size is set to about half the memory available
# on the system and that the owner of the process is allowed to use this
# limit.
#
# Elasticsearch performs poorly when the system is swapping the memory.
#
# ---------------------------------- Network -----------------------------------
#
# Set the bind address to a specific IP (IPv4 or IPv6):
#
network.host: {{ elasticsearch_network_host }}
#
# Set a custom port for HTTP:
#
http.port: {{ elasticsearch_http_port }}
#
# For more information, consult the network module documentation.
#
# --------------------------------- Discovery ----------------------------------
#
# Pass an initial list of hosts to perform discovery when new node is started:
# The default list of hosts is ["127.0.0.1", "[::1]"]
#
#discovery.zen.ping.unicast.hosts: ["host1", "host2"]
#
# Prevent the "split brain" by configuring the majority of nodes (total number of master-eligible nodes / 2 + 1):
#
#discovery.zen.minimum_master_nodes:
#
# For more information, consult the zen discovery module documentation.
#
# ---------------------------------- Gateway -----------------------------------
#
# Block initial recovery after a full cluster restart until N nodes are started:
#
#gateway.recover_after_nodes: 3
#
# For more information, consult the gateway module documentation.
#
# ---------------------------------- Various -----------------------------------
#
# Require explicit names when deleting indices:
#
#action.destructive_requires_name: true

{{ elasticsearch_extra_options }}
"""

#: ``templates/heap.options.j2``, verbatim -- the ``>= 7`` JVM file. No
#: ``ansible_managed`` header in the upstream template, so none here either;
#: the two heap settings are the whole managed content.
HEAP_OPTIONS_TEMPLATE = """\
## JVM configuration

################################################################
## IMPORTANT: JVM heap size
################################################################
##
## You should always set the min and max JVM heap
## size to the same value. For example, to set
## the heap to 4 GB, set:
##
## -Xms4g
## -Xmx4g
##
## See https://www.elastic.co/guide/en/elasticsearch/reference/current/heap-size.html
## for more information
##
################################################################

# Xms represents the initial size of total heap space
# Xmx represents the maximum size of total heap space

-Xms{{ elasticsearch_heap_size_min }}
-Xmx{{ elasticsearch_heap_size_max }}
"""

#: ``templates/jvm.options.j2``, verbatim -- the ``< 7`` JVM file: the same two
#: heap lines plus the static expert settings. ``${ES_TMPDIR}``, ``%p`` and the
#: ``8-13:`` version-prefix lines are literal free text, untouched by Jinja2.
JVM_OPTIONS_TEMPLATE = """\
## JVM configuration

################################################################
## IMPORTANT: JVM heap size
################################################################
##
## You should always set the min and max JVM heap
## size to the same value. For example, to set
## the heap to 4 GB, set:
##
## -Xms4g
## -Xmx4g
##
## See https://www.elastic.co/guide/en/elasticsearch/reference/current/heap-size.html
## for more information
##
################################################################

# Xms represents the initial size of total heap space
# Xmx represents the maximum size of total heap space

-Xms{{ elasticsearch_heap_size_min }}
-Xmx{{ elasticsearch_heap_size_max }}

################################################################
## Expert settings
################################################################
##
## All settings below this section are considered
## expert settings. Don't tamper with them unless
## you understand what you are doing
##
################################################################

## GC configuration
8-13:-XX:+UseConcMarkSweepGC
8-13:-XX:CMSInitiatingOccupancyFraction=75
8-13:-XX:+UseCMSInitiatingOccupancyOnly

## G1GC Configuration
# NOTE: G1 GC is only supported on JDK version 10 or later
# to use G1GC, uncomment the next two lines and update the version on the
# following three lines to your version of the JDK
# 10-13:-XX:-UseConcMarkSweepGC
# 10-13:-XX:-UseCMSInitiatingOccupancyOnly
14-:-XX:+UseG1GC
14-:-XX:G1ReservePercent=25
14-:-XX:InitiatingHeapOccupancyPercent=30

## DNS cache policy
# cache ttl in seconds for positive DNS lookups noting that this overrides the
# JDK security property networkaddress.cache.ttl; set to -1 to cache forever
-Des.networkaddress.cache.ttl=60
# cache ttl in seconds for negative DNS lookups noting that this overrides the
# JDK security property networkaddress.cache.negative ttl; set to -1 to cache
# forever
-Des.networkaddress.cache.negative.ttl=10

## optimizations

# pre-touch memory pages used by the JVM during initialization
-XX:+AlwaysPreTouch

## basic

# explicitly set the stack size
-Xss1m

# set to headless, just in case
-Djava.awt.headless=true

# ensure UTF-8 encoding by default (e.g. filenames)
-Dfile.encoding=UTF-8

# use our provided JNA always versus the system one
-Djna.nosys=true

# turn off a JDK optimization that throws away stack traces for common
# exceptions because stack traces are important for debugging
-XX:-OmitStackTraceInFastThrow

# enable helpful NullPointerExceptions (https://openjdk.java.net/jeps/358), if
# they are supported
14-:-XX:+ShowCodeDetailsInExceptionMessages

# flags to configure Netty
-Dio.netty.noUnsafe=true
-Dio.netty.noKeySetOptimization=true
-Dio.netty.recycler.maxCapacityPerThread=0

# log4j 2
-Dlog4j.shutdownHookEnabled=false
-Dlog4j2.disable.jmx=true

# Fix for log4shell exploit
-Dlog4j2.formatMsgNoLookups=true

-Djava.io.tmpdir=${ES_TMPDIR}

## heap dumps

# generate a heap dump when an allocation from the Java heap fails
# heap dumps are created in the working directory of the JVM
-XX:+HeapDumpOnOutOfMemoryError

# specify an alternative path for heap dumps; ensure the directory exists and
# has sufficient space
-XX:HeapDumpPath=data

# specify an alternative path for JVM fatal error logs
-XX:ErrorFile=logs/hs_err_pid%p.log

## JDK 8 GC logging

8:-XX:+PrintGCDetails
8:-XX:+PrintGCDateStamps
8:-XX:+PrintTenuringDistribution
8:-XX:+PrintGCApplicationStoppedTime
8:-Xloggc:logs/gc.log
8:-XX:+UseGCLogFileRotation
8:-XX:NumberOfGCLogFiles=32
8:-XX:GCLogFileSize=64m

# JDK 9+ GC logging
9-:-Xlog:gc*,gc+age=trace,safepoint:file=logs/gc.log:utctime,pid,tags:filecount=32,filesize=64m
# due to internationalization enhancements in JDK 9 Elasticsearch need to set the provider to COMPAT otherwise
# time/date parsing will break in an incompatible way for some date patterns and locals
9-:-Djava.locale.providers=COMPAT

# temporary workaround for C2 bug with JDK 10 on hardware with AVX-512
10-:-XX:UseAVX=2
"""

#: The request names that queue ``restart elasticsearch`` -- the role's one
#: ``notify:`` channel, which sits on both template tasks (the version branch
#: only ever runs one of them, but both carry the notification). The metadata
#: companions join it because Ansible's template module reports a change on
#: owner/group/mode drift as well (D4). The dependency, key, repository,
#: package, service and **wait** tasks carry no ``notify`` in the role, so
#: their names are deliberately absent -- the wait especially, because it
#: always reports changed (D6).
NOTIFY_RESTART: frozenset[str] = frozenset(
    {
        "Configure Elasticsearch",
        "Set Elasticsearch configuration attributes",
        "Configure Elasticsearch JVM options",
        "Set Elasticsearch JVM options attributes",
    }
)


# --- variables ---------------------------------------------------------------


class ElasticsearchVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``elasticsearch`` role mapping, so ``version`` there sets
    :attr:`version`. Every Ansible default is carried over; the only renaming is
    the documented ``elasticsearch_*`` prefix drop.

    The model is frozen and hashable, so a value that reaches
    :func:`~reemote.core.op` hashes (design doc section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``elasticsearch_version``: the Elastic repository channel (``7.x``,
    #: ``6.x``...). It selects the repository line *and* the JVM file, exactly
    #: as the role's ``[0] | int`` gate did; the leading character must be a
    #: digit (D5). Pinning a concrete package version is :attr:`package`'s job.
    version: str = "7.x"
    #: ``elasticsearch_package``: the apt package name. The README documents
    #: pinning a version *through the name* (``elasticsearch=7.10.2``); an apt
    #: wildcard (``elasticsearch=7.10.*``) cannot be version-compared by
    #: ``apt.packages`` and re-installs every pass -- the README's own hint.
    package: str = "elasticsearch"
    #: ``elasticsearch_package_state``: what to do with the package.
    package_state: Literal["present", "latest"] = "present"
    #: ``elasticsearch_service_state``: the service verb. ``started``/
    #: ``stopped`` are fact-gated and converge; ``restarted``/``reloaded`` are
    #: stateless and always report a change, faithful to Ansible's service
    #: module.
    service_state: Literal["started", "stopped", "restarted", "reloaded"] = "started"
    #: ``elasticsearch_service_enabled``: whether the unit starts on boot.
    service_enabled: bool = True
    #: ``elasticsearch_network_host``: the ``network.host`` setting, and the
    #: address the final ``wait_for`` probes (``host={{ ... }}``).
    network_host: str = "localhost"
    #: ``elasticsearch_http_port``: the ``http.port`` setting and the wait's port.
    http_port: int = 9200
    #: ``elasticsearch_heap_size_min``: the ``-Xms`` argument.
    heap_size_min: str = "1g"
    #: ``elasticsearch_heap_size_max``: the ``-Xmx`` argument.
    heap_size_max: str = "2g"
    #: ``elasticsearch_extra_options``: raw lines appended to the config,
    #: exactly as the template's final ``{{ }}`` substitution does.
    extra_options: str = ""

    @field_validator("version")
    @classmethod
    def _version_leads_with_a_digit(cls, value: str) -> str:
        """``elasticsearch_version[0] | int`` needs a leading digit (D5)."""
        if not value or not value[0].isdigit():
            raise ValueError(
                "must start with the major-version digit (e.g. '7.x', '6.8.23')"
            )
        return value

    @field_validator("package", "network_host", "heap_size_min", "heap_size_max")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """An empty package/host/heap size is a typo, not "unset"."""
        if not value:
            raise ValueError("must be a non-empty string")
        return value


@dataclass(frozen=True)
class ElasticsearchPlan:
    """One host's resolved deploy target, built by the body.

    The role's resolved artifacts are the two rendered files -- the destination
    of the JVM one already decided by the version branch -- plus both families'
    repository forms (the apt line, and the ``dnf.repo`` section id, baseurl and
    description from ``elasticsearch.repo.j2``) and the flags the package,
    service and wait requests read; the key URL/path and the config modes are
    frozen constants. It replaces a per-host plan dict: under a per-host body
    the plan is a local, rendered after ``hv`` resolves and before the request
    wave is built.
    """

    config_content: str
    jvm_path: str
    jvm_content: str
    package: str
    package_state: str
    service_state: str
    enabled: bool
    network_host: str
    http_port: int
    repo_src: str
    repo_section: str
    repo_baseurl: str
    repo_description: str


# --- pure helpers ------------------------------------------------------------


def version_major(version: str) -> int:
    """``elasticsearch_version[0] | int``, reproduced faithfully (D5).

    Only the *first character* decides the branch, exactly as the role's Jinja
    expression did -- ``"6.8.23"`` and ``"6.x"`` both give ``6``, ``"7.x"``
    gives ``7``, and ``"10.x"`` gives ``1``, the same first-character quirk the
    role's ``[0]`` index had: a two-digit major still takes the ``< 7`` branch
    there and here. The leading-digit guarantee is a
    :class:`ElasticsearchVars` validator, so this never sees anything else.
    """
    return int(version[0])


def render_elasticsearch_yml(hv: ElasticsearchVars) -> str:
    """``templates/elasticsearch.yml.j2``, pre-rendered control-side (recipe 4).

    The jinja2 import is inside this function (through
    :func:`reemote.roles._render.jinja2_environment`) so that importing
    ``reemote.roles`` stays clean without the ``reemote[templates]`` extra
    (§17.4). The environment reproduces Ansible's whitespace handling
    byte-for-byte, and the context uses the role's original ``elasticsearch_*``
    names because the bundled template does. ``ansible_managed`` is the frozen
    literal (D3), already substituted in the template constant.
    """
    from ._render import jinja2_environment

    environment = jinja2_environment()
    template = environment.from_string(ELASTICSEARCH_CONF_TEMPLATE)
    return template.render(
        elasticsearch_network_host=hv.network_host,
        elasticsearch_http_port=hv.http_port,
        elasticsearch_extra_options=hv.extra_options,
    )


def render_jvm_options(hv: ElasticsearchVars) -> str:
    """The version-appropriate JVM template, pre-rendered control-side.

    ``tasks/main.yml`` runs one of the two ``template`` tasks -- the full
    ``jvm.options`` below 7, the ``heap.options`` drop-in from 7 on -- gated on
    ``elasticsearch_version[0] | int``; the same choice happens here over the
    parsed major (D5). Both templates interpolate only the two heap sizes, so
    one render covers both (their context is the same ``elasticsearch_heap_*``
    pair).
    """
    from ._render import jinja2_environment

    template_text = (
        JVM_OPTIONS_TEMPLATE if version_major(hv.version) < 7 else HEAP_OPTIONS_TEMPLATE
    )
    environment = jinja2_environment()
    return environment.from_string(template_text).render(
        elasticsearch_heap_size_min=hv.heap_size_min,
        elasticsearch_heap_size_max=hv.heap_size_max,
    )


def elasticsearch_plan(hv: ElasticsearchVars) -> ElasticsearchPlan:
    """Freeze the reads the deploy needs: the renders, the repo line, the flags.

    Control-side work inside the body, before the request wave is built: the
    Jinja2 templates become plain ``str``s (hashable, identical across waves)
    and the version string decides the JVM destination and becomes the concrete
    repository line and repo file (the ``{{ elasticsearch_version }}``
    interpolations of ``setup-Debian.yml`` and ``elasticsearch.repo.j2``).
    """
    return ElasticsearchPlan(
        config_content=render_elasticsearch_yml(hv),
        jvm_path=JVM_LEGACY_PATH if version_major(hv.version) < 7 else HEAP_PATH,
        jvm_content=render_jvm_options(hv),
        package=hv.package,
        package_state=hv.package_state,
        service_state=hv.service_state,
        enabled=hv.service_enabled,
        network_host=hv.network_host,
        http_port=hv.http_port,
        repo_src=f"deb {REPO_URL_BASE}/{hv.version}/apt stable main",
        repo_section=f"elasticsearch-{hv.version}",
        repo_baseurl=f"{REPO_URL_BASE}/{hv.version}/yum",
        repo_description=f"Elasticsearch repository for {hv.version} packages",
    )


# --- the deploy stages --------------------------------------------------------


def install(plan: ElasticsearchPlan) -> Deploy:
    """The family's ``setup-*.yml`` plus the ``Install Elasticsearch`` task.

    The plan is entirely static, so this stage is one pure request wave (the
    kibana precedent) on Ubuntu: the three Debian tasks run in the role's own
    order -- dependencies, key, repository -- and the generic ``package`` task
    that follows them in ``main.yml`` lands in the same wave, since nothing
    between them reads the machine. On RedHat the stage opens with the
    migration's one ``control`` barrier (D9) -- the key fetch feeding
    ``dnf.key`` -- then the repo file (D10) and the package; the barrier closes
    the empty wave 0 and these requests land in wave 1 with the configure
    stage. No setup or package task carries a ``notify`` in either family, so
    none of the names is in :data:`NOTIFY_RESTART`.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if host.family == "RedHat":
            # setup-RedHat.yml task 1: rpm_key of the armored Elastic key into
            # the rpm keyring (D9: control-side fetch, pinned short id so the
            # import converges through RpmGpgKeys).
            key_text = yield control(files.fetch_url, KEY_URL)
            yield op(
                dnf.key,
                name="Add Elasticsearch GPG key",
                src=io.BytesIO(key_text.encode("utf-8")),
                keyid=ELASTIC_KEY_ID,
                sudo=True,
            )
            # setup-RedHat.yml task 2: elasticsearch.repo.j2 becomes dnf.repo's
            # constructed file (D10): the version-selected section id, baseurl
            # and name, gpgcheck on and the key URL named, exactly the
            # template's lines that dnf still honours.
            yield op(
                dnf.repo,
                name="Add Elasticsearch repository",
                src=plan.repo_section,
                baseurl=plan.repo_baseurl,
                description=plan.repo_description,
                gpgkey=KEY_URL,
                sudo=True,
            )
        else:
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
            # setup-Debian.yml task 3: apt_repository ... update_cache=true, with no
            # name/filename -- faithfully the same file apt.repo manages when
            # filename is None, /etc/apt/sources.list (D2); the refresh is
            # change-gated inside the operation (issue #14).
            yield op(
                apt.repo,
                name="Add Elasticsearch repository",
                src=plan.repo_src,
                update_cache=True,
                sudo=True,
            )
        # main.yml: package name={{ elasticsearch_package }} state={{ ... }}.
        # present/latest map onto the two flags; the Debian task carries no
        # update because the repository request already refreshed the cache,
        # and the RedHat one because upstream's dnf path ran no refresh (D10).
        # A version pin rides the package string on both families.
        yield op(
            dnf.packages if host.family == "RedHat" else apt.packages,
            name="Install Elasticsearch",
            packages=(plan.package,),
            present=True,
            latest=plan.package_state == "latest",
            sudo=True,
        )

    return deploy


def configure(plan: ElasticsearchPlan) -> Deploy:
    """The ``template`` tasks, in the role's per-file order.

    One install/companion pair per managed file: ``elasticsearch.yml`` and the
    version-selected JVM file (the ``with_items`` loop's two destinations
    became the plan's two paths). The files are ``root:elasticsearch`` mode
    ``0660`` -- the role's own template task arguments, kept verbatim: the
    packaged unit runs as the ``elasticsearch`` user and reads them through the
    group bit. The elevated install ``mkdir -p``s the destination's parent, so
    the ``7.x`` drop-in directory needs no task of its own (D4). All four
    names notify: the two installs because the role's ``notify:`` sits on both
    template tasks, the two companions because Ansible's template module
    reports a change on owner/group/mode drift as well.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        # template elasticsearch.yml.j2 -> /etc/elasticsearch/elasticsearch.yml,
        # pre-rendered control-side (recipe 4). files.install's idempotence is
        # content-only: a converged file yields no commands even if its
        # metadata drifted.
        yield op(
            files.install,
            name="Configure Elasticsearch",
            content=plan.config_content,
            dest=CONF_PATH,
            user=CONF_USER,
            group=CONF_GROUP,
            mode=CONF_MODE,
            sudo=True,
        )
        # The paired files.file converges the root:elasticsearch 0660 metadata
        # the template task enforces on every run (D4).
        yield op(
            files.file,
            name="Set Elasticsearch configuration attributes",
            path=CONF_PATH,
            user=CONF_USER,
            group=CONF_GROUP,
            mode=CONF_MODE,
            sudo=True,
        )
        # The version branch's second template task: jvm.options (< 7) or
        # jvm.options.d/heap.options (>= 7), destination decided in the plan.
        yield op(
            files.install,
            name="Configure Elasticsearch JVM options",
            content=plan.jvm_content,
            dest=plan.jvm_path,
            user=CONF_USER,
            group=CONF_GROUP,
            mode=CONF_MODE,
            sudo=True,
        )
        yield op(
            files.file,
            name="Set Elasticsearch JVM options attributes",
            path=plan.jvm_path,
            user=CONF_USER,
            group=CONF_GROUP,
            mode=CONF_MODE,
            sudo=True,
        )

    return deploy


def handler(notified: frozenset[str]) -> Deploy:
    """``handlers/main.yml``: ``restart elasticsearch`` (the role's one handler).

    ``restart_handler`` closes over the notification set the checkpoint read
    produced and branches on ``host.name`` itself, so an unnotified host yields
    nothing and an empty set is the converged no-op. ``restarted=True`` restarts
    a running unit and starts a stopped one -- Ansible's ``state: restarted``
    -- and the request keeps the factory's default label
    ``Restart elasticsearch (handler)``.
    """
    return restart_handler(notified, SERVICE)


def start(plan: ElasticsearchPlan) -> Deploy:
    """The ``Start Elasticsearch`` and ``wait_for`` tasks, in that order.

    Ansible ran them after ``meta: flush_handlers``, so the stream places them
    after the handler in the same post-checkpoint wave -- the restart, then the
    state enforcement, then the readiness check, in yield order. The
    ``started``/``stopped`` axes of ``systemd.service`` are fact-gated and
    converge; ``restarted``/``reloaded`` are stateless, faithful to Ansible's
    service module. ``server.wait`` gained the ``host=`` variant for exactly
    this task (issue #56): ``"localhost"`` (the default) checks the listening
    ``Port`` on the target, any other address probes that ``TcpEndpoint`` from
    it; the wait always reports changed, which is why :func:`configure`'s
    checkpoint never sees its name (D6).
    """
    state = plan.service_state

    def deploy(host: Host) -> Generator[StageItem, Any]:
        # main.yml: service name=elasticsearch state={{ ... }}
        # enabled={{ elasticsearch_service_enabled }}.
        yield op(
            systemd.service,
            name="Start Elasticsearch",
            service=SERVICE,
            running=state != "stopped",
            restarted=state == "restarted",
            reloaded=state == "reloaded",
            enabled=plan.enabled,
            sudo=True,
        )
        # main.yml: wait_for host={{ elasticsearch_network_host }}
        # port={{ elasticsearch_http_port }} timeout=300 (D6: no delay knob).
        yield op(
            server.wait,
            name="Wait for Elasticsearch to accept connections",
            host=plan.network_host,
            port=plan.http_port,
            timeout=WAIT_TIMEOUT,
            sudo=True,
        )

    return deploy


# --- the role ----------------------------------------------------------------


def elasticsearch(v: VarsSet[ElasticsearchVars]) -> Deploy:
    """Install and configure Elasticsearch on Ubuntu and RedHat hosts, and run its handler.

    Args:
        v: The role's resolved variables, one :class:`ElasticsearchVars` per
            host (``resolve_vars(ElasticsearchVars, "elasticsearch",
            inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs -- no
        other role consumes them -- so it returns ``None`` on every path,
        including the family guard's early return.

    Raises:
        ImportError: The ``reemote[templates]`` extra is missing. The renders
            happen inside the body, while the first request wave is built, so
            the failure arrives at the moment the consumer needs the extra.
            Otherwise the role has no in-body error path -- no barrier to
            misread, no version to gate on beyond the validated
            :attr:`~ElasticsearchVars.version` -- and bad *variables* are
            rejected earlier, by :func:`~reemote.rolevars.resolve_vars`.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family not in TARGET_FAMILIES:
            return

        # The Debian setup (or, on RedHat, the control key fetch that closes
        # an empty wave 0 first), the package and the rendered config -- static
        # plan, no fact barriers -- closed by the role's only checkpoint. The
        # checkpoint must immediately follow the stage it reports on; the
        # handler wave's entries would be invisible to a second checkpoint,
        # and nothing after the restart reads a report.
        plan = elasticsearch_plan(hv)
        yield from install(plan)(host)
        yield from configure(plan)(host)
        wave_report = yield checkpoint("configure done")

        # Wave 1: meta: flush_handlers first -- the restart, only on the hosts
        # this checkpoint selected -- then the service task and the port wait,
        # in the role's own post-flush order.
        notified = changed_hosts(wave_report, NOTIFY_RESTART)
        yield from handler(notified)(host)
        yield from start(plan)(host)

    return deploy


__all__ = [
    "ElasticsearchVars",
    "elasticsearch",
]
