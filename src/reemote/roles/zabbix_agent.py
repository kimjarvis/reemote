# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The Zabbix agent role (migrated from ``ansible-zabbix-agent``).
#
# Installs and configures the Zabbix 4.x agent on Ubuntu hosts: the vendor apt
# keyring and repository lines (with the old-source purge), the agent packages,
# the optional apt pin, the SELinux permissive gate, the rendered
# ``/etc/zabbix/zabbix_agentd.conf``, the TLS-PSK file when asked for, the
# include directory, the service's enabled/running state, and the role's single
# ``restart zabbix-agent`` handler. :func:`zabbix_agent` is the whole task list
# and :class:`ZabbixAgentVars` is ``defaults/main.yml`` plus the frozen
# ``vars/zabbix.yml`` signing-key table, bound from the inventory entry's
# ``zabbix_agent`` role mapping by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. The role's Debian, Linux Mint,
# SUSE, RedHat, XCP-ng, Sangoma, Windows and macOS branches are out of scope, so
# its ``include_vars``/``include_tasks`` OS dispatch collapses to the frozen
# Debian constants below and an off-target host returns at the family guard.
#
# :func:`zabbix_agent` is an **async** factory (the corpus's second, after
# ``datadog``): the signing-key download is one inventory-wide constant URL, so
# it is fetched once here -- even under ``dry`` -- and the runner must
# ``await zabbix_agent(v)``.
#
# The stream carries four waves on a converged host, five on a fresh one (the
# handler wave only exists once the checkpoint notified; design doc section 5.7):
#
# 0. the ``fact(LsbRelease)`` barrier -- skipped entirely when
#    :attr:`ZabbixAgentVars.distribution_release` overrides the codename.
# 1. the ``fact(FindFiles)`` purge sweep, the read that feeds the removal
#    requests and the per-host key stream.
# 2. the keyring install, removals, repository lines, packages, the pin, the
#    enable, closed by the ``act`` getenforce probe.
# 3. :func:`configure` -- SELinux exemption, PSK file, the rendered conf, the
#    include dir, the final service task -- closed by
#    ``checkpoint("configure done")``.
# 4. :func:`~reemote.handlers.service_handler` -- ``Restart zabbix-agent
#    (handler)``, only on the hosts that checkpoint selected.
#
# The reads the play took between its task files -- the distribution release,
# the repository-file ``find``, the getenforce ``stat``+``command`` pair and the
# handler notification -- are barriers inside the one per-host generator. The
# factory returns ``None`` on every path, including the family guard's, because
# nothing here publishes outputs (design doc section 9).
#
# Deviations from the Ansible role, all deliberate:
#
# * **D1** family guard: the ``zabbix_agent_os_family`` dispatch and every
#   non-Ubuntu task file are frozen away; only the Debian/Ubuntu path runs. The
#   template's three ``Windows`` branches stay in its verbatim text but never
#   fire, because ``zabbix_agent_os_family`` is frozen to ``"Debian"``.
# * **D2** the ``apt`` tasks' ``update_cache: yes cache_valid_time: 0`` refreshed
#   the index on *every* run (a permanently changed task). The refresh instead
#   rides ``apt.repo(update_cache=True)`` on the last repository line, so it
#   fires only when a sources file actually changed and a converged host reports
#   no change. A hand-broken state (repo lines present, index never refreshed)
#   needs a separate ``apt.update`` request -- the role's own repair behaviour
#   is not reproduced.
# * **D3** ``apt_key: url=...`` becomes the async factory's control-side
#   ``files.fetch_url`` plus a per-host ``apt.key(stream, keyid=...)``: idempotent
#   through the ``AptKeys`` fact (issue #53) instead of the deprecated
#   ``apt-key``. The keyring lands in ``/etc/apt/trusted.gpg.d`` under a fixed
#   name -- global trust, the ``apt_key`` semantics -- so the repository lines
#   stay byte-identical to the role's and carry no ``signed-by`` option.
# * **D4** the ``sign_keys`` table is frozen from ``vars/zabbix.yml``, and that
#   4.4-era table has no jammy/noble entries: an unknown (version, codename)
#   pair raises :class:`ValueError` with an override hint instead of Ansible's
#   undefined-key failure inside the ``apt_key`` task.
# * **D5** the tasks' ``environment: http_proxy/https_proxy`` become ``op``'s
#   ``env=`` meta-parameter (issue #66), omitted exactly where the role used
#   ``default(omit)``.
# * **D6** the getenforce ``stat`` + ``command`` + ``set_fact`` chain becomes one
#   ``act(Shell, "test -x /usr/sbin/getenforce && getenforce")`` barrier: a
#   non-zero exit is data, the missing binary and the disabled daemon both read
#   as "no SELinux", and ``changed_when: false`` is inherent to a barrier.
# * **D7** handlers are the checkpoint pattern: ``changed_hosts`` after
#   ``checkpoint("configure done")`` selects the notifying names ("Configure
#   zabbix-agent", "Place TLS PSK File"). The handler's ``enabled: yes`` is not
#   reproduced inside ``service_handler`` -- the install stage's enable task and
#   the final service task already converge ``enabled`` on every host. The
#   ``firewalld-reload`` command handler is scoped out with its notifying tasks.
# * **D8** the repository purge is the role's two-step ``find`` (registered,
#   then a ``file: state=absent`` loop with an ``excludes``): the ``find`` is the
#   ``fact(FindFiles)`` barrier and the exclusion is a plain Python filter, so
#   one *file*-shaped removal request is emitted per stale path.
# * **D9** the SELinux ordering quirk is preserved: the policycoreutils install
#   in the Debian stage sees only the *user-set* ``selinux`` variable (the
#   getenforce probe has not run yet, exactly as in the role), while the
#   permissive-domain task runs on the variable **or** the probe's verdict.
# * **D10** the ``Linux.yml`` IP ``set_fact`` chain (``ansible_default_ipv4``,
#   the ``ipaddr`` public/private split, the ``listeninterface`` lookup, the
#   ``listenip`` validation against ``ansible_all_ipv4_addresses``) only feeds
#   the API branch, so it is out of scope: ``listenip`` is honoured verbatim
#   (unset renders the role's default of listening on all addresses), and
#   setting ``listeninterface`` raises rather than silently ignoring it.
# * **D11** the opt-in branches are scoped out with explicit raises, never silent
#   drops: ``docker``, ``tlspsk_auto``, ``firewall_enable``,
#   ``firewalld_enable``, ``api_create_hostgroup``/``api_create_hosts`` and a
#   non-empty ``userparameters`` each raise :class:`ValueError` naming the
#   replacement path. The API tasks additionally have no library operation at
#   all -- issue #72. The scripts-tree copy behind
#   ``userparameters`` was refused as boundary issue #67. The API-only
#   variables (``zabbix_url``, ``zabbix_api_user``, ``zabbix_host_groups``,
#   ``zabbix_macros`` ...) are rejected outright by ``extra="forbid"``.
# * **D12** ``zabbix_agent_hostname`` defaults to ``inventory_hostname``, which
#   is the reemote host name; the role's literal ``Server=None`` rendering for
#   the unset ``server``/``serveractive`` defaults renders as ``Server=`` here.
# * **D13** the template is carried verbatim and rendered control-side through
#   ``reemote.roles._render`` (needs ``reemote[templates]``), with Ansible's
#   ``comment`` filter and a ``version_compare`` test registered -- the systemd
#   role precedent. ``ansible_managed`` is frozen to its default text.
# """

import operator
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import IO, Any, Literal

from pydantic import BaseModel, ConfigDict

from ..actions import Shell
from ..core import op
from ..handlers import changed_hosts, service_handler
from ..hosts import Host
from ..modules import apt, files, selinux, systemd
from ..modules.files import FindFiles
from ..modules.server import LsbRelease
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, act, checkpoint, fact

# --- role constants (defaults/main.yml + vars/*.yml, Ubuntu path only) --------

#: ``vars/Debian.yml``, frozen to the single Ubuntu target.
SERVICE_NAME = "zabbix-agent"
CONF_FILENAME = "zabbix_agentd.conf"
CONF_DIR = "/etc/zabbix"
CONF_PATH = f"{CONF_DIR}/{CONF_FILENAME}"

#: The ``apt_key`` task's fixed key URL, fetched once by the async factory.
REPO_KEY_URL = "http://repo.zabbix.com/zabbix-official-repo.key"

#: Where the keyring lands: D3's global-trust location, so the repository lines
#: stay byte-identical to the role's (no ``signed-by`` option).
REPO_KEYRING = "/etc/apt/trusted.gpg.d/zabbix-official-repo.gpg"

#: ``zabbix_agent_packages``' default list, frozen as a hashable tuple.
ZABBIX_PACKAGES = ("zabbix-agent", "zabbix-sender", "zabbix-get")

#: The policycoreutils package the Debian stage installs when the *user-set*
#: ``selinux`` variable is true (D9).
POLICYCOREUTILS_PACKAGE = "policycoreutils-python-utils"

#: ``selinux_permissive: name=zabbix_agent_t``.
SELINUX_DOMAIN = "zabbix_agent_t"

#: D6: the ``stat`` + ``command`` pair as one probe; a non-zero exit (no
#: binary) is data.
GETENFORCE_PROBE = "test -x /usr/sbin/getenforce && getenforce"

#: The ``find`` task's path/pattern pair (D8); the ``excludes`` value is the
#: managed filename, reconstructed per version below.
SOURCES_DIR = "/etc/apt/sources.list.d"
SOURCES_PATTERN = "repo_zabbix_com_zabbix*.list"

#: The apt preferences directory ``zabbix_agent_apt_priority`` writes into.
APT_PREFERENCES_DIR = "/etc/apt/preferences.d"

#: The two task names carrying ``notify: restart zabbix-agent`` (D7).
NOTIFYING_NAMES = ("Configure zabbix-agent", "Place TLS PSK File")

#: ``vars/zabbix.yml``'s ``sign_keys`` table, frozen verbatim. The (version,
#: codename) key is the short form -- ``"4.4"`` -> ``"44"``; the Debian codename
#: rows are carried along so the table matches upstream even though only Ubuntu
#: releases are reachable through the family guard (D1, D4).
SIGN_KEYS: dict[str, dict[str, str]] = {
    "44": {
        "focal": "A14FE591", "eoan": "A14FE591", "cosmic": "A14FE591",
        "bionic": "A14FE591", "sonya": "A14FE591", "serena": "A14FE591",
        "buster": "A14FE591", "stretch": "A14FE591", "wheezy": "A14FE591",
        "jessie": "79EA5ED4", "trusty": "79EA5ED4", "xenial": "E709712C",
    },
    "42": {
        "focal": "A14FE591", "eoan": "A14FE591", "cosmic": "A14FE591",
        "bionic": "A14FE591", "sonya": "A14FE591", "serena": "A14FE591",
        "buster": "A14FE591", "stretch": "A14FE591", "wheezy": "A14FE591",
        "jessie": "79EA5ED4", "trusty": "79EA5ED4", "xenial": "E709712C",
    },
    "40": {
        "bionic": "A14FE591", "sonya": "A14FE591", "serena": "A14FE591",
        "buster": "A14FE591", "stretch": "A14FE591", "wheezy": "A14FE591",
        "jessie": "79EA5ED4", "trusty": "79EA5ED4", "xenial": "E709712C",
    },
    "34": {
        "bionic": "A14FE591", "sonya": "A14FE591", "serena": "A14FE591",
        "stretch": "A14FE591", "wheezy": "A14FE591",
        "jessie": "79EA5ED4", "trusty": "79EA5ED4", "xenial": "E709712C",
    },
    "32": {
        "bionic": "A14FE591", "sonya": "79EA5ED4", "serena": "79EA5ED4",
        "stretch": "A14FE591", "wheezy": "79EA5ED4",
        "jessie": "79EA5ED4", "trusty": "79EA5ED4", "xenial": "E709712C",
    },
    "30": {
        "bionic": "A14FE591", "wheezy": "79EA5ED4", "jessie": "79EA5ED4",
        "stretch": "A14FE591", "buster": "A14FE591", "trusty": "79EA5ED4",
        "xenial": "E709712C",
    },
    "24": {
        "wheezy": "79EA5ED4", "jessie": "79EA5ED4", "precise": "79EA5ED4",
        "trusty": "79EA5ED4",
    },
    "22": {
        "squeeze": "79EA5ED4", "jessie": "79EA5ED4", "precise": "79EA5ED4",
        "trusty": "79EA5ED4", "lucid": "79EA5ED4",
    },
}

#: ``templates/zabbix_agentd.conf.j2``, verbatim. Rendered control-side by
#: :func:`render_agentd_conf` through
#: :func:`~reemote.roles._render.jinja2_environment` with Ansible's whitespace
#: defaults, plus the two registered helpers the template asks for (D13): the
#: ``comment`` filter for the ``ansible_managed`` header line and the
#: ``version_compare`` test for the module-path and TLS sections. The
#: ``zabbix_agent_os_family == "Windows"`` branches stay in the text but never
#  fire on this target (D1).
AGENTD_TEMPLATE = """{{ ansible_managed | comment }}
# this is a config file for zabbix agent
# to get more information about zabbix, visit http://www.zabbix.com

############ general parameters #################

### option: pidfile
#       name of pid file.
#
{% if zabbix_agent_os_family != "Windows" %}
PidFile={{ zabbix_agent_pidfile }}
{% endif %}

### option: logfile
#       name of log file.
#       if not set, syslog is used.
#
{% if zabbix_agent_os_family == "Windows" %}
LogFile={{ zabbix_agent_win_logfile }}
{% else %}
LogFile={{ zabbix_agent_logfile }}
{% endif %}

### option: logfilesize
#       maximum size of log file in mb.
#       0 - disable automatic log rotation.
#
LogFileSize={{ zabbix_agent_logfilesize }}

### option: debuglevel
#       specifies debug level
#       0 - no debug
#       1 - critical information
#       2 - error information
#       3 - warnings
#       4 - for debugging (produces lots of information)
#
DebugLevel={{ zabbix_agent_debuglevel }}


### option: sourceip
#       source ip address for outgoing connections.
#
{% if zabbix_agent_sourceip is defined and zabbix_agent_sourceip %}
SourceIP={{ zabbix_agent_sourceip }}
{% endif %}

### option: enableremotecommands
#       whether remote commands from zabbix server are allowed.
#       0 - not allowed
#       1 - allowed
#
EnableRemoteCommands={{ zabbix_agent_enableremotecommands }}

### option: logremotecommands
#       enable logging of executed shell commands as warnings.
#       0 - disabled
#       1 - enabled
#
LogRemoteCommands={{ zabbix_agent_logremotecommands }}

##### passive checks related

### option: server
#       list of comma delimited ip addresses (or hostnames) of zabbix servers.
#       incoming connections will be accepted only from the hosts listed here.
#       no spaces allowed.
#       if ipv6 support is enabled then '127.0.0.1', '::127.0.0.1', '::ffff:127.0.0.1' are treated equally.
#
# mandatory: yes
# default:
# server=
Server={{ zabbix_agent_server }}

### option: listenport
#       agent will listen on this port for connections from the server.
#
ListenPort={{ zabbix_agent_listenport }}


### option: listenip
#       list of comma delimited ip addresses that the agent should listen on.
#       first ip address is sent to zabbix server if connecting to it to retrieve list of active checks.
#
{% if zabbix_agent_listenip is defined and zabbix_agent_listenip !='0.0.0.0' and zabbix_agent_listenip %}
ListenIP={{ zabbix_agent_listenip }}
{% endif %}

### option: startagents
#       number of pre-forked instances of zabbix_agentd that process passive checks.
#       if set to 0, disables passive checks and the agent will not listen on any tcp port.
#
StartAgents={{ zabbix_agent_startagents }}

##### active checks related
### option: serveractive
#       list of comma delimited ip:port (or hostname:port) pairs of zabbix servers for active checks.
#       if port is not specified, default port is used.
#       ipv6 addresses must be enclosed in square brackets if port for that host is specified.
#       if port is not specified, square brackets for ipv6 addresses are optional.
#       if this parameter is not specified, active checks are disabled.
#       example: serveractive=127.0.0.1:20051,zabbix.domain,[::1]:30051,::1,[12fc::1]
#
ServerActive={{ zabbix_agent_serveractive }}

### option: hostname
#       unique, case sensitive hostname.
#       required for active checks and must match hostname as configured on the server.
#       value is acquired from hostnameitem if undefined.
#
{% if zabbix_agent_hostname is defined and zabbix_agent_hostname %}
Hostname={{ zabbix_agent_hostname }}
{% endif %}

### option: hostnameitem
#       item used for generating hostname if it is undefined.
#       ignored if hostname is defined.
#
{% if zabbix_agent_hostnameitem is defined and zabbix_agent_hostnameitem %}
HostnameItem={{ zabbix_agent_hostnameitem }}
{% endif %}

### option: hostmetadata
#	optional parameter that defines host metadata.
#	host metadata is used at host auto-registration process.
#	an agent will issue an error and not start if the value is over limit of 255 characters.
#	if not defined, value will be acquired from hostmetadataitem.
#
{% if zabbix_agent_hostmetadata is defined and zabbix_agent_hostmetadata %}
HostMetadata={{ zabbix_agent_hostmetadata }}
{% endif %}

### option: hostmetadataitem
#	optional parameter that defines an item used for getting host metadata.
#	host metadata is used at host auto-registration process.
#	during an auto-registration request an agent will log a warning message if
#	the value returned by specified item is over limit of 255 characters.
#	this option is only used when hostmetadata is not defined.
#
{% if zabbix_agent_hostmetadataitem is defined and zabbix_agent_hostmetadataitem %}
HostMetadataItem={{ zabbix_agent_hostmetadataitem }}
{% endif %}

### option: refreshactivechecks
#       how often list of active checks is refreshed, in seconds.
#
RefreshActiveChecks={{ zabbix_agent_refreshactivechecks }}

### option: buffersend
#       do not keep data longer than n seconds in buffer.
#
BufferSend={{ zabbix_agent_buffersend }}

### option: buffersize
#       maximum number of values in a memory buffer. the agent will send
#       all collected data to zabbix server or proxy if the buffer is full.
#
BufferSize={{ zabbix_agent_buffersize }}

### option: maxlinespersecond
#       maximum number of new lines the agent will send per second to zabbix server
#       or proxy processing 'log' and 'logrt' active checks.
#       the provided value will be overridden by the parameter 'maxlines',
#       provided in 'log' or 'logrt' item keys.
#
MaxLinesPerSecond={{ zabbix_agent_maxlinespersecond }}

############ advanced parameters #################

### option: alias
#       sets an alias for parameter. it can be useful to substitute long and complex parameter name with a smaller and simpler one.
#
{% if zabbix_agent_zabbix_alias is defined and zabbix_agent_zabbix_alias %}
Alias={{ zabbix_agent_zabbix_alias }}
{% endif %}

### option: timeout
#       spend no more than timeout seconds on processing
#
Timeout={{ zabbix_agent_timeout }}

{% if zabbix_agent_os_family != "Windows" %}
### option: allowroot
#       allow the agent to run as 'root'. if disabled and the agent is started by 'root', the agent
#       will try to switch to user 'zabbix' instead. has no effect if started under a regular user.
#       0 - do not allow
#       1 - allow
#
AllowRoot={{ zabbix_agent_allowroot }}
{% endif %}

### Option: User
#	Drop privileges to a specific, existing user on the system.
#	Only has effect if run as 'root' and AllowRoot is disabled.
#
# Mandatory: no
# Default:

{% if zabbix_agent_runas_user is defined and zabbix_agent_runas_user %}
User={{ zabbix_agent_runas_user }}
{% endif %}

### option: include
#       you may include individual files or all files in a directory in the configuration file.
#       installing zabbix will create include directory in /usr/local/etc, unless modified during the compile time.
#
{% if zabbix_agent_os_family == "Windows" %}
Include={{ zabbix_agent_win_include }}
{% else %}
Include={{ zabbix_agent_include }}
{% endif %}

####### user-defined monitored parameters #######

### option: unsafeuserparameters
#       allow all characters to be passed in arguments to user-defined parameters.
#       0 - do not allow
#       1 - allow
#
UnsafeUserParameters={{ zabbix_agent_unsafeuserparameters }}

### option: userparameter
#       user-defined parameter to monitor. there can be several user-defined parameters.
#       format: userparameter=<key>,<shell command>
#       note that shell command must not return empty string or eol only.
#       see 'zabbix_agentd' directory for examples.
#
# mandatory: no
# default:
# userparameter=

# disabled. an configuration file should be placed on directory: {{ zabbix_agent_include }}

####### LOADABLE MODULES #######
{% if zabbix_version is version_compare('2.2', '>=') %}
### Option: LoadModulePath
#       Full path to location of agent modules.
#       Default depends on compilation options.
#
{% if zabbix_agent_os_family != "Windows" %}
LoadModulePath={{ zabbix_agent_loadmodulepath }}
{% endif %}
{% endif %}

### Option: LoadModule
#       Module to load at agent startup. Modules are used to extend functionality of the agent.
#       Format: LoadModule=<module.so>
#       The modules must be located in directory specified by LoadModulePath.
#       It is allowed to include multiple LoadModule parameters.
#
{% if zabbix_agent_loadmodule is defined and zabbix_agent_loadmodule %}
LoadModule={{ zabbix_agent_loadmodule }}
{% endif %}

{% if zabbix_version is version_compare('3.0', '>=') %}
####### TLS-RELATED PARAMETERS #######

### Option: TLSConnect
#       How the agent should connect to server or proxy. Used for active checks.
#       Only one value can be specified:
#               unencrypted - connect without encryption
#               psk         - connect using TLS and a pre-shared key
#               cert        - connect using TLS and a certificate
#
# Mandatory: yes, if TLS certificate or PSK parameters are defined (even for 'unencrypted' connection)
# Default:
# TLSConnect=unencrypted
{% if zabbix_agent_tlsconnect is defined and zabbix_agent_tlsconnect %}
TLSConnect={{ zabbix_agent_tlsconnect }}
{% endif %}

### Option: TLSAccept
#       What incoming connections to accept.
#       Multiple values can be specified, separated by comma:
#               unencrypted - accept connections without encryption
#               psk         - accept connections secured with TLS and a pre-shared key
#               cert        - accept connections secured with TLS and a certificate
#
# Mandatory: yes, if TLS certificate or PSK parameters are defined (even for 'unencrypted' connection)
# Default:
# TLSAccept=unencrypted
{% if zabbix_agent_tlsaccept is defined and zabbix_agent_tlsaccept %}
TLSAccept={{ zabbix_agent_tlsaccept }}
{% endif %}

### Option: TLSCAFile
#       Full pathname of a file containing the top-level CA(s) certificates for
#       peer certificate verification.
#
# Mandatory: no
# Default:
# TLSCAFile=
{% if zabbix_agent_tlscafile is defined and zabbix_agent_tlscafile %}
TLSCAFile={{ zabbix_agent_tlscafile }}
{% endif %}

### Option: TLSCRLFile
#       Full pathname of a file containing revoked certificates.
#
# Mandatory: no
# Default:
# TLSCRLFile=
{% if zabbix_agent_tlscrlfile is defined and zabbix_agent_tlscrlfile %}
TLSCRLFile={{ zabbix_agent_tlscrlfile }}
{% endif %}

### Option: TLSServerCertIssuer
#      Allowed server certificate issuer.
#
# Mandatory: no
# Default:
# TLSServerCertIssuer=
{% if zabbix_agent_tlsservercertissuer is defined and zabbix_agent_tlsservercertissuer %}
TLSServerCertIssuer={{ zabbix_agent_tlsservercertissuer }}
{% endif %}

### Option: TLSServerCertSubject
#      Allowed server certificate subject.
#
# Mandatory: no
# Default:
# TLSServerCertSubject=
{% if zabbix_agent_tlsservercertsubject is defined and zabbix_agent_tlsservercertsubject %}
TLSServerCertSubject={{ zabbix_agent_tlsservercertsubject }}
{% endif %}

### Option: TLSCertFile
#       Full pathname of a file containing the agent certificate or certificate chain.
#
# Mandatory: no
# Default:
# TLSCertFile=
{% if zabbix_agent_tlscertfile is defined and zabbix_agent_tlscertfile %}
TLSCertFile={{ zabbix_agent_tlscertfile }}
{% endif %}

### Option: TLSKeyFile
#       Full pathname of a file containing the agent private key.
#
# Mandatory: no
# Default:
# TLSKeyFile=
{% if zabbix_agent_tlskeyfile is defined and zabbix_agent_tlskeyfile %}
TLSKeyFile={{ zabbix_agent_tlskeyfile }}
{% endif %}

### Option: TLSPSKIdentity
#       Unique, case sensitive string used to identify the pre-shared key.
#
# Mandatory: no
# Default:
# TLSPSKIdentity=
{% if zabbix_agent_tlspskidentity is defined and zabbix_agent_tlspskidentity %}
TLSPSKIdentity={{ zabbix_agent_tlspskidentity }}
{% endif %}

### Option: TLSPSKFile
#       Full pathname of a file containing the pre-shared key.
#
# Mandatory: no
# Default:
# TLSPSKFile=
{% if zabbix_agent_tlspskfile is defined and zabbix_agent_tlspskfile %}
TLSPSKFile={{ zabbix_agent_tlspskfile }}
{% endif %}
{% endif %}
"""


# --- variables ---------------------------------------------------------------


class ZabbixAgentVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``zabbix_agent`` role mapping, so ``zabbix_agent_server``
    there sets :attr:`server`. ``zabbix_version`` is an alias of
    ``zabbix_agent_version`` upstream; the single :attr:`version` field covers
    both. The role's empty values become ``None`` (rendered *undefined* for the
    template, which is what its ``is defined and`` gates produce anyway) or
    ``""`` for the two keys the template renders unconditionally.

    Every knob of the scoped-out branches is still declared -- so a key there
    binds and then raises in the body (D11) or is carried inert -- rather than
    being rejected as an unknown field. Sequences are ``tuple``, never
    ``list``: the model is frozen and hashable, and a value that reaches
    :func:`~reemote.core.op` must hash (design doc section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Which ``http://repo.zabbix.com/zabbix/<version>/ubuntu/`` to configure,
    #: and the ``sign_keys`` table row. The role ships 2.2-4.4; its 4.4-era
    #: table has no jammy/noble (D4).
    version: str = "4.4"
    #: ``zabbix_repo``: only ``"zabbix"`` (the vendor repository) and any other
    #: string (manage the repository yourself, install from the distro's own
    #: archive) are supported; the role's ``None``/empty branch just skips the
    #: repository and keyring tasks.
    repo: str = "zabbix"
    #: ``zabbix_agent_distribution_release``: the codename override for both the
    #: repository line and the ``sign_keys`` lookup. ``None`` reads
    #: ``fact(LsbRelease)`` inside the body instead (the role's
    #: ``ansible_distribution_release``).
    distribution_release: str | None = None
    #: ``zabbix_agent_src_reinstall``: purge ``.list`` files for other Zabbix
    #: sources before writing the managed one (D8).
    src_reinstall: bool = True
    #: ``zabbix_agent_packages``.
    packages: tuple[str, ...] = ZABBIX_PACKAGES
    #: ``zabbix_agent_package_state`` -- ``absent`` maps to
    #: ``apt.packages(present=False)``.
    package_state: Literal["present", "latest", "absent"] = "present"
    #: ``zabbix_apt_install_recommends`` -- False adds
    #: ``--no-install-recommends`` to the install.
    install_recommends: bool = False
    #: ``zabbix_agent_apt_priority``: a positive pin priority writes the
    #: ``/etc/apt/preferences.d/zabbix-agent-<version>`` file.
    apt_priority: int | None = None
    #: ``zabbix_http_proxy`` / ``zabbix_https_proxy``: the apt tasks' proxy
    #: environment (D5). Omitted from the requests where ``None``, as the role's
    #: ``default(omit)`` did.
    http_proxy: str | None = None
    https_proxy: str | None = None
    #: ``zabbix_selinux``: the *user-set* half of the SELinux gate (D9). The
    #: probe result is the other half and is never a variable.
    selinux: bool = False

    # --- zabbix_agentd.conf scalars (the template's always-defined keys) ---

    pidfile: str = "/var/run/zabbix/zabbix_agentd.pid"
    logfile: str = "/var/log/zabbix/zabbix_agentd.log"
    logfilesize: int = 100
    debuglevel: int = 3
    sourceip: str | None = None
    enableremotecommands: int = 0
    logremotecommands: int = 0
    server: str = ""
    serveractive: str = ""
    listenport: int = 10050
    #: ``zabbix_agent_listenip``: honoured verbatim; the unset default leaves
    #: the ``ListenIP`` line out, the role's ``0.0.0.0`` rendering (D10).
    listenip: str | None = None
    #: ``zabbix_agent_listeninterface``: out of scope, raises when set (D10).
    listeninterface: str | None = None
    startagents: int = 3
    #: ``zabbix_agent_hostname``: ``None`` renders the host name, the role's
    #: ``inventory_hostname`` default (D12).
    hostname: str | None = None
    hostnameitem: str | None = None
    hostmetadata: str | None = None
    hostmetadataitem: str | None = None
    refreshactivechecks: int = 120
    buffersend: int = 5
    buffersize: int = 100
    maxlinespersecond: int = 100
    #: ``zabbix_agent_zabbix_alias`` -- the bare field name drops the stutter.
    zabbix_alias: str | None = None
    timeout: int = 3
    allowroot: int = 0
    runas_user: str | None = None
    include: str = "/etc/zabbix/zabbix_agentd.d"
    unsafeuserparameters: int = 0
    loadmodulepath: str = "${libdir}/modules"
    loadmodule: str | None = None

    # --- TLS knobs (template lines + the PSK file, opt-in and cheap) ---

    tlsconnect: str | None = None
    tlsaccept: str | None = None
    tlscafile: str | None = None
    tlscrlfile: str | None = None
    tlsservercertissuer: str | None = None
    tlsservercertsubject: str | None = None
    tlscertfile: str | None = None
    tlskeyfile: str | None = None
    tlspskidentity: str | None = None
    #: ``zabbix_agent_tlspskfile``: when set, its directory is created and the
    #: secret placed in it (both notifying the restart handler, D7).
    tlspskfile: str | None = None
    #: ``zabbix_agent_tlspsk_secret``: the PSK content; the file task runs only
    #: when both PSK variables are set, as the role's ``when`` pair.
    tlspsk_secret: str | None = None
    #: ``zabbix_agent_tlspsk_auto``: the generated-secret include is scoped out
    #: (D11), raises when true.
    tlspsk_auto: bool = False

    # --- scoped-out opt-in branches: declared so the keys bind, then raise ---

    #: ``zabbix_agent_userparameters``: the scripts-tree copy has no library
    #: operation (boundary issue #67); a non-empty list raises (D11).
    userparameters: tuple[Any, ...] = ()
    #: ``zabbix_agent_docker``: the Docker branch is out of scope (D11).
    docker: bool = False
    #: ``zabbix_agent_firewall_enable`` / ``zabbix_agent_firewalld_enable``:
    #: the opt-in firewall branches are out of scope (D11).
    firewall_enable: bool = False
    firewalld_enable: bool = False
    #: ``zabbix_api_create_hostgroup`` / ``zabbix_api_create_hosts``: the
    #: Zabbix API branch has no operations at all -- issue #72 (D11).
    api_create_hostgroup: bool = False
    api_create_hosts: bool = False

    # --- inert knobs: carried so existing inventories bind, they do nothing ---

    #: reemote's apt operations always use ``apt-get``, so the role's knob to
    #: allow it is already the truth.
    force_apt_get: bool = True
    #: only the scoped-out iptables/firewalld tasks and the optional JMX
    #: documentation read this.
    jmx_listenport: int | None = None
    #: only the localhost ``pip`` tasks of the API branch consumed this.
    install_pip_packages: bool = True
    #: same: it gated elevation of the localhost pip tasks.
    become_on_localhost: bool = True
    #: same: it built the Docker image tag only.
    version_patch: int = 0


@dataclass(frozen=True)
class InstallPlan:
    """One host's resolved repository inputs, frozen after the fact barriers.

    ``codename`` is the override or the :class:`~reemote.modules.server.LsbRelease`
    read; ``sign_key`` the table lookup it selects (D4); ``stale_sources`` the
    purge list the ``FindFiles`` barrier returned *after* the role's
    ``excludes`` filter (D8); ``env`` the proxy pairs of D5, already a hashable
    tuple of pairs or ``None``.
    """

    codename: str
    sign_key: str
    repo_filename: str
    stale_sources: tuple[str, ...]
    env: tuple[tuple[str, str], ...] | None


# --- pure helpers ------------------------------------------------------------


def version_operands(text: Any) -> tuple[int, ...]:
    """The numeric tuple of a version string, dotted/underscored/dashed alike.

    ``zabbix_version`` is built to be compared against ``'2.2'``/``'3.0'``
    literals by the template's two ``version_compare`` gates; Ansible's own
    comparison is ``LooseVersion``-based, and for these inputs a plain tuple of
    ints -- shorter tuples compared left-to-right with zero padding -- gives the
    same verdict.
    """
    parts = str(text).replace("_", ".").replace("-", ".").split(".")
    return tuple(int(part) for part in parts if part.isdigit())


#: The operators the template (or a future one) can ask ``version_compare`` for.
VERSION_COMPARATORS: dict[str, Callable[[tuple[int, ...], tuple[int, ...]], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
    "eq": operator.eq,
    "ne": operator.ne,
    "ge": operator.ge,
    "le": operator.le,
    "gt": operator.gt,
    "lt": operator.lt,
}


def version_compare(value: Any, comparison: Any, eqln: str = ">=") -> bool:
    """Ansible's ``version_compare`` test, restricted to numeric versions."""
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


def comment_filter(text: Any, prefix: str = "# ") -> str:
    """Ansible's ``comment`` filter: every line of *text*, prefixed.

    Only the header line's ``{{ ansible_managed | comment }}`` uses it, with no
    arguments -- the ``'# '`` prefix -- so this is the exact systemd-role
    reproduction.
    """
    return "\n".join(f"{prefix}{line}" for line in str(text).splitlines())


def _environment() -> Any:
    """``jinja2_environment`` plus the template's two Ansible-isms (D13).

    The import lives inside this function so ``import reemote.roles`` stays
    clean without the ``reemote[templates]`` extra (§17.4).
    """
    from ._render import jinja2_environment

    env = jinja2_environment()
    env.filters["comment"] = comment_filter
    # The template uses ``is version_compare(...)`` -- a *test*, resolved
    # against ``env.tests``, not the filter table.
    env.tests["version_compare"] = version_compare
    return env


def render_agentd_conf(v: ZabbixAgentVars, hostname: str) -> str:
    """``templates/zabbix_agentd.conf.j2``, pre-rendered control-side.

    The context carries the variables ``defaults/main.yml`` always defines and
    omits the ones the role left empty, which is what keeps the template's
    ``{% if x is defined and x %}`` gates behaving as under Ansible: an unset
    optional key is *undefined* for the template, not ``None`` (strict
    undefined otherwise, so a template typo fails loudly at render time).
    ``listenip``'s ``!= '0.0.0.0'`` conjunct made the role's computed default
    render no line, so the same value is simply left out here.
    """
    context: dict[str, Any] = {
        "ansible_managed": "Ansible managed",
        "zabbix_agent_os_family": "Debian",
        "zabbix_version": v.version,
        "zabbix_agent_pidfile": v.pidfile,
        "zabbix_agent_logfile": v.logfile,
        "zabbix_agent_logfilesize": v.logfilesize,
        "zabbix_agent_debuglevel": v.debuglevel,
        "zabbix_agent_enableremotecommands": v.enableremotecommands,
        "zabbix_agent_logremotecommands": v.logremotecommands,
        "zabbix_agent_server": v.server,
        "zabbix_agent_listenport": v.listenport,
        "zabbix_agent_startagents": v.startagents,
        "zabbix_agent_serveractive": v.serveractive,
        "zabbix_agent_hostname": v.hostname or hostname,
        "zabbix_agent_refreshactivechecks": v.refreshactivechecks,
        "zabbix_agent_buffersend": v.buffersend,
        "zabbix_agent_buffersize": v.buffersize,
        "zabbix_agent_maxlinespersecond": v.maxlinespersecond,
        "zabbix_agent_timeout": v.timeout,
        "zabbix_agent_allowroot": v.allowroot,
        "zabbix_agent_include": v.include,
        "zabbix_agent_unsafeuserparameters": v.unsafeuserparameters,
        "zabbix_agent_loadmodulepath": v.loadmodulepath,
    }
    optional = (
        ("zabbix_agent_sourceip", v.sourceip),
        ("zabbix_agent_listenip", v.listenip),
        ("zabbix_agent_hostnameitem", v.hostnameitem),
        ("zabbix_agent_hostmetadata", v.hostmetadata),
        ("zabbix_agent_hostmetadataitem", v.hostmetadataitem),
        ("zabbix_agent_zabbix_alias", v.zabbix_alias),
        ("zabbix_agent_runas_user", v.runas_user),
        ("zabbix_agent_loadmodule", v.loadmodule),
        ("zabbix_agent_tlsconnect", v.tlsconnect),
        ("zabbix_agent_tlsaccept", v.tlsaccept),
        ("zabbix_agent_tlscafile", v.tlscafile),
        ("zabbix_agent_tlscrlfile", v.tlscrlfile),
        ("zabbix_agent_tlsservercertissuer", v.tlsservercertissuer),
        ("zabbix_agent_tlsservercertsubject", v.tlsservercertsubject),
        ("zabbix_agent_tlscertfile", v.tlscertfile),
        ("zabbix_agent_tlskeyfile", v.tlskeyfile),
        ("zabbix_agent_tlspskidentity", v.tlspskidentity),
        ("zabbix_agent_tlspskfile", v.tlspskfile),
    )
    for key, value in optional:
        if value:
            context[key] = value
    return _environment().from_string(AGENTD_TEMPLATE).render(**context)


def proxy_env(v: ZabbixAgentVars) -> tuple[tuple[str, str], ...] | None:
    """The ``environment:`` block of D5, as ``op``'s hashable ``env`` pairs.

    The role passed each proxy through ``| default(None) | default(omit)``, so
    an unset variable contributed nothing; the same omission is expressed by
    returning ``None`` when neither is set, and by carrying only the pairs that
    have a value.
    """
    pairs = tuple(
        (name, value)
        for name, value in (("http_proxy", v.http_proxy), ("https_proxy", v.https_proxy))
        if value
    )
    return pairs or None


def sign_key_for(version: str, codename: str, host_name: str) -> str:
    """The ``sign_keys[short][release]`` lookup, with a loud miss (D4).

    Raises:
        ValueError: The frozen 4.4-era table has no entry for this
            (version, codename) pair -- a modern Ubuntu release against an old
            Zabbix version. The message names both overrides.
    """
    short = version.replace(".", "")
    sign_key = SIGN_KEYS.get(short, {}).get(codename)
    if sign_key is None:
        raise ValueError(
            f"{host_name}: no Zabbix signing key for version {version!r} on "
            f"Ubuntu {codename!r} (the upstream 4.4-era sign_keys table does "
            "not know this release). Set 'zabbix_agent_version' to a release "
            "the table covers, or 'zabbix_agent_distribution_release' to a "
            "codename it lists."
        )
    return sign_key


def validate(v: ZabbixAgentVars, host: Host) -> None:
    """The scoped-out knobs, refused inside the body before any contact (D11).

    The datadog precedent: a flag whose branch this migration does not carry
    raises rather than silently doing nothing. The API-only *values* (server
    URL, credentials, host groups, macros) never reach the model at all --
    ``extra="forbid"`` rejects them while the inventory resolves.

    Raises:
        ValueError: A scoped-out branch was asked for, naming the replacement
            path or the tracker issue that owns the gap.
    """
    if v.userparameters:
        raise ValueError(
            f"{host.name}: 'zabbix_agent_userparameters' is out of scope: the "
            "scripts-tree copy was declined upstream (issue #67), so reemote "
            "has no recursive transfer operation. Ship userparameter files "
            "with your own deploy, one files.install per file."
        )
    if v.listeninterface:
        raise ValueError(
            f"{host.name}: 'zabbix_agent_listeninterface' is out of scope: "
            "resolving an interface to its address needs the interface facts "
            "the role read from Ansible. Set 'zabbix_agent_listenip' instead."
        )
    scoped = (
        (v.docker, "the Docker-agent branch (Docker.yml)"),
        (v.tlspsk_auto, "the TLS-PSK auto-generation branch (tlspsk_auto.yml)"),
        (v.firewall_enable, "the iptables branch"),
        (v.firewalld_enable, "the firewalld branch and its reload handler"),
        (
            v.api_create_hostgroup or v.api_create_hosts,
            "the Zabbix API branch -- issue #72 tracks the missing operations",
        ),
    )
    for asked, what in scoped:
        if asked:
            raise ValueError(
                f"{host.name}: {what} is scoped out of this migration; unset "
                "the enabling variable to use the supported path."
            )


def install(plan: InstallPlan, v: ZabbixAgentVars, key_stream: IO[bytes] | None) -> Deploy:
    """``tasks/Debian.yml``, Ubuntu path: keyring, sources, packages, enable.

    The keyring rides the frozen plan's ``sign_key`` so the install is
    ``AptKeys``-gated (D3); the purge loop's removals come from the
    ``FindFiles`` barrier the body already read (D8); the repository refresh
    fires once, after the last sources write (D2). The policycoreutils task is
    gated on the *user-set* variable only (D9), and the enable task is
    ``enabled``-only, the role's ``use: service`` shape.
    """
    env_kw: dict[str, Any] = {"env": plan.env} if plan.env else {}

    def deploy(host: Host) -> Generator[StageItem, Any, Any]:
        if key_stream is not None:
            yield op(
                apt.key,
                name="Debian | Install gpg key",
                src=key_stream,
                dest=REPO_KEYRING,
                keyid=plan.sign_key,
                sudo=True,
            )
        for path in plan.stale_sources:
            yield op(
                files.file,
                name=f"Debian | Remove unecessary zabbix repository {path}",
                path=path,
                present=False,
                sudo=True,
            )
        if plan.sign_key:
            # The role's own line shape: the archive URL and the codename are
            # separate fields -- ``deb <url> <dist> <components>``, not a path.
            base = f"http://repo.zabbix.com/zabbix/{v.version}/ubuntu"
            yield op(
                apt.repo,
                name="Debian | Installing deb repository Ubuntu",
                src=f"deb {base} {plan.codename} main",
                filename=plan.repo_filename,
                sudo=True,
                **env_kw,
            )
            yield op(
                apt.repo,
                name="Debian | Installing deb-src repository Ubuntu",
                src=f"deb-src {base} {plan.codename} main",
                filename=plan.repo_filename,
                update_cache=True,
                sudo=True,
                **env_kw,
            )
        if v.apt_priority:
            yield op(
                files.directory,
                name="Debian | Create /etc/apt/preferences.d/",
                path=APT_PREFERENCES_DIR,
                sudo=True,
            )
            underscore = v.version.replace(".", "_")
            yield op(
                files.install,
                name="Debian | Configuring the weight for APT",
                content=(
                    "Package: zabbix-agent\n"
                    "Pin: origin repo.zabbix.com\n"
                    f"Pin-Priority: {v.apt_priority}\n"
                ),
                dest=f"{APT_PREFERENCES_DIR}/zabbix-agent-{underscore}",
                user="root",
                group="root",
                mode="0644",
                sudo=True,
            )
        yield op(
            apt.packages,
            name="Debian | Installing zabbix-agent",
            packages=v.packages,
            present=v.package_state != "absent",
            latest=v.package_state == "latest",
            no_recommends=not v.install_recommends,
            sudo=True,
            **env_kw,
        )
        if v.selinux:
            yield op(
                apt.packages,
                name="Install policycoreutils-python",
                packages=(POLICYCOREUTILS_PACKAGE,),
                present=True,
                no_recommends=not v.install_recommends,
                sudo=True,
                **env_kw,
            )
        yield op(
            systemd.service,
            name="Debian | Enable the service",
            service=SERVICE_NAME,
            enabled=True,
            running=None,
            sudo=True,
        )

    return deploy


def configure(v: ZabbixAgentVars, conf_content: str, selinux_active: bool) -> Deploy:
    """``tasks/Linux.yml``'s configure half, in the role's own order (D6-D9).

    The SELinux exemption follows the probe's verdict *or* the user-set
    variable; the PSK directory/file pair only runs when the matching
    variables are set, and the file notifies like the role's; the rendered
    conf, the include directory and the final started+enabled task close the
    wave the checkpoint reports (D7).
    """

    def deploy(host: Host) -> Generator[StageItem, Any, Any]:
        if selinux_active:
            yield op(
                selinux.permissive_domain,
                name="Allow zabbix_agent to start (SELinux)",
                domain=SELINUX_DOMAIN,
                present=True,
                sudo=True,
            )
        if v.tlspskfile:
            yield op(
                files.directory,
                name="Create directory for PSK file if not exist.",
                path=str(PurePosixPath(v.tlspskfile).parent),
                mode="0755",
                sudo=True,
            )
            if v.tlspsk_secret:
                yield op(
                    files.install,
                    name="Place TLS PSK File",
                    content=v.tlspsk_secret,
                    dest=v.tlspskfile,
                    user="zabbix",
                    group="zabbix",
                    mode="0400",
                    sudo=True,
                )
        yield op(
            files.install,
            name="Configure zabbix-agent",
            content=conf_content,
            dest=CONF_PATH,
            user="root",
            group="root",
            mode="0644",
            sudo=True,
        )
        yield op(
            files.directory,
            name="Create include dir zabbix-agent",
            path=v.include,
            user="root",
            group="zabbix",
            mode="0750",
            sudo=True,
        )
        # The role's final service task carries no notify, so its name is
        # deliberately absent from NOTIFYING_NAMES.
        yield op(
            systemd.service,
            name="Make sure the zabbix-agent service is running",
            service=SERVICE_NAME,
            running=True,
            enabled=True,
            sudo=True,
        )

    return deploy


# --- the role ----------------------------------------------------------------


async def zabbix_agent(v: VarsSet[ZabbixAgentVars]) -> Deploy:
    """Install and configure the Zabbix agent on Ubuntu hosts.

    This is an **async** factory: the signing key's URL is an inventory-wide
    constant, so it is fetched here -- once, and even under ``dry`` (§6.3).
    The runner must ``await zabbix_agent(v)``. Hosts whose resolved variables
    skip the vendor repository (``repo != "zabbix"``) are skipped by the fetch
    gate.

    Args:
        v: The role's resolved variables, one :class:`ZabbixAgentVars` per host
            (``resolve_vars(ZabbixAgentVars, "zabbix_agent", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. Publishes nothing, so the
        generator returns ``None``.

    Raises:
        ValueError: A scoped-out knob is set (D11), or the (version, codename)
            pair has no signing key (D4). Both raise inside the body, before
            the host is contacted.
    """
    # Control-side IO, inventory-wide: the one key the repository tasks sign.
    key_text: str | None = None
    if any(hv.repo == "zabbix" for hv in v.values.values()):
        key_text = await files.fetch_url(REPO_KEY_URL)

    def deploy(host: Host) -> Generator[StageItem, Any, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return
        validate(hv, host)

        # Wave 0: the codename, unless the variables override the fact (the
        # role's zabbix_agent_distribution_release, defaulting to
        # ansible_distribution_release).
        if hv.distribution_release:
            codename = hv.distribution_release
        else:
            codename = (yield fact(LsbRelease))["codename"]

        using_repo = hv.repo == "zabbix"
        sign_key = sign_key_for(hv.version, codename, host.name) if using_repo else ""
        managed_file = f"repo_zabbix_com_zabbix_{hv.version.replace('.', '_')}_ubuntu"
        stale: tuple[str, ...] = ()
        stream: IO[bytes] | None = None
        if using_repo:
            # Wave 1: the purge sweep's read precedes the removals and the
            # keyring request, mirroring the role's find -> loop ordering (D8).
            found = yield fact(FindFiles, SOURCES_DIR, fname=SOURCES_PATTERN)
            stale = tuple(
                path for path in found if PurePosixPath(path).stem != managed_file
            )
            stream = _key_stream(key_text)
        plan = InstallPlan(
            codename=codename,
            sign_key=sign_key,
            repo_filename=managed_file,
            stale_sources=stale if hv.src_reinstall else (),
            env=proxy_env(hv),
        )

        # Wave 2: install, closed by the SELinux probe (D6).
        yield from install(plan, hv, stream)(host)
        result = yield act(
            Shell,
            command=GETENFORCE_PROBE,
            name="Collect getenforce output",
            sudo=True,
        )
        # D9: the permissive gate is the user variable *or* this host's probe;
        # the policycoreutils request above already ran on the variable alone.
        stdout = result["stdout"] if result["exit_status"] == 0 else ""
        selinux_active = hv.selinux or "Enforcing" in stdout or "Permissive" in stdout

        # Wave 3: configure (the conf rendered control-side from the frozen
        # template), then read its own outcome (D7).
        yield from configure(hv, render_agentd_conf(hv, host.name), selinux_active)(host)
        wave_report = yield checkpoint("configure done")

        notified = changed_hosts(wave_report, NOTIFYING_NAMES)

        # Wave 4: the handler, only on the hosts the checkpoint selected.
        yield from service_handler(notified, (SERVICE_NAME,), action="restarted")(host)

    return deploy


def _key_stream(key_text: str | None) -> IO[bytes] | None:
    """The factory's fetched key text as the stream ``apt.key`` reads.

    Built per host inside the body on purpose: a stream is hashable but
    identity-compared, and one module-level stream would be consumed by the
    first host (rule 3, design doc section 6.2).
    """
    import io

    return io.BytesIO(key_text.encode("utf-8")) if key_text is not None else None


__all__ = [
    "ZabbixAgentVars",
    "zabbix_agent",
]
