# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The Composer role (migrated from ``geerlingguy.composer``).
#
# Installs `Composer <https://getcomposer.org/>`_, the PHP dependency manager, on
# Ubuntu hosts: the signature-verified installer download, the installer run, the
# phar move into ``/usr/local/bin/composer``, the ``~/.composer`` home directory,
# the optional GitHub OAuth ``auth.json``, the globally-required packages and the
# ``/etc/profile.d/composer.sh`` PATH entry. :func:`composer` is the whole task
# list and :class:`ComposerVars` is ``defaults/main.yml``, bound from the
# inventory's ``composer_``-prefixed keys by
# :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. A host outside the target family
# returns before its first ``yield`` and emits no requests, no barriers and no
# log lines at all.
#
# The stream is one deploy body carrying ``fact`` gates, one ``control`` barrier
# and ``act`` barriers -- no checkpoint and no handler, because nothing reads an
# outcome (the role ships no ``handlers/main.yml`` and notifies nothing). The wave
# count grows with the configured package list: for a fresh host with ``P`` global
# packages it is ``4 + 2*P + 1`` -- one ``fact(FileExists, composer_path)`` gate,
# one ``fact(server.Home)`` for the tilde expansion, ``P`` ``fact(FileExists,
# home/vendor/<name>)`` creates-gates, one ``control(files.fetch_url)`` signature
# fetch, the installer download request closed by the ``act`` installer run, the
# place/home/auth requests closed by the first ``act`` global-require, one require
# action per missing package, and a final wave holding the profile entry. A
# converged host skips the whole installer half (``control`` included) and every
# require action, so it collapses to ``2 + P`` fact waves plus one request wave.
#
# Deviations from the Ansible role, all deliberate:
#
# * the installer signature and the installer itself are fetched **control-side**
#   (``files.fetch_url`` / ``files.download``); ``uri``/``get_url`` fetch on the
#   target. The control machine needs egress to ``composer.github.io`` and
#   ``getcomposer.org``; the host downloads nothing. (The global-require step
#   *does* download on the host -- that is Composer itself running, exactly as in
#   the role.)
# * the signature fetch is a per-host ``control`` barrier gated on this host's
#   ``need_install`` (section 18.8's recommendation), not an inventory-wide
#   ``async def`` factory fetch: a host that already has the composer binary never
#   touches the network, and the factory stays synchronous.
# * every elevated request carries ``sudo=True`` explicitly; the role's
#   ``become_user: {{ composer_home_owner }}`` maps to the inventory ``privilege``
#   section (a non-root ``composer_home_owner`` needs ``privilege.sudo_user``),
#   never to an operation argument. The installer download and run stay
#   unprivileged -- the role's ``get_url``/``command`` tasks carry no ``become``
#   and write only to ``/tmp``.
# * ``mv /tmp/composer.phar {{ composer_path }}`` becomes ``files.copy`` (rename
#   form, mode 0755) plus a paired ``files.file(present=False)`` cleanup: copy is
#   sha1-idempotent but keeps the source, so the cleanup reproduces ``mv``'s source
#   consumption.
# * ``php_executable`` -- the role's only **unprefixed** variable (a ``set_fact``
#   default) -- is read raw from ``host.extra`` rather than bound by
#   ``resolve_vars``; giving it a ``composer_`` prefix would break the role's
#   inventory contract (the datadog probe-config precedent).
# * the two opt-in branches are **rejected** at variable-resolution time, before
#   any host is contacted: ``composer_keep_updated`` (self-update is stateless and
#   never converges) and ``composer_add_project_to_path`` (the ``project-bin.yml``
#   branch). The role's ``fail:`` xor message is reproduced when a version pin and
#   ``keep_updated`` are both set.
# * the two templates are rendered control-side with ``json.dumps`` / an f-string
#   (reemote ships no Jinja2, and neither template needs it -- no ``templates``
#   extra); the profile entry uses the *resolved absolute* home where the Jinja2
#   template interpolates the raw ``~/.composer``.
# * ``composer_global_packages`` defaults to the role's own ``[]``; the old
#   example's ``phpunit/phpunit`` demo default was an example artifact, so a
#   program that wants the global-require path demonstrated passes the packages in
#   the inventory.
# """

import json
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ..actions import Shell
from ..core import op
from ..hosts import CommandError, Host
from ..modules import files, server
from ..modules.files import FileExists
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, act, control, fact

# --- role constants (defaults/main.yml, default path only) --------------------

#: The installer signature and installer URLs (hardcoded in ``tasks/main.yml``).
SIGNATURE_URL = "https://composer.github.io/installer.sig"
INSTALLER_URL = "https://getcomposer.org/installer"

#: The installer's ``/tmp`` staging paths (``tasks/main.yml``) and the profile
#: entry's destination.
INSTALLER_PATH = "/tmp/composer-installer.php"
PHAR_PATH = "/tmp/composer.phar"
PROFILE_PATH = "/etc/profile.d/composer.sh"

#: The role's ``set_fact`` default for its one unprefixed variable, read raw from
#: ``host.extra`` (see the deviations above).
PHP_EXECUTABLE_DEFAULT = "php"


# --- variables ---------------------------------------------------------------


class ComposerVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names carry no ``composer_`` prefix -- the prefix passed to
    :func:`~reemote.rolevars.resolve_vars` binds ``composer_path`` in the
    inventory to :attr:`path`, so an inventory written for the role needs no
    change. The role's one *unprefixed* variable, ``php_executable``, is **not** a
    field here: ``resolve_vars`` skips keys that do not carry the prefix, so it
    stays in ``host.extra`` and :func:`resolve_settings` reads it raw.

    The model is frozen, so it stays hashable through the deploy body (a
    :class:`~reemote.rolevars.VarsSet` is closed over by the factory). The two
    opt-in branches are rejected here, at resolution time, so a bad setting fails
    fast before any host is contacted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Where the phar lands; must be absolute. Its parent directory must exist
    #: (the role's ``mv`` assumes the same).
    path: str = "/usr/local/bin/composer"
    #: Scoped out: ``true`` fails fast (the self-update branch never converges).
    keep_updated: bool = False
    #: An exact version pin, applied as the installer's ``--version=`` flag only
    #: when :attr:`version_branch` is ``""`` (the role's ``{% if %}...{% elif %}``).
    version: str = ""
    #: The installer's major-version branch flag; wins over :attr:`version`.
    version_branch: str = "--2"
    #: The composer home; ``~`` expands to :attr:`home_owner`'s home (gathered via
    #: ``server.Home``), an absolute path is used as-is.
    home_path: str = "~/.composer"
    #: The home's owner; a non-root owner needs the inventory ``privilege.sudo_user``.
    home_owner: str = "root"
    #: The home's group.
    home_group: str = "root"
    #: Packages to ``composer global require``, as ``(name, release)`` pairs. The
    #: inventory may carry the role's list-of-``{name, release}`` shape; the
    #: validator flattens it (release defaults to ``@stable``). The role's own
    #: default is ``[]``.
    global_packages: tuple[tuple[str, str], ...] = ()
    #: Whether ``/etc/profile.d/composer.sh`` is written (only reached when packages
    #: are configured, as in the role).
    add_to_path: bool = True
    #: Scoped out: ``true`` fails fast (the ``project-bin.yml`` branch).
    add_project_to_path: bool = False
    #: When non-empty, ``auth.json`` is installed into the composer home.
    github_oauth_token: str = ""

    @field_validator("path")
    @classmethod
    def _path_absolute(cls, value: str) -> str:
        """Reject a relative or empty ``composer_path``: it would land elsewhere."""
        if not isinstance(value, str) or not value:
            raise ValueError(f"composer_path must be a non-empty string, got {value!r}")
        if not value.startswith("/"):
            raise ValueError(f"composer_path must be absolute, got {value!r}")
        return value

    @field_validator("home_owner", "home_group")
    @classmethod
    def _owner_non_empty(cls, value: str) -> str:
        """An empty owner/group would chown to nothing."""
        if not isinstance(value, str) or not value:
            raise ValueError(f"composer_home_owner/group must be non-empty, got {value!r}")
        return value

    @field_validator("home_path")
    @classmethod
    def _home_path_form(cls, value: str) -> str:
        """``composer_home_path`` must be absolute or start with ``~``."""
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"composer_home_path must be a non-empty string, got {value!r}"
            )
        if not value.startswith(("/", "~")):
            raise ValueError(
                "composer_home_path must be absolute or start with '~', got "
                f"{value!r}"
            )
        return value

    @field_validator("global_packages", mode="before")
    @classmethod
    def _normalize_packages(cls, value: Any) -> tuple[tuple[str, str], ...]:
        """Flatten ``composer_global_packages`` to frozen ``(name, release)`` pairs.

        Ansible's loop is over a list of dicts (``{name: ..., release: ...}``);
        reemote keeps the pairs as tuples. Both the JSON shape (list of dicts) and
        an already-flattened pair sequence are accepted; ``release`` defaults to
        ``@stable`` exactly as the role's ``item.release | default('@stable')``.
        """
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            # ValueError (not TypeError) is the pydantic validator idiom: pydantic
            # wraps it into ValidationError, as it does every sibling raise here.
            raise ValueError(  # noqa: TRY004
                "composer_global_packages must be a list of {name, release} objects "
                f"or (name, release) pairs, got {value!r}"
            )
        pairs: list[tuple[str, str]] = []
        for item in value:
            if isinstance(item, dict):
                name = item.get("name")
                release = item.get("release", "@stable")
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                name, release = item
            else:
                raise ValueError(
                    "composer_global_packages items must be {name, release} objects "
                    f"or (name, release) pairs, got {item!r}"
                )
            if not isinstance(name, str) or not name:
                raise ValueError(
                    "composer_global_packages name must be a non-empty string, got "
                    f"{name!r}"
                )
            if not isinstance(release, str) or not release:
                raise ValueError(
                    "composer_global_packages release must be a non-empty string, got "
                    f"{release!r}"
                )
            pairs.append((name, release))
        return tuple(pairs)

    @model_validator(mode="after")
    def _reject_scoped_out(self) -> "ComposerVars":
        """Fail fast on the two opt-in branches this migration scopes out.

        Raised while the variables are resolved, so before any host is contacted.
        The role's ``fail:`` xor gate is subsumed: its own message is reproduced
        when a version pin and ``keep_updated`` are both set.
        """
        if self.keep_updated:
            if self.version:
                raise ValueError(
                    "You cannot request a specific version AND keep the composer up "
                    "to date. Set either composer_version or composer_keep_updated, "
                    "but not both."
                )
            raise ValueError(
                "composer_keep_updated is scoped out of this migration (the role's "
                "self-update branch is opt-in and stateless); leave it false"
            )
        if self.add_project_to_path:
            raise ValueError(
                "composer_add_project_to_path is scoped out of this migration (the "
                "role's project-bin branch is opt-in); leave it false"
            )
        return self


# --- frozen per-host resolution ----------------------------------------------


@dataclass(frozen=True)
class ComposerSettings:
    """One host's resolved composer targets: the pure half of the old plan freeze.

    Built by :func:`resolve_settings` from the resolved variables and the raw
    ``php_executable`` in ``host.extra``, so every value the requests and the two
    action commands need is decided before the first barrier. Frozen and hashable,
    as a deploy body's local must be.
    """

    composer_path: str
    php: str
    installer_arg: str
    home_path: str
    home_owner: str
    home_group: str
    packages: tuple[tuple[str, str], ...]
    add_to_path: bool
    auth_content: str | None


def resolve_settings(hv: ComposerVars, host: Host) -> ComposerSettings:
    """Resolve one host's composer settings from its variables and raw extra.

    Pure apart from the ``host.extra`` read for the role's one unprefixed
    variable. The installer argument follows the role's
    ``{% if composer_version_branch %}...{% elif composer_version %}--version=...
    {% endif %}`` precedence: the branch (default ``--2``) wins, and a version pin
    applies only when the branch is explicitly empty. ``auth.json`` is rendered
    here (the role's template is a plain JSON object with 4-space indent); the
    profile entry needs the *resolved* home and is rendered after the ``Home``
    barrier.
    """
    php = host.extra.get("php_executable", PHP_EXECUTABLE_DEFAULT)
    if not isinstance(php, str) or not php:
        raise ValueError(f"php_executable must be a non-empty string, got {php!r}")
    token = hv.github_oauth_token
    return ComposerSettings(
        composer_path=hv.path,
        php=php,
        installer_arg=hv.version_branch
        or (f"--version={hv.version}" if hv.version else ""),
        home_path=hv.home_path,
        home_owner=hv.home_owner,
        home_group=hv.home_group,
        packages=hv.global_packages,
        add_to_path=hv.add_to_path,
        auth_content=render_auth_json(token) if token else None,
    )


# --- pure helpers ------------------------------------------------------------


def render_auth_json(token: str) -> str:
    """``templates/auth.json.j2`` -- a plain JSON object, 4-space indent.

    The template is a static ``{"github-oauth": {"github.com": <token>}}`` object,
    so ``json.dumps`` reproduces it byte-for-byte (trailing newline included); no
    Jinja2 is involved.
    """
    return json.dumps({"github-oauth": {"github.com": token}}, indent=4) + "\n"


def render_profile(home: str) -> str:
    """``templates/composer.sh.j2`` -- one ``export`` line, f-string rendered.

    Uses the *resolved absolute* home (the template interpolates the raw
    ``~/.composer``): equivalent for every login shell that sources
    ``/etc/profile.d``, and unambiguous for a non-root owner.
    """
    return f"export PATH=$PATH:{home}/vendor/bin\n"


def resolve_home(home_path: str, home_base: str | None) -> str:
    """Expand ``composer_home_path`` against the owner's gathered home.

    The role's ``~/.composer`` is tilde-expanded by Ansible in the
    ``become_user``'s context (root by default); reemote gathers
    ``server.Home(home_owner)`` and expands control-side. An absolute override is
    used as-is; an unknown owner (``Home`` returned ``None``) fails fast rather
    than inventing a path.
    """
    if home_path.startswith("/"):
        return home_path
    if home_base is None:
        raise ValueError(
            f"cannot resolve composer_home_path {home_path!r}: the home directory "
            "of composer_home_owner is unknown on the target"
        )
    if home_path == "~":
        return home_base
    return home_base + home_path[1:]


def installer_command(settings: ComposerSettings) -> str:
    """The role's installer ``command:`` with ``chdir=/tmp`` inlined.

    reemote's ``Shell`` action has no ``chdir`` parameter, so the working directory
    becomes a ``cd /tmp &&`` prefix (the recipe for ``command:`` kwargs the action
    surface does not carry).
    """
    command = f"cd /tmp && {settings.php} composer-installer.php"
    if settings.installer_arg:
        command += f" {settings.installer_arg}"
    return command


def require_command(composer_path: str, name: str, release: str) -> str:
    """The role's per-package ``composer global require`` command text."""
    return (
        f"{composer_path} global require {name}:{release} "
        "--no-progress --no-interaction"
    )


# --- the deploy stages (each returns a per-host generator) --------------------


def download_installer(settings: ComposerSettings, signature: str) -> Deploy:
    """The role's "Download Composer installer." task (one request).

    ``files.download`` fetches the installer control-side, verifies the role's
    ``sha384:`` checksum pin against the signature the ``control`` barrier just
    returned, and spools it to ``/tmp/composer-installer.php`` at 0755. The pin is
    what makes the fetch deterministic. No ``sudo``: the role's ``get_url`` task
    carries no ``become`` and ``/tmp`` is login-writable.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            files.download,
            name="Download Composer installer.",
            url=INSTALLER_URL,
            dest=INSTALLER_PATH,
            mode="0755",
            checksum=f"sha384:{signature}",
        )

    return deploy


def place_composer(
    settings: ComposerSettings, home: str, need_install: bool
) -> Deploy:
    """The phar move, the composer home and the OAuth token.

    Runs after the installer ``act`` barrier, so ``files.copy``'s source
    (``/tmp/composer.phar``) exists. The role's ``mv`` becomes copy plus a paired
    removal: copy renames into place at 0755 sha1-idempotently but keeps the
    source, so the cleanup request reproduces ``mv``'s source consumption.
    ``overwrite=True`` is safe -- the exists gate means the destination was absent
    when the body read it. The home directory and ``auth.json`` run for every
    target (the role's tasks are not gated on the stat register).
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if need_install:
            yield op(
                files.copy,
                name="Move Composer into globally-accessible location.",
                src=PHAR_PATH,
                dest=settings.composer_path,
                mode="0755",
                overwrite=True,
                sudo=True,
            )
            # mv consumes its source; files.copy does not. The paired removal
            # keeps /tmp clean and the second pass a no-op.
            yield op(
                files.file,
                name="Remove the staged composer.phar from /tmp.",
                path=PHAR_PATH,
                present=False,
                sudo=True,
            )
        yield op(
            files.directory,
            name="Ensure composer directory exists.",
            path=home,
            mode="0755",
            user=settings.home_owner,
            group=settings.home_group,
            sudo=True,
        )
        if settings.auth_content is not None:
            yield op(
                files.install,
                name="Add GitHub OAuth token for Composer.",
                content=settings.auth_content,
                dest=f"{home}/auth.json",
                mode="0644",
                user=settings.home_owner,
                group=settings.home_group,
                sudo=True,
            )

    return deploy


def profile_entry(settings: ComposerSettings, home: str) -> Deploy:
    """The role's "Add composer_home_path bin directory to $PATH." task.

    Faithful to the role's ordering (the template task lives in
    ``global-require.yml``, after the require loop), so it follows the require
    actions. The content is the pre-rendered ``composer.sh.j2``; ``files.install``
    is content-idempotent, so a converged entry is a no-op. Rendered only when the
    role would reach it: global packages configured (the include gate) and
    ``composer_add_to_path`` true.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if not settings.packages or not settings.add_to_path:
            return
        yield op(
            files.install,
            name="Add composer home bin directory to global $PATH.",
            content=render_profile(home),
            dest=PROFILE_PATH,
            mode="0644",
            sudo=True,
        )

    return deploy


# --- the factory --------------------------------------------------------------


def composer(v: VarsSet[ComposerVars]) -> Deploy:
    """Install Composer and configure its home on Ubuntu hosts.

    Args:
        v: The role's resolved variables, one :class:`ComposerVars` per host
            (``resolve_vars(ComposerVars, "composer_", inventory)``). The role's
            one unprefixed variable, ``php_executable``, is read raw from each
            ``host.extra``.

    Returns:
        A deploy for :func:`~reemote.run_waves`. Publishes nothing, so the
        generator returns ``None``; a composing role sequences it with
        ``yield from scoped("composer", composer(v))(host)``.

    Raises:
        ValueError: A role variable is invalid (raised by pydantic while the
            variables are resolved, before any host is contacted), ``php_executable``
            in ``host.extra`` is empty, or ``composer_home_owner``'s home cannot be
            resolved. The first two are raised before any request runs.
        CommandError: The installer or a ``composer global require`` action exited
            non-zero. Raised in the body from the barrier's own ``Result`` -- an
            ``act`` barrier never raises on a failing exit status, so the check
            belongs to the role.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        settings = resolve_settings(hv, host)

        # The role's ``stat`` register: the exists gate for the whole installer
        # half. Read first, so a converged host skips the signature fetch too.
        need_install = not bool((yield fact(FileExists, path=settings.composer_path)))

        # The owner's home, for the ``~/.composer`` tilde expansion.
        home_base = yield fact(server.Home, user=settings.home_owner)
        home = resolve_home(settings.home_path, home_base)

        # The ``creates=`` gate, one ``fact`` per configured package: a package
        # whose vendor directory already exists is not re-required. Hosts with
        # different package counts park asymmetrically, which is legal.
        missing: list[tuple[str, str]] = []
        for name, release in settings.packages:
            vendor = bool(
                (yield fact(FileExists, path=f"{home}/vendor/{name}"))
            )
            if not vendor:
                missing.append((name, release))

        if need_install:
            # Control-side IO gated on a per-host fact (section 4.4): the URL is a
            # constant but the *need* is per-host, so it cannot move into an
            # ``async def`` factory without fetching for hosts that already have
            # composer. The barrier returns the signature text.
            signature = (yield control(files.fetch_url, SIGNATURE_URL)).strip()
            # The download request is applied in the wave the installer action
            # closes, so the installer file exists before the action runs.
            yield from download_installer(settings, signature)(host)
            command = installer_command(settings)
            result = yield act(Shell, command=command, name="Run Composer installer.")
            # A barrier never raises on a non-zero exit -- the Result is data -- so
            # a broken installer is caught here and never feeds the copy.
            if result["exit_status"] != 0:
                raise CommandError(
                    host.name, command, result["exit_status"], result["stderr"]
                )

        # The phar move, the home directory and auth.json. The copy's source is the
        # phar the installer action just produced.
        yield from place_composer(settings, home, need_install)(host)

        # The global-require loop: one ``act`` per missing package, with the role's
        # COMPOSER_HOME environment and elevation. Each action closes the wave
        # holding the requests before it, so the composer binary is in place.
        for name, release in missing:
            command = require_command(settings.composer_path, name, release)
            result = yield act(
                Shell,
                command=command,
                name=f"Globally require {name}.",
                env={"COMPOSER_HOME": home},
                sudo=True,
            )
            if result["exit_status"] != 0:
                raise CommandError(
                    host.name, command, result["exit_status"], result["stderr"]
                )

        # The PATH entry, after the require loop exactly as the role orders it.
        yield from profile_entry(settings, home)(host)

    return deploy


__all__ = [
    "ComposerVars",
    "composer",
]
