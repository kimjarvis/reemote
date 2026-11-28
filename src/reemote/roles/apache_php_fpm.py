# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The Apache PHP-FPM role (migrated from ``geerlingguy.apache-php-fpm``).
#
# Enables ``mod_proxy_fcgi`` (and its ``mod_proxy`` prerequisite) for Apache on
# Ubuntu hosts: the two ``mods-available`` → ``mods-enabled`` symlinks, and the
# ``restart apache`` handler they notify. :func:`apache_php_fpm` is the whole
# task list -- ``tasks/main.yml`` is a single two-item ``file`` loop -- and
# :class:`ApachePhpFpmVars` carries the two values the role took from its
# dependency, bound from the inventory entry's ``apache_php_fpm`` role mapping
# by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. The role's own gate is
# ``when: ansible_facts.os_family == 'Debian'``; its RedHat/CentOS branch is a
# README note (RHEL 7 ships mod_proxy_fcgi enabled), not tasks, so nothing on
# that side needs collapsing.
#
# The stream carries two waves on a fresh host, one on a converged rerun
# (design doc section 5.7):
#
# 0. :func:`enable_modules` -- the two ``files.link`` requests -- closed by
#    ``checkpoint("configure done")``, which the handler gate reads.
# 1. :func:`handler` -- ``Restart apache2 (handler)``, only on the hosts the
#    checkpoint selected. On a rerun nothing changed, the checkpoint names
#    nobody, the handler yields no request, and the wave is not appended.
#
# The role ships no ``defaults``, ``vars``, ``handlers``, ``templates`` or
# ``files`` of its own: "Role Variables: None." The two inputs it does use --
# ``apache_server_root`` and the ``restart apache`` handler -- belong to its
# meta-dependency ``geerlingguy.apache``, so they become variables here (D3).
#
# :func:`apache_php_fpm` returns ``None``: no composing role in the corpus
# consumes an output, so there is no outputs dataclass (§17.2).
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: the host-family guard is the body's opening ``if``, not a ``when=`` on
#   the task: a host outside the ``"Ubuntu"`` family returns before it yields
#   anything, so it emits no requests and no log entries at all. The Ubuntu
#   family is the Debian gate narrowed to the one target this corpus fixes.
# * D2: the task's ``mode: 0644`` on a *symlink* is dropped --
#   :func:`~reemote.modules.files.link` owns no mode, and a Linux symlink's own
#   mode is always ``0777`` and meaningless (Ansible's chmod follows the link
#   to the packaged ``0644`` target, so nothing observable changes).
# * D3: reemote has no role-dependency mechanism: ``apache_server_root`` and
#   the ``restart apache`` handler become :attr:`ApachePhpFpmVars.server_root`,
#   :attr:`~ApachePhpFpmVars.service` and
#   :attr:`~ApachePhpFpmVars.restart_state` (the dependency role's
#   ``apache_restart_state``, default ``restarted``), and running Apache is a
#   documented prerequisite -- the ``mods-available/*.load`` files come from
#   the ``apache2`` package. Composing with the migrated
#   :func:`~reemote.roles.apache` role is the dependency's faithful form.
# * D4: the handler is an in-stream ``checkpoint`` + ``changed_hosts`` gate,
#   not a play-end flush: ``restart apache`` is queued only when a symlink was
#   actually created or re-pointed, and ``files.link`` is fact-gated on the
#   ``Link`` fact, so a converged host queues nothing and the handler wave
#   disappears.
# * D5: :func:`~reemote.modules.files.link`'s default ``create_remote_dir=True``
#   ``mkdir -p``s ``mods-enabled`` when it is missing, where Ansible's ``file``
#   task would fail on the absent parent. On a host meeting the prerequisite
#   (D3) the directory already exists and the two shapes agree.
# """

from collections.abc import Generator
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from ..core import op
from ..handlers import changed_hosts, service_handler
from ..hosts import Host
from ..modules import files
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, checkpoint

#: ``tasks/main.yml``'s ``with_items`` verbatim: enabling ``mod_proxy_fcgi``
#: on Debian/Ubuntu means symlinking its ``.load`` file -- and mod_proxy's
#: own, which it LoadFile-depends on -- from ``mods-available`` to
#: ``mods-enabled``, exactly what ``a2enmod proxy proxy_fcgi`` does.
PROXY_MODULES = ("proxy.load", "proxy_fcgi.load")


class ApachePhpFpmVars(BaseModel):
    """The role's inputs -- the dependency role's values, unprefixed.

    The upstream role declares no variables of its own; these three carry
    what it read from ``geerlingguy.apache`` (D3).
    :func:`~reemote.rolevars.resolve_vars` binds the inventory entry's
    ``apache_php_fpm`` role mapping, so ``server_root`` there sets
    :attr:`server_root`. The model is frozen and hashable (design doc
    section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``apache_server_root`` on Debian: the root the two ``mods-*``
    #: directories sit under.
    server_root: str = "/etc/apache2"

    #: The unit the ``restart apache`` handler acts on
    #: (``apache_service.name`` on Debian).
    service: str = "apache2"

    #: The handler's verb -- the dependency role's ``apache_restart_state``.
    #: ``restarted``/``reloaded`` are stateless (they always report changed),
    #: faithful to Ansible's service module; ``started`` only starts a
    #: stopped unit.
    restart_state: Literal["restarted", "reloaded", "started"] = "restarted"


def notifying_names() -> tuple[str, ...]:
    """Every request name that queues ``restart apache``.

    Derived from the same frozen :data:`PROXY_MODULES` the deploy is built
    from, so the notification set matches the requests exactly. A rename in
    :func:`enable_modules` without one here silently empties the gate.
    """
    return tuple(f"Enable Apache module {mod}" for mod in PROXY_MODULES)


def enable_modules(v: ApachePhpFpmVars) -> Deploy:
    """``tasks/main.yml``'s single ``file`` loop.

    Each item is a ``mods-available`` → ``mods-enabled`` symlink, the
    Debian-apache2 spelling of ``a2enmod``. :func:`~reemote.modules.files.link`
    reads the ``Link`` fact and yields ``ln -s`` only when the link is missing
    or points elsewhere, so the pair converges; the mode the Ansible task
    asked for is meaningless on a symlink and is dropped (D2). The paths are
    root-owned, so the requests elevate.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for mod in PROXY_MODULES:
            yield op(
                files.link,
                name=f"Enable Apache module {mod}",
                path=f"{v.server_root}/mods-enabled/{mod}",
                target=f"{v.server_root}/mods-available/{mod}",
                sudo=True,
            )

    return deploy


def handler(v: ApachePhpFpmVars, notified: frozenset[str]) -> Deploy:
    """The dependency role's ``restart apache`` handler, on notified hosts.

    Upstream this handler lives in ``geerlingguy.apache`` and runs at play
    end; here the role owns an equivalent one, gated on its own checkpoint
    (D3, D4). ``started`` maps to :func:`~reemote.handlers.service_handler`'s
    ``"running"`` verb; an empty ``notified`` set yields no request, which is
    the converged no-op Ansible's handler gives for free.
    """
    action = "running" if v.restart_state == "started" else v.restart_state
    return service_handler(notified, (v.service,), action=action)


def apache_php_fpm(v: VarsSet[ApachePhpFpmVars]) -> Deploy:
    """Enable ``mod_proxy_fcgi`` for Apache on Ubuntu hosts.

    Args:
        v: The role's resolved variables, one :class:`ApachePhpFpmVars` per
            host (``resolve_vars(ApachePhpFpmVars, "apache_php_fpm",
            inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves` that returns ``None`` for
        every host it runs -- no composing role in the corpus consumes an
        output (§17.2).
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return None

        # Wave 0: the two symlinks, then read the wave's own outcomes. The
        # checkpoint must immediately follow the stage it reports on; one
        # placed after the handler wave would read the handler's entries and
        # notify nobody.
        yield from enable_modules(hv)(host)
        wave_report = yield checkpoint("configure done")

        notified = changed_hosts(wave_report, notifying_names())

        # Wave 1: the handler, only on the hosts the checkpoint selected.
        yield from handler(hv, notified)(host)

        return None

    return deploy


__all__ = ["ApachePhpFpmVars", "apache_php_fpm"]
