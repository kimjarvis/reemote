# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The Node.js role (migrated from ``geerlingguy.nodejs``).
#
# Installs Node.js and npm from the NodeSource apt repository on Ubuntu hosts,
# then wires up npm's global-prefix workflow: the repository dependencies, the
# NodeSource signing key and deb822 ``.sources`` file (with the folded apt-cache
# refresh), the version-pinned ``nodejs`` package, the root-owned global npm
# directory owned by the install user, the optional ``/etc/profile.d/npm.sh``
# environment script, the global npm packages and the optional ``package.json``
# install. :func:`nodejs` is the whole task list and :class:`NodejsVars` is
# ``defaults/main.yml``, bound from the inventory entry's ``nodejs`` role
# mapping by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. A host outside the family returns
# before its first ``yield`` and emits no requests, no barriers and no log lines
# at all -- so the Alpine and CentOS hosts in a shared inventory are never
# contacted. The role's RedHat branch (``setup-RedHat.yml``, the ``nodejs`` yum
# module and the NodeSource rpm) is out of scope, so the ``import_tasks`` OS
# dispatch collapses to the body's opening family guard and the frozen
# NodeSource constants below.
#
# The stream is **one request wave, and no barrier at all** -- the cheapest shape
# in the corpus (cheaper even than ``java``, which keeps a ``fact`` barrier for
# its version-keyed package table). Nothing here needs a read of the machine
# before a request can be built: ``nodejs_version`` is a plain variable (not a
# version-keyed ``include_vars`` table), the repository's ``Suites`` is the
# literal ``nodistro`` (not the release codename ``docker`` interpolates), the
# npm install user comes from the resolved variables or the inventory's own
# ``connection`` data, and the role has no handlers, no ``register``/``when``
# outcome reads and no command/shell tasks. So the body resolves a pure plan from
# ``hv`` and ``host.connection`` and yields the whole task list as requests:
#
# 0. :func:`install` -- the dependencies, the signing key + deb822 repository +
#    folded cache refresh, the ``nodejs`` package, the npm global directory, the
#    profile script, the global npm packages and the ``package.json`` install.
#
# Because there is no barrier, ``run_waves`` never closes an intermediate wave:
# the whole role lands in wave 0, and each request is applied before the next is
# pulled, so the ordering the role relies on holds -- dependencies before the
# repository, the repository (and its chained ``apt-get update``) before the
# ``nodejs`` install that resolves against it.
#
# **The dry run has no barrier to park on** (deviation D9). ``java``/``docker``/
# ``repo_epel`` all park on their first ``fact`` barrier, so a ``dry=True``
# preview stops there having planned nothing. This role yields requests from its
# first item, so ``dry=True`` instead *plans every request* -- reading facts and
# firing the control-side signing-key fetch -- and applies nothing. The preview
# is the whole wave, not a one-barrier stub.
#
# No template engine is needed: ``templates/npm.sh.j2`` is three pure
# ``{{ npm_config_prefix }}`` substitutions with no other Jinja2 syntax, which
# :func:`render_npm_profile` reproduces with a plain f-string, so this role never
# imports :mod:`reemote.roles._render` and needs no ``reemote[templates]`` extra
# (the ``php``/``java`` precedent).
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: the role's three repository tasks -- ``get_url`` (the signing key),
#   ``deb822_repository`` and the ``apt: update_cache=yes when node_repo is
#   changed`` refresh -- fold into ONE ``apt.sources_file(signed_by=<URL>,
#   update_cache=True)``. The URL ``signed_by`` form (issue #17) fetches the
#   ASCII-armored key control-side and installs the repo-named keyring
#   ``/etc/apt/keyrings/nodesource_<version>.asc`` itself, referencing it in
#   ``Signed-By``; ``update_cache=True`` chains one ``apt-get update`` on a real
#   sources change (write *or* removal), reproducing the role's fire-on-change
#   refresh with no separate task and no barrier.
# * D2: the keyring path differs from the role's. ``get_url`` wrote
#   ``/etc/apt/signing-key-nodesource-repo.asc``; ``apt.sources_file`` installs
#   the repo-named ``/etc/apt/keyrings/nodesource_<version>.asc`` (its fixed
#   convention). Functionally identical -- the sources file references wherever
#   the key lands. The role's ``get_url`` also carried a User-Agent workaround
#   (NodeSource discriminates the deb822 module's agent); reemote's fetch is a
#   plain control-side GET, so if NodeSource ever rejects it, fetch the key
#   yourself and pass ``signed_by`` a local keyring path.
# * D3: ``apt.sources_file``'s URL ``signed_by`` accepts ASCII-armored text ONLY
#   and raises ``ValueError`` on a binary ``.gpg`` body. The role's ``get_url``
#   downloads whatever NodeSource serves at ``nodesource-repo.gpg.key``. If that
#   endpoint serves a binary key, install it with ``apt.key`` from a control-side
#   fetch and pass ``signed_by`` the keyring path instead -- the documented
#   fallback in the operation's own error message.
# * D4: ``set_fact: nodejs_install_npm_user = ansible_user | default(lookup('env',
#   'USER'))`` becomes :func:`resolve_npm_user` -- the explicit
#   :attr:`NodejsVars.install_npm_user` when set, else the inventory
#   ``connection``'s ``username``/``user``. reemote has no ``ansible_user`` magic
#   variable, and the *controller's* ``$USER`` is not a meaningful per-host owner,
#   so the connection login name is the faithful analog. A host with neither
#   raises before any request is built.
# * D5: the role's ``npm_config_prefix`` and ``npm_config_unsafe_perm`` carry an
#   ``npm_`` name upstream, not the ``nodejs`` role's. ``resolve_vars`` binds
#   exactly the ``nodejs`` role mapping, so these two knobs are NOT typed
#   fields on :class:`NodejsVars` -- they are read raw from ``host.extra`` by
#   :func:`resolve_npm_config`, preserving the upstream inventory spelling
#   byte-for-byte. Renaming them ``nodejs_npm_config_*`` would break every
#   existing inventory that sets ``npm_config_prefix`` (the datadog role's six
#   unprefixed probe-config variables are the same precedent).
# * D6: ``NODE_PATH`` on the npm task's environment is dropped -- the ``npm``
#   module scopes it out deliberately, because it steers Node's *runtime* module
#   resolution, not what ``npm install`` records as installed, so it changes
#   neither the fact's answer nor any command's effect. The rendered
#   ``/etc/profile.d/npm.sh`` still exports ``NODE_PATH`` verbatim, so the login
#   environment the role sets up is preserved where it actually matters.
# * D7: the npm global-package loop's per-item ``state`` partitions into up to
#   three ``npm.packages`` requests (present / latest / absent), because
#   ``present`` and ``latest`` are request-level flags that apply to every
#   package in one call; the role ran one npm task per item. A version pin
#   (``item.version``) becomes reemote's shared ``<pkg>=<version>`` spelling,
#   which the operation translates to npm's ``<pkg>@<version>``.
# * D8: the npm requests carry ``sudo=True`` -- the root-owned global prefix
#   ``/usr/local/lib/npm`` needs escalation to write, where the role relied on
#   play-level ``become``. ``NPM_CONFIG_UNSAFE_PERM`` is rendered only when true
#   (the operation's behavior), which is identical to the role's explicit
#   ``false``. The profile template's ``mode=0644`` gets a paired
#   ``files.file(mode="0644")`` companion, because ``files.install`` idempotence
#   is content-only and would never re-converge a drifted mode (the java/apache
#   ``ports.conf`` precedent).
# * the host-family guard is the body's opening ``if``, not a ``when=`` on every
#   request: an off-target host emits no requests and no ``[skipped]`` lines.
# * variable validation becomes pydantic validation: a bad
#   :attr:`NodejsVars.npm_global_packages` entry or an empty name raises
#   ``ValidationError`` -- a ``ValueError``, so the runner maps it -- before any
#   host is contacted.
# """

from collections.abc import Generator, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..hosts import Host
from ..modules import apt, files, npm
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem

# --- role constants (defaults/main.yml + setup-Debian.yml, Ubuntu path) --------

#: ``setup-Debian.yml``'s ``apt: name=[...]`` prerequisites. ``gnupg2`` and
#: ``apt-transport-https`` are what Ansible's deb822_repository/get_url tasks
#: need on the host; reemote generates the deb822 text and fetches the key
#: control-side, but the dependencies are kept so the host matches the role's
#: post-state (the docker role's ``DEPENDENCY_PACKAGES`` precedent).
DEPENDENCY_PACKAGES = ("apt-transport-https", "python3-debian", "gnupg2")

#: ``setup-Debian.yml``'s ``get_url`` source: the NodeSource repository signing
#: key. ``apt.sources_file``'s URL ``signed_by`` fetches it control-side (D1/D3).
NODESOURCE_KEY_URL = "https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key"

#: ``setup-Debian.yml``'s ``deb822_repository`` base. The role interpolates
#: ``nodejs_version`` into both the repository name and its URI, so the two are
#: derived per host in :func:`nodejs_plan` from :attr:`NodejsVars.version`.
NODESOURCE_REPO_BASE = "https://deb.nodesource.com"

#: ``tasks/main.yml``'s ``template: dest=`` -- the login environment script that
#: puts npm's relocated global ``bin`` on ``$PATH``.
NPM_PROFILE = "/etc/profile.d/npm.sh"

#: ``defaults/main.yml``'s ``npm_config_prefix`` default. Read raw from
#: ``host.extra`` (not a typed field) because it carries an ``npm_`` name
#: upstream, not a ``nodejs`` role variable (D5).
DEFAULT_NPM_CONFIG_PREFIX = "/usr/local/lib/npm"

#: ``defaults/main.yml``'s ``npm_config_unsafe_perm`` default (D5).
DEFAULT_NPM_CONFIG_UNSAFE_PERM = False


# --- variables ---------------------------------------------------------------


class NpmGlobalPackage(BaseModel):
    """One ``nodejs_npm_global_packages`` entry: a name, an optional pin, a state.

    The role's loop reads each item as either a bare string (``- node-sass``) or
    a mapping (``{name: jslint, version: 0.9.3, state: present}``); the
    ``npm_global_packages`` before-validator coerces a bare string to
    ``{"name": ...}`` so pydantic builds this model either way. Frozen, so a
    tuple of them stays hashable through the deploy body (design doc section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The package name (``item.name | default(item)``).
    name: str
    #: ``item.version | default(omit)``: an optional pin, rendered as reemote's
    #: ``<pkg>=<version>`` spelling (D7).
    version: str | None = None
    #: ``item.state | default('present')``: the npm module's state, split into
    #: the operation's ``present``/``latest`` flags by :func:`partition_npm_packages`.
    state: Literal["present", "absent", "latest"] = "present"

    @field_validator("name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """An empty package name is a typo, not "unset"."""
        if not value:
            raise ValueError("npm global package name must be a non-empty string")
        return value


class NodejsVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``nodejs`` role mapping, so ``version`` sets
    :attr:`version` and ``npm_global_packages`` :attr:`npm_global_packages`.
    The two ``npm_config_*`` knobs are NOT fields here: they carry an
    ``npm_`` name upstream, so they are read raw from
    ``host.extra`` by :func:`resolve_npm_config`, preserving the inventory
    contract (D5, the datadog precedent).

    The model is frozen, so it stays hashable through the deploy body
    (:class:`~reemote.rolevars.VarsSet` is closed over by the factory), and
    :attr:`npm_global_packages` is a ``tuple`` of frozen
    :class:`NpmGlobalPackage` models, never a ``list`` of dicts (design doc
    section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``nodejs_version``: the NodeSource major release (``"16.x"``, ``"20.x"``).
    #: Interpolated into the repository name/URI and, with the ``x`` stripped and
    #: a ``*`` appended, into the ``nodejs=<major>.*`` apt pin.
    version: str = "16.x"
    #: ``nodejs_install_npm_user``: the owner of the global npm directory. The
    #: empty default resolves to the inventory connection's login name (D4).
    install_npm_user: str = ""
    #: ``nodejs_npm_global_packages``: the global packages to ensure, each a bare
    #: name or a ``{name, version, state}`` mapping (the role's ``with_items``).
    npm_global_packages: tuple[NpmGlobalPackage, ...] = ()
    #: ``nodejs_package_json_path``: a manifest whose dependencies to install
    #: globally; the empty default skips the task, as the role's
    #: ``when: ... != ""`` does.
    package_json_path: str = ""
    #: ``nodejs_generate_etc_profile``: whether to write ``/etc/profile.d/npm.sh``.
    generate_etc_profile: bool = True

    @field_validator("version")
    @classmethod
    def _version_non_empty(cls, value: str) -> str:
        """An empty version would render an empty repository URI and apt pin."""
        if not value:
            raise ValueError("nodejs_version must be a non-empty string")
        return value

    @field_validator("npm_global_packages", mode="before")
    @classmethod
    def _coerce_packages(cls, value: Any) -> Any:
        """A bare-string item becomes ``{"name": item}``; a list becomes a tuple.

        The role's ``with_items`` accepts ``- node-sass`` and
        ``- {name: jslint, version: 0.9.3}`` interchangeably; normalize the
        shorthand here so pydantic builds an :class:`NpmGlobalPackage` either
        way. A non-sequence falls through for pydantic to reject.
        """
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(
                {"name": item} if isinstance(item, str) else item for item in value
            )
        return value


# --- frozen per-host plan ------------------------------------------------------


@dataclass(frozen=True)
class NodejsPlan:
    """One host's resolved targets, built by :func:`nodejs_plan`.

    Everything the deploy reads is decided here from the resolved variables and
    the connection login name -- there is no barrier, so the plan is a pure
    function of ``hv`` and ``npm_user``. Frozen and hashable; under a per-host
    body it is a local, not a host-keyed dict.
    """

    #: The deb822 ``.sources`` base name and the managed keyring name (D1/D2).
    sources_filename: str
    #: The NodeSource repository URI for this host's version.
    repo_uri: str
    #: The ``nodejs=<major>.*`` apt pin (the role's ``regex_replace('x','') + '*'``).
    nodejs_pin: str
    #: The owner/group of the global npm directory (D4).
    npm_user: str
    #: The npm prefix and its unsafe-perm flag (D5).
    npm_config_prefix: str
    npm_config_unsafe_perm: bool
    #: The rendered ``/etc/profile.d/npm.sh`` content, or ``None`` when
    #: :attr:`NodejsVars.generate_etc_profile` is false.
    profile_content: str | None
    #: The global package specs partitioned by state (D7); empty tuples skip.
    npm_present: tuple[str, ...]
    npm_latest: tuple[str, ...]
    npm_absent: tuple[str, ...]
    #: The ``package.json`` path, or ``None`` when unset (the role's ``!= ""`` gate).
    package_json: str | None


# --- pure helpers ------------------------------------------------------------


def resolve_npm_user(install_npm_user: str, connection: Mapping[str, Any]) -> str:
    """``ansible_user | default(lookup('env', 'USER'))``, the reemote way (D4).

    The explicit :attr:`NodejsVars.install_npm_user` wins; otherwise the
    inventory ``connection``'s login name (``username``, or asyncssh's ``user``
    alias) is the owner, the faithful analog of Ansible's ``ansible_user``.
    reemote has no ``ansible_user`` and the controller's ``$USER`` is not a
    meaningful per-host owner, so a host with neither raises -- before any
    request is built, since the plan is resolved up front.
    """
    if install_npm_user:
        return install_npm_user
    user = connection.get("username") or connection.get("user")
    if not user:
        raise ValueError(
            "cannot resolve the npm install user: install_npm_user is "
            "unset and the inventory connection carries no username/user -- "
            "the role's `ansible_user | default(env USER)` has no reemote "
            "analog for a connection with no login name"
        )
    return str(user)


def resolve_npm_config(extra: Mapping[str, Any]) -> tuple[str, bool]:
    """The two ``npm_``-prefixed knobs, read raw from ``host.extra`` (D5).

    ``npm_config_prefix`` and ``npm_config_unsafe_perm`` carry an ``npm_`` name
    upstream, so ``resolve_vars`` -- which binds only the ``nodejs`` role
    mapping -- leaves them in the raw ``host.extra`` channel. Reading them here
    preserves the existing inventory spelling; renaming them into the typed
    model would break every
    inventory that sets ``npm_config_prefix`` (the datadog precedent). Defaults
    match ``defaults/main.yml``. A wrong-typed value raises ``TypeError`` and an
    empty prefix raises ``ValueError`` before any request is built.
    """
    prefix = extra.get("npm_config_prefix", DEFAULT_NPM_CONFIG_PREFIX)
    if not isinstance(prefix, str):
        raise TypeError("npm_config_prefix must be a string path")
    if not prefix:
        raise ValueError("npm_config_prefix must be a non-empty path")
    unsafe_perm = extra.get("npm_config_unsafe_perm", DEFAULT_NPM_CONFIG_UNSAFE_PERM)
    if not isinstance(unsafe_perm, bool):
        raise TypeError("npm_config_unsafe_perm must be a boolean")
    return prefix, unsafe_perm


def render_npm_profile(npm_config_prefix: str) -> str:
    """``templates/npm.sh.j2``, pre-rendered control-side.

    Three pure ``{{ npm_config_prefix }}`` substitutions with no other Jinja2
    syntax, so a plain f-string is the whole renderer -- no Jinja2 dependency
    (the ``php``/``java`` precedent, deviation D8's no-extra case). The trailing
    newline is kept, matching the template source byte-for-byte. ``NODE_PATH`` is
    exported here verbatim even though the npm *commands* drop it (D6): the login
    environment is where it actually steers module resolution.
    """
    return (
        f"export PATH=$PATH:{npm_config_prefix}/bin\n"
        f"export NPM_CONFIG_PREFIX={npm_config_prefix}\n"
        f"export NODE_PATH=$NODE_PATH:{npm_config_prefix}/lib/node_modules\n"
    )


def npm_spec(package: NpmGlobalPackage) -> str:
    """One package entry as reemote's ``<pkg>`` or ``<pkg>=<version>`` spec (D7)."""
    if package.version:
        return f"{package.name}={package.version}"
    return package.name


def partition_npm_packages(
    packages: tuple[NpmGlobalPackage, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Split the loop's per-item ``state`` into three request-level groups (D7).

    ``npm.packages`` takes ``present``/``latest`` as flags that apply to every
    package in one call, so items sharing a state batch into one request:
    ``present`` (present=True), ``latest`` (present=True, latest=True) and
    ``absent`` (present=False). Returns ``(present, latest, absent)`` spec
    tuples, each empty when no item asked for that state.
    """
    present = tuple(npm_spec(p) for p in packages if p.state == "present")
    latest = tuple(npm_spec(p) for p in packages if p.state == "latest")
    absent = tuple(npm_spec(p) for p in packages if p.state == "absent")
    return present, latest, absent


def nodejs_plan(
    hv: NodejsVars,
    npm_user: str,
    npm_config_prefix: str,
    npm_config_unsafe_perm: bool,
) -> NodejsPlan:
    """Freeze every target the deploy needs from the vars and the npm knobs.

    Pure, and called before the request wave: the NodeSource repository name and
    URI interpolate :attr:`NodejsVars.version`, the apt pin is the role's
    ``regex_replace('x', '') + '*'`` (``"16.x"`` -> ``"nodejs=16.*"``, which the
    operation's glob match converges on the second pass, issue #41), the profile
    script is rendered, and the global-package loop is partitioned by state. The
    ``npm_config_*`` knobs are passed in from ``host.extra`` (D5), not read off
    ``hv``.
    """
    version = hv.version
    present, latest, absent = partition_npm_packages(hv.npm_global_packages)
    return NodejsPlan(
        sources_filename=f"nodesource_{version}",
        repo_uri=f"{NODESOURCE_REPO_BASE}/node_{version}",
        nodejs_pin=f"nodejs={version.replace('x', '')}*",
        npm_user=npm_user,
        npm_config_prefix=npm_config_prefix,
        npm_config_unsafe_perm=npm_config_unsafe_perm,
        profile_content=(
            render_npm_profile(npm_config_prefix) if hv.generate_etc_profile else None
        ),
        npm_present=present,
        npm_latest=latest,
        npm_absent=absent,
        package_json=hv.package_json_path or None,
    )


# --- the deploy --------------------------------------------------------------


def install(plan: NodejsPlan) -> Deploy:
    """The whole Ubuntu role as one request wave: repo, package, npm wiring.

    Every value the role read from its variables is frozen into the plan, and
    there is no barrier, so this deploy is a pure function of it. The driver
    applies each request before pulling the next, which preserves the role's
    ordering -- dependencies, then the repository and its chained cache refresh,
    then the ``nodejs`` install that resolves against it. The role has no
    handlers, so there is no notification split and no checkpoint.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        # setup-Debian.yml task 1: the repository prerequisites.
        yield op(
            apt.packages,
            name="Ensure NodeSource dependencies are present",
            packages=DEPENDENCY_PACKAGES,
            sudo=True,
        )

        # setup-Debian.yml tasks 2-4 folded into one request (D1): the URL
        # signed_by fetches the armored key control-side and installs the
        # repo-named keyring, the deb822 sources file references it, and
        # update_cache=True chains one apt-get update on a real change -- the
        # role's "update cache if repo was added" task, with no barrier.
        yield op(
            apt.sources_file,
            name="Add NodeSource repository for Node.js",
            filename=plan.sources_filename,
            types="deb",
            uris=plan.repo_uri,
            suites="nodistro",
            components="main",
            signed_by=NODESOURCE_KEY_URL,
            update_cache=True,
            sudo=True,
        )

        # setup-Debian.yml task 5: the version-pinned nodejs package. The
        # ``nodejs=<major>.*`` glob pin converges on the second pass (issue #41).
        yield op(
            apt.packages,
            name="Ensure Node.js and npm are installed",
            packages=(plan.nodejs_pin,),
            sudo=True,
        )

        # tasks/main.yml: the global npm directory, owned by the install user so
        # that user can later install without sudo (D4).
        yield op(
            files.directory,
            name="Create npm global directory",
            path=plan.npm_config_prefix,
            user=plan.npm_user,
            group=plan.npm_user,
            mode="0755",
            sudo=True,
        )

        if plan.profile_content is not None:
            # tasks/main.yml: the /etc/profile.d/npm.sh environment script,
            # pre-rendered control-side (D8). files.install's idempotence is
            # content-only, so the paired files.file converges the mode:0644 the
            # template task enforces on every run (the java precedent).
            yield op(
                files.install,
                name="Add npm prefix bin directory to the global PATH",
                content=plan.profile_content,
                dest=NPM_PROFILE,
                mode="0644",
                sudo=True,
            )
            yield op(
                files.file,
                name="Set npm profile script mode",
                path=NPM_PROFILE,
                mode="0644",
                sudo=True,
            )

        # tasks/main.yml: the global npm package loop, partitioned by per-item
        # state into up to three requests (D7). Each carries the relocated prefix
        # and the unsafe-perm flag the role set as command environment.
        if plan.npm_present:
            yield op(
                npm.packages,
                name="Ensure npm global packages are installed",
                packages=plan.npm_present,
                global_=True,
                prefix=plan.npm_config_prefix,
                unsafe_perm=plan.npm_config_unsafe_perm,
                sudo=True,
            )
        if plan.npm_latest:
            yield op(
                npm.packages,
                name="Ensure npm global packages are up to date",
                packages=plan.npm_latest,
                latest=True,
                global_=True,
                prefix=plan.npm_config_prefix,
                unsafe_perm=plan.npm_config_unsafe_perm,
                sudo=True,
            )
        if plan.npm_absent:
            yield op(
                npm.packages,
                name="Ensure npm global packages are removed",
                packages=plan.npm_absent,
                present=False,
                global_=True,
                prefix=plan.npm_config_prefix,
                unsafe_perm=plan.npm_config_unsafe_perm,
                sudo=True,
            )

        if plan.package_json is not None:
            # tasks/main.yml: the opt-in package.json install (the role's
            # ``when: nodejs_package_json_path != ""``). Stateless -- npm applies
            # the manifest on every run, as the operation documents.
            yield op(
                npm.packages,
                name="Install packages defined in a given package.json",
                package_json=plan.package_json,
                prefix=plan.npm_config_prefix,
                unsafe_perm=plan.npm_config_unsafe_perm,
                sudo=True,
            )

    return deploy


# --- the role ----------------------------------------------------------------


def nodejs(v: VarsSet[NodejsVars]) -> Deploy:
    """Install Node.js and npm from NodeSource and wire up npm's global prefix.

    Args:
        v: The role's resolved variables, one :class:`NodejsVars` per host
            (``resolve_vars(NodejsVars, "nodejs", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs -- no
        other role consumes them -- so it returns ``None`` on every path,
        including the family guard's early return.

    Raises:
        ValueError: The npm install user cannot be resolved (no
            ``nodejs_install_npm_user`` and no connection login name, D4), an
            ``npm_config_prefix`` extra key is empty (D5), or
            ``apt.sources_file`` rejects a non-armored signing-key body (D3).
            The user and config resolution raise in the body before any request
            is built, so nothing is half-applied; the key rejection surfaces from
            the request wave's own materialization.
        TypeError: An ``npm_config_prefix`` or ``npm_config_unsafe_perm`` extra
            key carries the wrong type (D5).
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        # No barrier: the plan resolves from the variables, the inventory's own
        # connection data and the raw ``npm_config_*`` extra keys, so the whole
        # task list is one pure request wave.
        npm_user = resolve_npm_user(hv.install_npm_user, host.connection)
        npm_config_prefix, npm_config_unsafe_perm = resolve_npm_config(host.extra)
        plan = nodejs_plan(hv, npm_user, npm_config_prefix, npm_config_unsafe_perm)
        yield from install(plan)(host)

    return deploy


__all__ = [
    "NodejsVars",
    "NpmGlobalPackage",
    "nodejs",
]
