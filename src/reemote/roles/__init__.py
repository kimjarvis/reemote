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

The template-rendering roles (``apache``, ``datadog``, ``firewall``,
``memcached``, ``nginx``, ``ntp``, ``postgresql``, ``redis``, ``systemd``) import
jinja2 and PyYAML only
through :mod:`reemote.roles._render`, the single import site for them, which needs
the ``reemote[templates]`` extra (§17.4). Nothing in this package pulls that module
in at import time, so ``import reemote.roles`` stays clean without the extra.
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
from .certbot import Cert, CertbotVars, certbot
from .composer import ComposerVars, composer
from .datadog import DatadogVars, datadog
from .docker import DockerVars, docker
from .drush import DrushVars, drush
from .firewall import FirewallVars, ForwardedPort, firewall
from .git import GitVars, git
from .glusterfs import GlusterfsVars, glusterfs
from .hosts import HostsEntry, HostsVars, hosts
from .java import JavaVars, java
from .kubectl import KubectlChecksum, KubectlVars, kubectl
from .memcached import MemcachedVars, memcached
from .nfs import NfsVars, nfs
from .nginx import NginxVars, nginx
from .nodejs import NodejsVars, NpmGlobalPackage, nodejs
from .ntp import NtpVars, ntp
from .php import PhpOutputs, PhpVars, php
from .php_xdebug import PhpXdebugVars, php_xdebug
from .pip import PipPackage, PipVars, pip
from .postgresql import PostgresqlVars, postgresql, postgresql_data
from .redis import RedisVars, redis
from .repo_epel import EpelVars, repo_epel
from .security import SecurityVars, security
from .systemd import SystemdVars, UnitSpec, systemd
from .users import DeletedUserSpec, GroupSpec, UserSpec, UsersVars, users

__all__: list[str] = [
    "ApacheOutputs",
    "ApacheVars",
    "Cert",
    "CertbotVars",
    "ComposerVars",
    "DatadogVars",
    "DeletedUserSpec",
    "DockerVars",
    "DrushVars",
    "EpelVars",
    "FirewallVars",
    "ForwardedPort",
    "GitVars",
    "GlusterfsVars",
    "GroupSpec",
    "HostsEntry",
    "HostsVars",
    "JavaVars",
    "KubectlChecksum",
    "KubectlVars",
    "MemcachedVars",
    "NfsVars",
    "NginxVars",
    "NodejsVars",
    "NpmGlobalPackage",
    "NtpVars",
    "PhpOutputs",
    "PhpVars",
    "PhpXdebugVars",
    "PipPackage",
    "PipVars",
    "PostgresqlVars",
    "RedisVars",
    "SecurityVars",
    "SystemdVars",
    "UnitSpec",
    "UserSpec",
    "UsersVars",
    "apache",
    "certbot",
    "composer",
    "datadog",
    "docker",
    "drush",
    "firewall",
    "git",
    "glusterfs",
    "hosts",
    "java",
    "kubectl",
    "memcached",
    "nfs",
    "nginx",
    "nodejs",
    "ntp",
    "php",
    "php_xdebug",
    "pip",
    "postgresql",
    "postgresql_data",
    "redis",
    "repo_epel",
    "security",
    "systemd",
    "users",
]
