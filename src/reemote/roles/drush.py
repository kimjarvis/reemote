# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The Drush role (migrated from ``geerlingguy.drush``).
#
# Installs the `Drush launcher <https://www.drush.org/>`_, the Drupal
# command-line shell's version-delegating phar, on Ubuntu hosts: the old-symlink
# cleanup, the ``/usr/local/bin`` directory, the launcher phar download and the
# 0755 executable bit. :func:`drush` is the whole task list and
# :class:`DrushVars` is ``defaults/main.yml``, bound from the inventory
# entry's ``drush`` role mapping by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. A host outside the target family,
# or one with ``launcher_install`` false, returns before its first ``yield`` and
# emits no requests, no barriers and no log lines at all.
#
# This is the smallest role in the corpus and the shape check for the design:
# **two ``fact`` barriers then ``yield from`` -- and nothing else.** No
# checkpoint, no handler, no ``act``, no ``control``. The role ships no
# ``handlers/main.yml``, nothing reads an outcome, and the phar's fetch is
# *inside* the ``files.download`` operation (the control-side ``_fetch_url_bytes``
# seam), not a barrier of the body. The wave count is fixed at three -- wave 0
# ``fact(FileLink)``, wave 1 ``fact(File)``, wave 2 the launcher requests -- on
# every host and every pass; if a fourth ever appears, a barrier has been added
# speculatively (design/roles.md §18.9).
#
# The role's ``stat`` + ``register: drush_path_state`` + ``when: …islnk``
# construct, which the two-phase script had to freeze in ``main()`` as a
# gather → snapshot → factory, is simply two in-body ``fact`` barriers here --
# §17.2's table applied. Like every ``files.download`` caller, the role fetches
# the phar on *every* pass (the sha1 comparison must read the body), so the
# convergence claim is about changed entries, not fetch counts; the second pass
# answers both barriers ``True``-consistent and reports no change.
#
# The gap this role's migration originally found -- no binary
# spool-and-install to a root-owned path -- was filed as issue #23 and is closed
# on main: ``files.download`` fetches bytes control-side and a non-UTF-8 body
# installs through ``files.install``'s binary path, sha1-idempotent. No new
# library gaps surfaced in this migration.
#
# Deviations from the Ansible role, all deliberate:
#
# * the phar is fetched **control-side** (``files.download``) and spooled to the
#   host, where ``get_url`` downloads on the target. The control machine needs
#   egress to ``github.com``; the host downloads nothing.
# * ``get_url`` with the default ``force: no`` skips an *existing* destination
#   regardless of content; ``files.download`` is sha1-idempotent -- equal bytes
#   no-op, a drifted binary converges to the release content once.
# * the 0755 mode is applied **at install time** (``files.download(mode=…)``)
#   rather than by a separate chmod after a default-mode download; the role's
#   final "Ensure Drush is executable." task is kept as a ``files.file(mode=…)``
#   metadata-convergence request for an already-present destination, because
#   install/download idempotence is content-only and never compares mode.
# * no checksum pin: the role sets none, and GitHub release assets are
#   immutable. The request sequence is deterministic without a pin.
# * the symlink cleanup uses ``force=True`` -- the ``File`` fact reads a symlink
#   as a non-file, so ``files.file(present=False)`` would raise without it.
# * the old script's one optimistic edge is *gone* on the wave path: a symlink
#   whose target already held the exact phar bytes planned a no-op download in
#   prepare (the sha1 read follows the link) but installed in apply (the cleanup
#   removed the link first). ``run_waves`` materializes once, after the cleanup
#   applied, so the download's read-back sees the real post-cleanup state.
# * every elevated request carries ``sudo=True`` explicitly (the role relies on
#   play-level ``become``); run-as users/passwords come from the inventory
#   ``privilege`` section, never from an operation.
# * the family and ``launcher_install`` gates are early returns in the body, not
#   ``when=`` guards on each request: a skipped host emits no requests and is
#   never contacted, and the dry pass stops before its barriers.
# * ``launcher_version`` is typed ``str`` with an explicit non-string rejection:
#   the role's variable is a dotted version string (``0.10.1``), and an int or
#   float reaching the URL template would silently build a nonsense release URL.
# * the two opt-in branches are **rejected** at variable-resolution time, before
#   any host is contacted: ``drush_composer_global_install`` (a global
#   ``composer require`` + symlink) and ``drush_install_from_source`` (``git
#   clone`` + composer install). Both are default-false.
# """

import posixpath
from collections.abc import Generator
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ..core import op
from ..hosts import Host
from ..modules import files
from ..modules.files import File, FileLink
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, fact

# --- role constants (defaults/main.yml, launcher path only) -------------------

#: The role's default launcher release, which fixes the phar URL.
DEFAULT_LAUNCHER_VERSION = "0.10.1"

#: The release-asset URL template -- the role's ``drush_launcher_phar_url``
#: default, a Jinja2 interpolation of ``drush_launcher_version``.
PHAR_URL_TEMPLATE = (
    "https://github.com/drush-ops/drush-launcher/releases/download/"
    "{version}/drush.phar"
)


# --- variables ---------------------------------------------------------------


class DrushVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``drush`` role mapping, so ``launcher_path`` there sets
    :attr:`launcher_path`. The model is frozen, so it stays hashable through the
    deploy body, and the two scoped-out opt-in branches are rejected here, at
    resolution time, before any host is contacted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Whether the launcher path runs at all. Pydantic's lax ``bool`` coercion
    #: accepts the YAML 1.1 scalars (``yes``/``no``/``on``/``off``/``1``/``0``)
    #: exactly as Ansible's ``| bool`` does.
    launcher_install: bool = True
    #: The launcher release; fixes the phar URL unless :attr:`launcher_phar_url`
    #: pins one.
    launcher_version: str = DEFAULT_LAUNCHER_VERSION
    #: An explicit phar URL; it wins over the version (Ansible's variable
    #: precedence). ``None`` rebuilds the role's release URL from
    #: :attr:`launcher_version`.
    launcher_phar_url: str | None = None
    #: Where the launcher lands; must be absolute. Its dirname is the directory
    #: task's path.
    launcher_path: str = "/usr/local/bin/drush"
    #: Scoped out: ``true`` fails fast (the composer-global branch).
    composer_global_install: bool = False
    #: Scoped out: ``true`` fails fast (the build-from-source branch).
    install_from_source: bool = False

    @field_validator("launcher_version")
    @classmethod
    def _version_str(cls, value: str) -> str:
        """Reject an empty ``drush_launcher_version``; pydantic's own ``str``
        check already refuses a number, naming the field."""
        if not value:
            raise ValueError(
                f"drush_launcher_version must be a non-empty string, got {value!r}"
            )
        return value

    @field_validator("launcher_phar_url")
    @classmethod
    def _url_form(cls, value: str | None) -> str | None:
        """An explicit ``drush_launcher_phar_url`` must be a non-empty string."""
        if value is not None and not value:
            raise ValueError(
                "drush_launcher_phar_url must be a non-empty string or null, got "
                f"{value!r}"
            )
        return value

    @field_validator("launcher_path")
    @classmethod
    def _path_absolute(cls, value: str) -> str:
        """Reject a relative or empty ``drush_launcher_path``: it would land elsewhere."""
        if not value:
            raise ValueError(
                f"drush_launcher_path must be a non-empty string, got {value!r}"
            )
        if not value.startswith("/"):
            raise ValueError(
                f"drush_launcher_path must be absolute, got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _reject_scoped_out(self) -> "DrushVars":
        """Fail fast on the two opt-in branches this migration scopes out.

        Raised while the variables are resolved, so before any host is
        contacted -- the composer ``keep_updated`` precedent.
        """
        if self.composer_global_install:
            raise ValueError(
                "drush_composer_global_install is scoped out of this migration "
                "(the role's composer-global branch is opt-in); leave it false"
            )
        if self.install_from_source:
            raise ValueError(
                "drush_install_from_source is scoped out of this migration (the "
                "role's build-from-source branch is opt-in); leave it false"
            )
        return self


# --- pure helpers ------------------------------------------------------------


def phar_url(hv: DrushVars) -> str:
    """The role's ``drush_launcher_phar_url`` default, rebuilt from the version.

    An explicit URL wins over the version (Ansible's variable precedence);
    otherwise the release URL interpolates the (possibly overridden) version,
    exactly as ``defaults/main.yml``'s Jinja2 does.
    """
    return hv.launcher_phar_url or PHAR_URL_TEMPLATE.format(
        version=hv.launcher_version
    )


# --- the deploy stage (returns a per-host generator) --------------------------


def launcher(hv: DrushVars, is_symlink: bool, dest_exists: bool) -> Deploy:
    """The role's five launcher tasks as requests, in role order.

    Pure of barriers -- every branch reads data the caller's two ``fact``
    barriers already resolved -- so it is one request wave. The barrier
    outcomes arrive as closed-over booleans.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        path = hv.launcher_path
        if is_symlink:
            # The File fact reads a symlink as a non-file, so force=True takes
            # the remove-the-link branch (Ansible's state: absent on a link).
            yield op(
                files.file,
                name="Perform cleanup of old symlink.",
                path=path,
                present=False,
                force=True,
                sudo=True,
            )
        yield op(
            files.directory,
            name="Ensure Drush path directory exists.",
            path=posixpath.dirname(path),
            mode="0755",
            sudo=True,
        )
        # 0755 at install time: the binary sha1 read-back is unprivileged, so
        # the launcher must stay login-readable for the no-op gate to work.
        yield op(
            files.download,
            name="Install Drush.",
            url=phar_url(hv),
            dest=path,
            mode="0755",
            sudo=True,
        )
        if dest_exists:
            # Metadata convergence for a pre-existing regular file. (A symlink
            # reads as a non-file, so dest_exists is False on that branch.)
            yield op(
                files.file,
                name="Ensure Drush is executable.",
                path=path,
                mode="0755",
                sudo=True,
            )

    return deploy


# --- the factory --------------------------------------------------------------


def drush(v: VarsSet[DrushVars]) -> Deploy:
    """Install the Drush launcher on Ubuntu hosts.

    Args:
        v: The role's resolved variables, one :class:`DrushVars` per host
            (``resolve_vars(DrushVars, "drush", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. Publishes nothing, so the
        generator returns ``None``; a composing role sequences it with
        ``yield from scoped("drush", drush(v))(host)``.

    Raises:
        ValueError: A role variable is invalid (raised by pydantic while the
            variables are resolved, before any host is contacted).
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        # The family gate and the role's include-level ``when: drush_launcher_install``
        # are body-opening returns, not per-request ``when=`` guards: such a host
        # emits no requests, no barriers and is never contacted.
        if host.family != "Ubuntu" or not hv.launcher_install:
            return

        # The role's ``stat`` register as two fact barriers: FileLink answers
        # the ``islnk`` gate (the symlink target, or None), File answers
        # whether a regular file already sits at the path (its metadata is what
        # the executable-bit task converges). Nothing else is a barrier --
        # §18.9: a checkpoint here would mean something has been over-applied.
        link = yield fact(FileLink, path=hv.launcher_path)
        info = yield fact(File, path=hv.launcher_path)

        # The old two-phase script froze these into a per-host plans dict in
        # main(); the per-host body IS the plan now (§17.2).
        yield from launcher(
            hv, link is not None, isinstance(info, dict)
        )(host)

    return deploy


__all__ = [
    "DrushVars",
    "drush",
]
