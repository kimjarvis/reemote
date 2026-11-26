# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The PHP Xdebug role (migrated from ``geerlingguy.php-xdebug``).
# 
# Builds and enables the Xdebug PHP extension on Ubuntu hosts: the build toolchain
# and the module directory, the source tarball extracted into a workspace, the
# ``phpize && ./configure && make`` build, the module moved into PHP's extension
# directory, the rendered ``xdebug.ini`` written into every extension conf path,
# the optional CLI disable, and the role's two chained handlers (``restart
# webserver`` -> ``restart php-fpm``). :func:`php_xdebug` is the whole task list and
# :class:`PhpXdebugVars` is ``defaults/main.yml``, bound from the inventory
# entry's ``php_xdebug`` role mapping by
# :func:`~reemote.rolevars.resolve_vars`.
# 
# **The dependency is a `yield from` plus a return value** (design doc section
# 10.4). This role does not re-implement any part of :mod:`reemote.roles.php` and
# does not look it up in a registry: the caller passes php's own ``VarsSet`` as an
# argument, the body runs php for this host with ``yield from``, and reads
# :class:`~reemote.roles.php.PhpOutputs` -- the resolved PHP version and the
# extension conf paths -- as ordinary data. That return value is the whole
# dependency story, and it is what the old per-role copy of php's version table and
# ``php_version`` / ``php_extension_conf_paths`` reconstruction was deleted in
# favour of (design doc section 15).
# 
# Target: hosts in the ``"Ubuntu"`` OS family, inherited rather than restated --
# php's own family guard returns ``PhpOutputs(version=None)`` before its first
# barrier, and this body returns on that ``None``. So a host outside the family
# emits nothing at all, and no request here needs a ``when`` guard.
# 
# The stream continues php's. After php's last wave (its handler), this role adds:
# 
# * ``setup`` -- ``apt.packages`` (build-essential), ``files.directory`` (the module
#   path) and ``files.unarchive`` (the tarball, ``creates=src_dir``) -- requests that
#   share php's handler wave, because no barrier separates the two.
# * ``fact(FileExists, built_so)`` -- closes that wave. The build is gated on it, so
#   the branch lives *inside* the body: the read happens after the extraction
#   applied, which is exactly why it cannot be hoisted (design doc section 6.2).
# * ``act(Shell, ...)`` -- ``phpize && ./configure && make`` as root, only on a host
#   where the built module is absent. Its non-zero exit is data, so the role raises
#   :class:`~reemote.CommandError` itself.
# * ``install`` -- the module move, the ini writes, the optional CLI removal --
#   closed by ``checkpoint("xdebug install done")``.
# * ``handler`` -- the restarts, for the hosts that one checkpoint selected.
# 
# **One checkpoint, two name sets.** Both :func:`~reemote.changed_hosts` reads --
# :data:`WEBSERVER_NOTIFY_NAMES` and :data:`FPM_NOTIFY_NAMES` -- come out of the
# *same* report, because every notifying task is in the one ``install`` deploy. Two
# checkpoints would read the second one an empty wave. And php's own handler wave
# already ran inside the ``yield from``: a checkpoint can only read the wave it
# closes, so this one cannot see php's notifications -- which is what keeps the
# webserver from being restarted on php's account. Merging :attr:`PhpOutputs.webserver_notified`
# into the set below would be a faithful-looking way to lose that separation, and it
# is deliberately not done (design doc section 18.7).
# 
# No template engine is needed: ``templates/xdebug.ini.j2`` is pure variable
# substitution, so :func:`render_xdebug_ini` is a plain f-string. The role needs no
# ``reemote[templates]`` extra.
# 
# Deviations from the Ansible role, all deliberate:
# 
# * ``php_xdebug_enable`` is new. The role has no such variable -- an operator who
#   wanted Xdebug off removed the role from the play -- so a deploy-level switch has
#   no Ansible name to match. It is the knob section 10.4's body opens with, and it
#   keeps that shape: ``False`` returns before the dependency even runs, so a
#   disabled host pays for none of php's barriers.
# * the Ubuntu PHP version table and the ``/etc/php/<version>/...`` conf-path
#   reconstruction the example carried are **gone**, replaced by
#   :attr:`PhpOutputs.version` and :attr:`PhpOutputs.extension_conf_paths`. This is
#   the falsifier of the whole return-value channel: if this module had to keep
#   them, the channel would be carrying nothing.
# * the ini destinations come from php's resolved extension conf paths, but
#   :attr:`PhpXdebugResolution.cli_ini` still hardcodes ``/etc/php/<version>/cli/
#   conf.d`` from the PHP version, because that is what the role's own
#   ``{{ php_version }}`` interpolation does -- an override of
#   ``php_extension_conf_paths`` moves the writes but not the removal. Preserved
#   verbatim rather than "fixed".
# * the build is an ``act`` barrier inside the stream, not ``gather_action`` between
#   two ``run()`` calls. The example grouped hosts by their rendered command; a
#   per-host body has one command and needs no grouping.
# * the version table's fallback (Debian's 8.2) and every ``__php_xdebug_*``
#   ``set_fact`` stay, but the version *lookup* is php's:
#   ``php_default_version_debian`` and the distribution read are not repeated here.
# * ``files.unarchive`` resolves the role's ``creates:`` gate in-operation, so the
#   extraction is one request and a converged host never fetches the tarball. The
#   ``mode=0755`` the role puts on the extraction is not passed: the covering
#   ``chmod -R`` in the role targets the shared workspace, not the archive.
# * the Xdebug-2 directive names (``xdebug.remote_enable`` and friends, not the
#   Xdebug-3 ``xdebug.client_*`` forms) are the role's own and are kept verbatim,
#   even though the default version is Xdebug 3. Changing them would silently
#   rewrite every operator's tuned ini.
# * the role's variable validation becomes pydantic validation: the render values
#   are ``str`` fields with ``coerce_numbers_to_str``, so the YAML ``1`` the role
#   defaults to renders ``1`` and a JSON ``true`` is refused rather than rendering
#   ``True`` where the template's own string default renders ``true``.
# """

import shlex
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..actions import Shell
from ..core import op
from ..handlers import changed_hosts, service_handler
from ..hosts import CommandError, Host
from ..modules import apt, files
from ..modules.files import FileExists
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, act, checkpoint, fact
from .php import FPM_HANDLER_ACTIONS, PhpOutputs, PhpVars, daemon_names, php

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path only) ---

#: ``vars/Debian.yml``'s ``__php_xdebug_module_path``, used when
#: ``php_xdebug_module_path`` is undefined. The role ships ``php7`` here on
#: purpose; on a modern PHP 8.3 host the real extension dir differs, so the
#: variable exists -- the default is preserved verbatim, not corrected.
DEBIAN_MODULE_PATH = "/usr/lib/php7/modules"

#: ``vars/Debian.yml``'s ``__php_xdebug_config_filename``.
DEBIAN_CONFIG_FILENAME = "20-xdebug.ini"

#: The tarball's home. ``php_xdebug_version`` is interpolated into it, so the
#: role's ``https://xdebug.org/files/`` download is reproduced exactly.
TARBALL_URL = "https://xdebug.org/files/xdebug-{version}.tgz"

#: The notifying request names -- the role's task names, verbatim (trailing
#: periods included). ``Move Xdebug module into place.`` (tasks/main.yml) queues
#: ``restart webserver`` only; the two ``configure.yml`` tasks queue both
#: handlers, which is why the FPM set is a subset rather than a copy. They are
#: module constants so one checkpoint's report can be read through both.
WEBSERVER_NOTIFY_NAMES = frozenset(
    {
        "Move Xdebug module into place.",
        "Copy xdebug ini into main extension config folder.",
        "Disable xdebug for PHP CLI.",
    }
)
FPM_NOTIFY_NAMES = frozenset(
    {
        "Copy xdebug ini into main extension config folder.",
        "Disable xdebug for PHP CLI.",
    }
)


# --- variables ---------------------------------------------------------------


class PhpXdebugVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare, so
    ``resolve_vars(PhpXdebugVars, "php_xdebug", inventory)`` binds the entry's
    ``php_xdebug`` role mapping: ``version`` there sets :attr:`version`.
    The dependency's variables -- the PHP version, the
    conf paths, the two daemon names and the two FPM handler gates -- are
    **not** fields here: they are read from :mod:`reemote.roles.php` through its
    :class:`~reemote.roles.php.PhpOutputs` and its own ``VarsSet``, so this model
    cannot drift out of step with the role it extends.

    The render fields are ``str`` with ``coerce_numbers_to_str``, matching the
    role's habit of interpolating them into ``xdebug.ini`` verbatim:
    ``php_xdebug_coverage_enable: 1`` renders ``1``, and a JSON ``true`` is
    refused instead of rendering ``True`` where the shipped default string
    renders ``true``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", coerce_numbers_to_str=True)

    #: New knob, not an Ansible variable (see the deviations above): ``False``
    #: skips the role, dependency included.
    enable: bool = True

    #: The Xdebug release to build. Dotted digits, because it names both a
    #: directory (``<workspace>/xdebug-<version>``) and a file
    #: (``xdebug-<version>.so``).
    version: str = "3.3.2"
    #: Where the source is extracted (``workspace`` in ``defaults/main.yml``).
    workspace: str = "/root"
    #: ``null`` falls back to the role's ``__php_xdebug_module_path``.
    module_path: str | None = None
    #: ``null`` falls back to the role's ``__php_xdebug_config_filename``.
    config_filename: str | None = None
    #: Removes the CLI ini after writing it, so the debugger does not attach to
    #: every ``php`` invocation.
    cli_disable: bool = False

    # --- xdebug.ini (the role's defaults, verbatim, raw-rendered) ------------
    coverage_enable: str = "1"
    default_enable: str = "1"
    remote_enable: str = "false"
    remote_connect_back: str = "false"
    remote_host: str = "localhost"
    remote_port: str = "9000"
    remote_log: str = "/tmp/xdebug.log"
    remote_autostart: str = "false"
    idekey: str = "sublime.xdebug"
    max_nesting_level: str = "256"

    @field_validator("version")
    @classmethod
    def _version_shape(cls, value: str) -> str:
        """The role's ``version`` must be dotted digits, as the paths assume."""
        parts = value.split(".")
        if not parts or not all(part.isdigit() for part in parts):
            raise ValueError(f"php_xdebug_version must be dotted digits, got {value!r}")
        return value

    @field_validator("workspace")
    @classmethod
    def _workspace_non_empty(cls, value: str) -> str:
        """An empty workspace would extract into ``/xdebug-...``."""
        if not value:
            raise ValueError("php_xdebug_workspace must be a non-empty string")
        return value


# --- frozen per-host resolution ---------------------------------------------


@dataclass(frozen=True)
class PhpXdebugResolution:
    """One host's resolved Xdebug build, frozen before the first request.

    Built by :func:`resolve_xdebug` from this role's variables **and** php's
    published outputs, so every path the deploys and the build action need is
    decided once. The three values the example inherited by hand -- the PHP
    version, the extension conf paths and the two daemon names -- arrive as
    arguments here, which is the return-value channel doing its job.

    ``ini_dests`` is a tuple of already-rendered destinations: the CLI filter is a
    resolution-time decision, not a deploy-time one, so the deploy is a pure
    function of this object and both determinism passes see one sequence.
    """

    version: str
    php_version: str
    workspace: str
    module_path: str
    config_filename: str
    ext_paths: tuple[str, ...]
    src_dir: str
    built_so: str
    installed_so: str
    tarball_url: str
    cli_disable: bool
    ini_dests: tuple[str, ...]
    cli_ini: str
    ini_content: str
    webserver_daemon: str
    fpm_daemon: str
    fpm_handler_action: str


# --- pure helpers ------------------------------------------------------------


def render_xdebug_ini(v: PhpXdebugVars, module_path: str, xversion: str) -> str:
    """``templates/xdebug.ini.j2`` -- pure substitution, verbatim.

    The template has no block tags or filters, so Ansible's ``trim_blocks`` /
    ``keep_trailing_newline`` defaults leave it a straight interpolation; the
    f-string reproduces it byte-for-byte, trailing newline included. The
    Xdebug-2 directive names on a Xdebug-3 build are the role's own and are kept
    (see the module docstring).
    """
    return f"""[XDebug]
zend_extension="{module_path}/xdebug-{xversion}.so"

xdebug.coverage_enable={v.coverage_enable}
xdebug.default_enable={v.default_enable}

xdebug.remote_enable={v.remote_enable}
xdebug.remote_connect_back={v.remote_connect_back}
xdebug.remote_host={v.remote_host}
xdebug.remote_port={v.remote_port}
xdebug.remote_log={v.remote_log}
xdebug.remote_autostart={v.remote_autostart}

xdebug.idekey="{v.idekey}"

xdebug.max_nesting_level={v.max_nesting_level}
"""


def resolve_xdebug(hv: PhpXdebugVars, php_out: PhpOutputs, pv: PhpVars) -> PhpXdebugResolution:
    """Combine this role's variables with php's outputs into one frozen set.

    Args:
        hv: This host's :class:`PhpXdebugVars`.
        php_out: What :func:`~reemote.roles.php.php` returned for this host. The
            caller has already rejected ``version is None``, so the two optional
            fields are present here.
        pv: This host's :class:`~reemote.roles.php.PhpVars`, for the handler
            daemon names and the FPM handler verb.
    """
    php_version = php_out.version or ""
    ext_paths = php_out.extension_conf_paths
    config_filename = hv.config_filename or DEBIAN_CONFIG_FILENAME
    module_path = hv.module_path or DEBIAN_MODULE_PATH
    src_dir = f"{hv.workspace}/xdebug-{hv.version}"
    webserver_daemon, fpm_daemon = daemon_names(pv, php_version)
    # configure.yml's template ``when``: an ext path is written unless it is the
    # CLI path and cli_disable is set. The CLI removal still targets the *default*
    # CLI conf path, because the role interpolates ``{{ php_version }}`` rather
    # than reading ``php_extension_conf_paths``.
    ini_dests = tuple(
        f"{ext}/{config_filename}" for ext in ext_paths if "cli" not in ext or not hv.cli_disable
    )
    return PhpXdebugResolution(
        version=hv.version,
        php_version=php_version,
        workspace=hv.workspace,
        module_path=module_path,
        config_filename=config_filename,
        ext_paths=tuple(ext_paths),
        src_dir=src_dir,
        built_so=f"{src_dir}/modules/xdebug.so",
        installed_so=f"{module_path}/xdebug-{hv.version}.so",
        tarball_url=TARBALL_URL.format(version=hv.version),
        cli_disable=hv.cli_disable,
        ini_dests=ini_dests,
        cli_ini=f"/etc/php/{php_version}/cli/conf.d/{config_filename}",
        ini_content=render_xdebug_ini(hv, module_path, hv.version),
        webserver_daemon=webserver_daemon,
        fpm_daemon=fpm_daemon,
        fpm_handler_action=FPM_HANDLER_ACTIONS[pv.fpm_handler_state],
    )


def build_command(rv: PhpXdebugResolution) -> str:
    """The build action's shell: ``cd <src_dir> && phpize && ./configure && make``.

    The role's ``command:`` loop runs the three items in order with
    ``chdir=src_dir`` and stops on the first failure -- an ``&&`` chain does exactly
    that. ``Shell`` with ``sudo=True`` renders ``"sudo " + command`` verbatim, with
    no ``sh -c`` wrapper of its own, so the chain is wrapped explicitly here: the
    command that reaches the host (and the simulation seed) is ``sudo sh -c 'cd
    ... && phpize && ./configure && make'``.
    """
    inner = f"cd {shlex.quote(rv.src_dir)} && phpize && ./configure && make"
    return f"sh -c {shlex.quote(inner)}"


# --- deploys -----------------------------------------------------------------


def setup(rv: PhpXdebugResolution) -> Deploy:
    """The toolchain, the module directory and the source extraction.

    :func:`~reemote.modules.files.unarchive` is the role's ``unarchive:`` task as
    one request: the URL is fetched control-side as binary, spooled, extracted and
    cleaned up, with ``creates=src_dir`` resolving Ansible's ``creates:`` gate
    in-operation -- a converged host is a no-op and never fetches. The module
    directory is created here, an earlier barrier than the copy that needs its
    parent to exist.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            apt.packages,
            name="Ensure dependencies for building from source are installed (Debian).",
            packages=("build-essential",),
            present=True,
            sudo=True,
        )
        yield op(
            files.directory,
            name="Ensure Xdebug module path exists.",
            path=rv.module_path,
            mode="0755",
            user="root",
            group="root",
            sudo=True,
        )
        yield op(
            files.unarchive,
            name="Untar Xdebug.",
            src=rv.tarball_url,
            dest=rv.workspace,
            creates=rv.src_dir,
            sudo=True,
        )

    return deploy


def install(rv: PhpXdebugResolution) -> Deploy:
    """Install the built module and configure PHP to load it.

    :func:`~reemote.modules.files.copy` is the role's ``copy: remote_src=true``
    task as one request: a file ``dest`` renames ``xdebug.so`` to
    ``xdebug-<version>.so``, ``mode``/``user``/``group`` converge in the same
    request, and ``overwrite=True`` is sha1-gated (Ansible's ``force: yes``: a
    drifted target converges once, a repeat is a no-op). The source must already
    exist -- the build action is the barrier before this deploy. The ini installs
    and the optional CLI disable are ``configure.yml``'s two tasks, and their
    names are the two notification sets a checkpoint reads after them.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            files.copy,
            name="Move Xdebug module into place.",
            src=rv.built_so,
            dest=rv.installed_so,
            overwrite=True,
            mode="0644",
            user="root",
            group="root",
            sudo=True,
        )
        for dest in rv.ini_dests:
            yield op(
                files.install,
                name="Copy xdebug ini into main extension config folder.",
                content=rv.ini_content,
                dest=dest,
                mode="0644",
                user="root",
                group="root",
                sudo=True,
            )
        if rv.cli_disable:
            # The CLI conf.d entry is a symlink on a stock host, so force=True:
            # the ``File`` fact reads a symlink as a non-file.
            yield op(
                files.file,
                name="Disable xdebug for PHP CLI.",
                path=rv.cli_ini,
                present=False,
                force=True,
                sudo=True,
            )

    return deploy


def handler(
    rv: PhpXdebugResolution,
    webserver_hosts: frozenset[str],
    fpm_hosts: frozenset[str],
) -> Deploy:
    """``handlers/main.yml``: ``restart webserver``, then chained ``restart php-fpm``.

    The chain collapses to an upstream-first service listing (the documented
    handler-chaining recipe): this host's webserver request is yielded before its
    FPM request, which is all the notification chain guaranteed. The caller has
    already applied the dependency's own gates -- ``php_enable_webserver`` for the
    webserver set, ``php_enable_php_fpm`` and ``php_fpm_state == 'started'`` for
    the FPM set, which unions the direct configure notifications with the webserver
    chain -- and :func:`~reemote.handlers.service_handler` acts only when
    ``host.name`` is in the set it is given. An empty set yields no request, the
    converged no-op Ansible's handler gives for free.
    """
    webserver = service_handler(webserver_hosts, (rv.webserver_daemon,), action="restarted")
    fpm = service_handler(fpm_hosts, (rv.fpm_daemon,), action=rv.fpm_handler_action)

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield from webserver(host)
        yield from fpm(host)

    return deploy


# --- the role ----------------------------------------------------------------


def php_xdebug(v: VarsSet[PhpXdebugVars], php_v: VarsSet[PhpVars]) -> Deploy:
    """Build and enable Xdebug on Ubuntu hosts, running php as its dependency.

    Args:
        v: The role's resolved variables, one :class:`PhpXdebugVars` per host
            (``resolve_vars(PhpXdebugVars, "php_xdebug", inventory)``).
        php_v: The *dependency's* resolved variables, one
            :class:`~reemote.roles.php.PhpVars` per host. This is the channel
            section 10.4 is about: an ordinary argument, not a lookup, and the
            reason a caller that also runs php on its own passes the same
            ``VarsSet`` to both. Each role reads only its own mapping in the
            entry's ``roles`` dict, so ``php`` and ``php_xdebug`` never see
            each other's keys.

    Returns:
        A deploy for :func:`~reemote.run_waves` that returns ``None``. What the
        dependency publishes travels inside the body, not out of it.

    Raises:
        ValueError: ``php_xdebug_version`` is not dotted digits, or the workspace
            is empty. Raised by pydantic while the variables are resolved, so
            before any host is contacted.
        CommandError: The build action exited non-zero. Raised in the body, from
            the barrier's own ``Result`` -- an ``act`` barrier never raises on a
            failing exit status, so the check belongs to the role.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if not hv.enable:
            return

        # The dependency: run php inline for this host and take its outputs.
        # There is no registry and no collision detection -- the caller passes
        # php's variables explicitly, which is the whole de-duplication story.
        # The local must not be called `php`: that would shadow the factory
        # for the whole function body and raise UnboundLocalError here.
        php_out = yield from php(php_v)(host)
        if php_out.version is None:
            # Outside the target family: php never resolved a version, so there
            # are no conf paths to write an ini into.
            return

        rv = resolve_xdebug(hv, php_out, php_v.for_host(host))

        # Wave: the toolchain, the module directory, the extraction. This host's
        # ini destinations were decided from php's extension conf paths above.
        yield from setup(rv)(host)

        # Barrier, then the branch it gates. The source tree exists once the
        # extraction has applied, but modules/xdebug.so does not until the build
        # runs -- so the read is *here*, after the wave it depends on, and the
        # body branches on its answer. That is the point of the barrier model,
        # not a determinism bug to be tidied away (design doc section 6.2).
        built = yield fact(FileExists, rv.built_so)
        if not built:
            command = build_command(rv)
            result = yield act(Shell, command=command, name="build xdebug", sudo=True)
            # A barrier never raises on a non-zero exit -- the Result is data --
            # so the example's post-gather check moves into the body.
            if result["exit_status"] != 0:
                raise CommandError(host.name, command, result["exit_status"], result["stderr"])

        # Wave: the module move, the ini writes, the CLI removal -- closed by the
        # one checkpoint this role needs. Its name carries the role because php's
        # own ``install done`` sits in the same unscoped stream.
        yield from install(rv)(host)
        install_report = yield checkpoint("xdebug install done")

        # One report, two name sets: every notifying task is in the deploy above,
        # so a second checkpoint would read an empty wave. php's own handler wave
        # is not in this report and is deliberately not borrowed from it.
        webserver_notified = changed_hosts(install_report, WEBSERVER_NOTIFY_NAMES)
        fpm_direct = changed_hosts(install_report, FPM_NOTIFY_NAMES)

        # The dependency's gates, read from the dependency's variables: which
        # daemon a restart names came from php too.
        pv = php_v.for_host(host)
        webserver_hosts = webserver_notified if pv.enable_webserver else frozenset()
        fpm_hosts = frozenset()
        if pv.enable_php_fpm and pv.fpm_state == "started":
            fpm_hosts = webserver_hosts | fpm_direct

        # Wave: the handlers, only on the hosts that checkpoint selected.
        yield from handler(rv, webserver_hosts, fpm_hosts)(host)

    return deploy


__all__ = ["PhpXdebugVars", "php_xdebug"]
