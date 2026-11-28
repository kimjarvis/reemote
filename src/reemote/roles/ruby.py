# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The ruby role (migrated from ``geerlingguy.ruby``).
#
# Installs Ruby on Ubuntu hosts and then ensures a list of gems: the
# ``ruby_packages`` apt install with its gated cache refresh, the rubygems
# package, the ``/etc/profile.d`` script that puts the user gem ``bin``
# directory on ``$PATH``, Bundler, and the ``ruby_install_gems`` loop.
# :func:`ruby` is the whole task list and :class:`RubyVars` is
# ``defaults/main.yml``, bound from the inventory entry's ``ruby`` role mapping
# by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. A host outside the family returns
# before its first ``yield`` and emits no requests, no barriers and no log lines
# at all. The role's RedHat branch (``setup-RedHat.yml``) and its opt-in
# build-from-source branch (``install-from-source.yml``, default-false
# ``ruby_install_from_source``) are out of scope, so the OS dispatch collapses to
# the body's opening family guard and the frozen Ubuntu constants below, and a
# mapping that sets ``install_from_source`` is rejected before any host is
# contacted.
#
# The stream is **one fact barrier and up to two request waves**. Wave 0 is the
# provisioning: the two apt installs and the profile script. The barrier then
# reads :class:`~reemote.modules.files.FileExists` for the ``gem`` binary, and
# wave 1 -- only emitted when the answer is yes -- is the Bundler task and the
# gem loop. The play relied on task order alone (``gem`` could not run before
# ``setup-Debian.yml`` installed it); a request-only stream cannot keep that
# order on a host that starts without ``gem``: a ``dry`` pass materializes every
# request of the wave -- and so runs the ``GemPackages`` fact read (``gem list
# --local``) each ``gem.packages`` performs -- before any of them is applied,
# and that probe exits 127. The barrier defers the gem loop to a wave that is
# only planned once ``gem`` provably exists. When the answer is no even after
# wave 0 (the binary is missing, or ``rubygems_package`` names a package that
# does not ship it), the role's work is done and the loop simply defers to the
# next invocation. A dry pass previews wave 0 and parks at the barrier, reading
# nothing. There is no handler and no ``register``/``when`` outcome read, so no
# checkpoint either (§4.3).
#
# No template engine is needed: the role ships no template and reads no YAML, so
# this module never imports :mod:`reemote.roles._render` and needs no
# ``reemote[templates]`` extra (the ``php``/``pip`` precedent). The one file the
# role copies ships as a frozen content constant instead.
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: the ``apt: update_cache`` task (``cache_valid_time: 86400``) folds into
#   the first ``apt.packages`` as ``update=True,
#   cache_valid_time=APT_CACHE_VALID_TIME`` (the ``git`` precedent). The refresh
#   is gated on the ``AptUpdateStamp`` fact, so a fresh cache skips it exactly as
#   Ansible's ``cache_valid_time`` would.
# * D2: ``include_vars`` + the ``Define ruby_packages`` ``set_fact`` collapse to
#   the frozen Ubuntu constants; ``vars/Debian.yml``'s ``__ruby_packages`` is
#   :data:`RUBY_PACKAGES` and remains overridable through
#   :attr:`RubyVars.packages`.
# * D3: the default :attr:`RubyVars.rubygems_package` is ``"ruby-rubygems"``,
#   not the role's ``ruby_rubygems_package_name: rubygems`` -- on a current
#   Ubuntu (verified on 24.04) the ``rubygems`` source package has no
#   installation candidate, while ``ruby-rubygems`` is the deb that ships
#   ``/usr/bin/gem``. The field keeps the role's override channel: an operator
#   with a release that still carries ``rubygems`` simply sets it.
# * D4: the Ubuntu-trusty ``set_fact`` (``rubygems-integration``) is a dead
#   branch -- 14.04 is long EOL and the default path on any supported release is
#   what :attr:`rubygems_package` defaults to.
# * D5: ``copy: src=rubygems.sh`` becomes ``files.install`` with the file's
#   content frozen as :data:`RUBYGEMS_PROFILE_SH` -- reemote roles ship no
#   ``files/`` tree. Idempotence is content-only (a mode-only drift is
#   ``files.file``'s job, which the role never needed either).
# * D6: the ``gem`` loop runs as **one ``gem.packages`` request per item**,
#   because the per-item knobs (``version`` pin, ``user_install``, ``bindir``)
#   are call-level scalars; a pin uses reemote's shared ``<pkg>=<version>``
#   spelling, which ``gem.packages`` translates to gem's own ``<pkg>:<version>``
#   token. Each item's ``user_install`` defaults to ``true`` exactly as the
#   Ansible ``gem`` module does (the role passes ``default(omit)``), while the
#   Bundler task names ``user_install: false``.
# * D7: ``become: true`` + ``become_user: ruby_install_gems_user`` (default
#   ``ansible_user``) becomes *scope plus privilege* rather than a run-as
#   parameter: a ``user_install`` item runs with no escalation, i.e. as the
#   inventory's login user -- the same account the role became -- and installs
#   into that user's own gem directory; a system-scope item (Bundler, and every
#   item with ``user_install: false``) carries ``sudo=True`` because the system
#   store is root-owned. ``ruby_install_gems_user`` is therefore not a variable:
#   run-as users come from the inventory, never from an operation.
# * D8: ``ruby_install_from_source`` (and its ``ruby_version`` /
#   ``ruby_download_url`` / ``ruby_source_configure_command`` / ``workspace``
#   sub-knobs) is scoped out -- a Phase 0 decision. The boolean is kept as a
#   fail-fast field: setting it ``true`` raises before any host is contacted;
#   the sub-knobs are not fields at all, so a mapping that sets one gets a
#   ``RoleVarsError`` naming the valid keys.
# * D9: the gem loop sits behind a ``fact(FileExists, GEM_BIN)`` barrier the
#   play did not have -- Ansible sequenced the tasks and trusted ``gem`` to
#   exist by the time the loop ran; reemote plans a whole wave before applying
#   it, so the loop's fact reads would run against a host where ``gem`` is not
#   yet installed. The barrier's ``no`` answer defers the loop to the next
#   invocation, where the previous invocation's apt install has landed it (the
#   live double-run converges); nothing is silently dropped.
# * every request that touches root-owned state carries ``sudo=True`` -- the
#   role relied on play-level ``become``; reemote has no such thing.
# * the host-family guard is the body's opening ``if``, not a ``when=`` on every
#   request: an off-target host emits no requests and no log lines at all.
# * variable validation becomes pydantic validation: a bad :class:`RubyVars` or
#   :class:`GemSpec` entry raises ``ValidationError`` -- a ``ValueError``, so
#   the runner maps it -- before any host is contacted.
# """

from collections.abc import Generator
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..hosts import Host
from ..modules import apt, files
from ..modules import gem as gem_mod
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, fact

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path) ---------

#: ``vars/Debian.yml``'s ``__ruby_packages``, the ``Define ruby_packages``
#: default (D2). ``build-essential``/``autogen``/``autoconf``/``libtool`` are
#: what the package path installs alongside the interpreter.
RUBY_PACKAGES: tuple[str, ...] = (
    "ruby-full",
    "ruby-dev",
    "build-essential",
    "autogen",
    "autoconf",
    "libtool",
)

#: ``setup-Debian.yml``'s ``cache_valid_time: 86400``, folded into the first
#: apt request (D1).
APT_CACHE_VALID_TIME = 86400

#: ``defaults/main.yml``'s ``ruby_rubygems_package_name``, re-defaulted (D3):
#: on a current Ubuntu the ``rubygems`` name has no installation candidate and
#: ``ruby-rubygems`` is the deb that ships ``/usr/bin/gem``.
DEFAULT_RUBYGEMS_PACKAGE = "ruby-rubygems"

#: Where the role's ``copy`` task lands ``files/rubygems.sh``, and what it
#: contains (D5) -- the script that prepends ``Gem.user_dir``/bin to ``$PATH``.
RUBYGEMS_PROFILE_PATH = "/etc/profile.d/rubygems.sh"

#: The ``gem`` binary ``ruby-rubygems`` ships -- the barrier's probe path (D9).
GEM_BIN = "/usr/bin/gem"

#: The frozen content of the role's ``files/rubygems.sh``, installed to
#: :data:`RUBYGEMS_PROFILE_PATH` (D5).
RUBYGEMS_PROFILE_SH = (
    "if which ruby >/dev/null && which gem >/dev/null; then\n"
    '    PATH="$(ruby -e \'puts Gem.user_dir\')/bin:$PATH"\n'
    "fi\n"
)


# --- variables ---------------------------------------------------------------


class GemSpec(BaseModel):
    """One ``ruby_install_gems`` entry: a name, an optional pin and a scope.

    The role's loop reads each item as either a bare string (``- rails``) or a
    mapping (``{name: sass, version: 3.7, user_install: false, bindir:
    /usr/local/bin}``); the ``install_gems`` before-validator coerces a bare
    string to ``{"name": ...}`` so pydantic builds this model either way.
    Frozen, so a tuple of them stays hashable through the deploy body (design
    doc section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``item.name | default(item)``.
    name: str
    #: ``item.version | default(omit)``: an optional pin, rendered in reemote's
    #: ``<pkg>=<version>`` spelling and translated to gem's ``<pkg>:<version>``
    #: install token by ``gem.packages`` (D6).
    version: str | None = None
    #: ``item.user_install | default(omit)`` -- the Ansible ``gem`` module's own
    #: default is ``true``, and that is what this field defaults to (D6/D7):
    #: the gem lands in the login user's own directory, no escalation.
    user_install: bool = True
    #: ``item.bindir | default(omit)``: where gem places the gems' executables.
    bindir: str | None = None

    @field_validator("name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """An empty gem name is a typo, not "unset"."""
        if not value:
            raise ValueError("gem name must be a non-empty string")
        return value

    @field_validator("version", "bindir")
    @classmethod
    def _none_or_non_empty(cls, value: str | None) -> str | None:
        """An empty override is a mistake, not "omit"."""
        if value is not None and not value:
            raise ValueError("must be a non-empty string or null")
        return value


class RubyVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``ruby`` role mapping, so ``install_bundler`` sets
    :attr:`install_bundler` and ``install_gems`` :attr:`install_gems`.
    :attr:`rubygems_package` is ``ruby_rubygems_package_name`` re-defaulted
    (D3). The model is frozen, so it stays hashable through the deploy body
    (:class:`~reemote.rolevars.VarsSet` is closed over by the factory), and
    :attr:`packages` / :attr:`install_gems` are ``tuple``s, never ``list``s of
    dicts (design doc section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``ruby_packages``: the apt packages of the package-install path
    #: (``vars/Debian.yml``'s ``__ruby_packages`` on Ubuntu, D2).
    packages: tuple[str, ...] = RUBY_PACKAGES
    #: ``ruby_rubygems_package_name``: the apt package providing ``gem`` (D3).
    rubygems_package: str = DEFAULT_RUBYGEMS_PACKAGE
    #: ``ruby_install_bundler``: whether to install the Bundler gem.
    install_bundler: bool = True
    #: ``ruby_install_gems``: the gems to ensure, each a bare name or a
    #: ``{name, version, user_install, bindir}`` mapping (the role's loop).
    install_gems: tuple[GemSpec, ...] = ()
    #: ``ruby_install_from_source``: the scoped-out build-from-source branch
    #: (D8). ``False`` (or unset) is the package path; ``True`` fails fast.
    install_from_source: bool = False

    @field_validator("packages", mode="before")
    @classmethod
    def _packages_tuple(cls, value: Any) -> Any:
        """A list becomes a tuple; a non-sequence falls through for pydantic."""
        return tuple(value) if isinstance(value, (list, tuple)) else value

    @field_validator("packages")
    @classmethod
    def _packages_non_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Every apt package name must be a non-empty string."""
        if any(not item for item in value):
            raise ValueError("ruby packages must be non-empty strings")
        return value

    @field_validator("rubygems_package")
    @classmethod
    def _rubygems_non_empty(cls, value: str) -> str:
        """An empty package name would render an empty apt install."""
        if not value:
            raise ValueError("ruby_rubygems_package_name must be a non-empty string")
        return value

    @field_validator("install_from_source")
    @classmethod
    def _source_branch_scoped_out(cls, value: bool) -> bool:
        """D8: the opt-in source build is scoped out, but stated, not silent."""
        if value:
            raise ValueError(
                "ruby_install_from_source is scoped out of this migration: "
                "the role only supports the Ubuntu package-install path"
            )
        return value

    @field_validator("install_gems", mode="before")
    @classmethod
    def _coerce_gems(cls, value: Any) -> Any:
        """A bare-string item becomes ``{"name": item}``; a list becomes a tuple.

        The role's ``with_items`` accepts ``- rails`` and
        ``- {name: sass, version: 3.7}`` interchangeably; normalize the
        shorthand here so pydantic builds a :class:`GemSpec` either way. A
        non-sequence falls through for pydantic to reject.
        """
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(
                {"name": item} if isinstance(item, str) else item for item in value
            )
        return value


# --- pure helpers ------------------------------------------------------------


def gem_spec(gem: GemSpec) -> str:
    """One entry as gem's ``<pkg>`` or reemote's ``<pkg>=<version>`` pin (D6)."""
    if gem.version:
        return f"{gem.name}={gem.version}"
    return gem.name


# --- the deploy --------------------------------------------------------------


def install(hv: RubyVars) -> Deploy:
    """The whole Ubuntu role: wave 0 provisions, wave 1 (if ``gem``) runs gems.

    Every value the role read from its variables is frozen into the resolved
    :class:`RubyVars`, so wave 0 is a pure function of the variables; the gem
    loop then waits for the :data:`GEM_BIN` barrier, because each
    ``gem.packages`` request reads the ``GemPackages`` fact while it is being
    planned and ``gem list --local`` cannot run before the apt install has
    landed the binary (D9). The role has no handlers, so there is no
    notification split and no checkpoint.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        # setup-Debian.yml: the cache refresh (D1) and
        # ``apt: name={{ ruby_packages }} state=present`` (D2).
        yield op(
            apt.packages,
            name="Install ruby and other required dependencies.",
            packages=hv.packages,
            update=True,
            cache_valid_time=APT_CACHE_VALID_TIME,
            sudo=True,
        )

        # setup-Debian.yml: ``apt: name={{ ruby_rubygems_package_name }}`` (D3).
        yield op(
            apt.packages,
            name="Install rubygems.",
            packages=(hv.rubygems_package,),
            sudo=True,
        )

        # main.yml: ``copy: src=rubygems.sh dest=/etc/profile.d mode=0644`` --
        # the file's content ships as a constant (D5).
        yield op(
            files.install,
            name="Add user installed RubyGems bin directory to global $PATH.",
            content=RUBYGEMS_PROFILE_SH,
            dest=RUBYGEMS_PROFILE_PATH,
            mode="0644",
            sudo=True,
        )

        # D9: the barrier closing wave 0 -- does ``gem`` exist now that the apt
        # installs have landed? The read is deferred by design: the gem loop's
        # requests read the GemPackages fact while being planned, so they must
        # not be planned against a host that has no ``gem`` yet.
        gem_bin = yield fact(files.FileExists, GEM_BIN)
        if not gem_bin:
            # The package path promises ``gem``; if even wave 0 did not land it,
            # the loop cannot run and defers to the next invocation (D9).
            return

        # main.yml: ``gem: name=bundler user_install=false``. System scope is
        # root-owned, so the request elevates (D7).
        if hv.install_bundler:
            yield op(
                gem_mod.packages,
                name="Install Bundler.",
                packages=("bundler",),
                user_install=False,
                sudo=True,
            )

        # main.yml: the ruby_install_gems loop, one request per item (D6). Each
        # is fact-gated by the GemPackages fact, so a converged host's item
        # no-ops. user_install items run unprivileged, as the login user (D7).
        for item in hv.install_gems:
            yield op(
                gem_mod.packages,
                name=f"Ensure gem {item.name} is installed.",
                packages=(gem_spec(item),),
                user_install=item.user_install,
                bindir=item.bindir,
                sudo=not item.user_install,
            )

    return deploy


# --- the role ----------------------------------------------------------------


def ruby(v: VarsSet[RubyVars]) -> Deploy:
    """Install Ruby via apt and ensure a list of gems on Ubuntu hosts.

    Args:
        v: The role's resolved variables, one :class:`RubyVars` per host
            (``resolve_vars(RubyVars, "ruby", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs -- no
        other role consumes them -- so it returns ``None`` on every path,
        including the family guard's early return.

    Raises:
        ValueError: A scoped-out ``install_from_source`` or a bad
            :class:`RubyVars` / :class:`GemSpec` value is rejected by pydantic
            -- so :func:`~reemote.rolevars.resolve_vars` raises, a
            ``ValueError`` -- before any host is contacted. The deploy body
            itself never raises: the ``GEM_BIN`` barrier's answer only steers
            which requests follow, and a failed read surfaces as the barrier's
            own typed error, not the body's.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        # Wave 0: the apt installs and the profile script. The GEM_BIN barrier
        # closes it; the gem loop follows in wave 1, planned only once ``gem``
        # exists on the host (D9).
        yield from install(hv)(host)

    return deploy


__all__ = [
    "GemSpec",
    "RubyVars",
    "ruby",
]
