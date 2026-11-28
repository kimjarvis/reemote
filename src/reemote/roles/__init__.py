# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Migrated Ansible roles as first-class ``reemote`` library citizens (§17).

A role here is a factory that returns a resumable deploy generator, reached
through this package rather than the top level::

    from reemote.roles import ApacheVars, apache

    report, hosts = await run_waves(inventory, apache(v))

Each role module ships a pydantic ``<Role>Vars`` model, the deploy factory
``<role>(v: VarsSet[<Role>Vars]) -> Deploy``, and -- only where another role
consumes them -- an outputs dataclass (§17.2). This ``__init__`` re-exports
those public names explicitly. It is **never** star-imported into
``reemote/__init__.py``: a role name would otherwise shadow a module namespace.

The collisions are handled deliberately. ``reemote.postgresql`` already names
the operation-module namespace re-exported at the top level (and ``reemote.git``
the git-repository module), so roles live only under ``reemote.roles`` and the
top-level names stay the module namespaces; inside ``roles/postgresql.py`` the
module is aliased (``from reemote.modules
import postgresql as pg``). ``tests/roles/test_exports.py`` pins both facts.

The template-rendering roles (``apache``, ``datadog``, ``elasticsearch``,
``firewall``, ``kibana``, ``memcached``, ``minio``, ``nginx``, ``ntp``,
``postgresql``, ``redis``, ``supervisor``, ``systemd``, ``zabbix_agent``) import
jinja2 and PyYAML only through :mod:`reemote.roles._render`, the single import
site for them, which needs the ``reemote[templates]`` extra (§17.4). Nothing in
this package pulls that module in at import time, so ``import reemote.roles``
stays clean without the extra.
``php`` renders its four templates as plain f-strings, so it never needs the
extra even though it templates files -- and ``php_xdebug`` inherits that, since
its one template is pure substitution too. ``nfs`` builds ``/etc/exports`` with
a Python join, the same no-extra case, as does ``security``, whose four config
files are f-string renders and fixpoint text transforms.

Public names are added here as each role is migrated (T4-T17): append a
``from .<role> import (...)`` block and extend ``__all__`` to match that
module's own ``__all__``. ``tests/roles/test_exports.py`` asserts ``__all__``
stays in lockstep with the role modules on disk.
"""

from .apache import ApacheOutputs, ApacheVars, apache
from .apache_php_fpm import ApachePhpFpmVars, apache_php_fpm
from .certbot import Cert, CertbotVars, certbot
from .clamav import ClamavVars, ConfigChange, clamav
from .composer import ComposerVars, composer
from .daemonize import DaemonizeVars, daemonize
from .datadog import DatadogVars, datadog
from .docker import DockerVars, docker
from .drush import DrushVars, drush
from .elasticsearch import ElasticsearchVars, elasticsearch
from .firewall import FirewallVars, ForwardedPort, firewall
from .git import GitVars, git
from .glusterfs import GlusterfsVars, glusterfs
from .helm import HelmChecksum, HelmVars, helm
from .hosts import HostsEntry, HostsVars, hosts
from .htpasswd import HtpasswdCredential, HtpasswdVars, htpasswd
from .java import JavaVars, java
from .kibana import KibanaVars, kibana
from .kubectl import KubectlChecksum, KubectlVars, kubectl
from .mailhog import MailhogVars, mailhog
from .memcached import MemcachedVars, memcached
from .minio import MinioOutputs, MinioVars, minio
from .nfs import NfsVars, nfs
from .nginx import NginxVars, nginx
from .node_exporter import NodeExporterVars, node_exporter
from .nodejs import NodejsVars, NpmGlobalPackage, nodejs
from .ntp import NtpVars, ntp
from .php import PhpOutputs, PhpVars, php
from .php_versions import PhpVersionsOutputs, PhpVersionsVars, php_versions
from .php_xdebug import PhpXdebugVars, php_xdebug
from .pip import PipPackage, PipVars, pip
from .postgresql import PostgresqlVars, postgresql, postgresql_data
from .redis import RedisVars, redis
from .repo_epel import EpelVars, repo_epel
from .repo_remi import RemiVars, repo_remi
from .ruby import GemSpec, RubyVars, ruby
from .security import SecurityVars, security
from .supervisor import Program, SupervisorVars, supervisor
from .swap import SwapVars, swap
from .systemd import SystemdVars, UnitSpec, systemd
from .users import DeletedUserSpec, GroupSpec, UserSpec, UsersVars, users
from .zabbix_agent import ZabbixAgentVars, zabbix_agent

__all__: list[str] = [
    "ApacheOutputs",
    "ApachePhpFpmVars",
    "ApacheVars",
    "Cert",
    "CertbotVars",
    "ClamavVars",
    "ComposerVars",
    "ConfigChange",
    "DaemonizeVars",
    "DatadogVars",
    "DeletedUserSpec",
    "DockerVars",
    "DrushVars",
    "ElasticsearchVars",
    "EpelVars",
    "FirewallVars",
    "ForwardedPort",
    "GemSpec",
    "GitVars",
    "GlusterfsVars",
    "GroupSpec",
    "HelmChecksum",
    "HelmVars",
    "HostsEntry",
    "HostsVars",
    "HtpasswdCredential",
    "HtpasswdVars",
    "JavaVars",
    "KibanaVars",
    "KubectlChecksum",
    "KubectlVars",
    "MailhogVars",
    "MemcachedVars",
    "MinioOutputs",
    "MinioVars",
    "NfsVars",
    "NginxVars",
    "NodeExporterVars",
    "NodejsVars",
    "NpmGlobalPackage",
    "NtpVars",
    "PhpOutputs",
    "PhpVars",
    "PhpVersionsOutputs",
    "PhpVersionsVars",
    "PhpXdebugVars",
    "PipPackage",
    "PipVars",
    "PostgresqlVars",
    "Program",
    "RedisVars",
    "RemiVars",
    "RubyVars",
    "SecurityVars",
    "SupervisorVars",
    "SwapVars",
    "SystemdVars",
    "UnitSpec",
    "UserSpec",
    "UsersVars",
    "ZabbixAgentVars",
    "apache",
    "apache_php_fpm",
    "certbot",
    "clamav",
    "composer",
    "daemonize",
    "datadog",
    "docker",
    "drush",
    "elasticsearch",
    "firewall",
    "git",
    "glusterfs",
    "helm",
    "hosts",
    "htpasswd",
    "java",
    "kibana",
    "kubectl",
    "mailhog",
    "memcached",
    "minio",
    "nfs",
    "nginx",
    "node_exporter",
    "nodejs",
    "ntp",
    "php",
    "php_versions",
    "php_xdebug",
    "pip",
    "postgresql",
    "postgresql_data",
    "redis",
    "repo_epel",
    "repo_remi",
    "ruby",
    "security",
    "supervisor",
    "swap",
    "systemd",
    "users",
    "zabbix_agent",
]
