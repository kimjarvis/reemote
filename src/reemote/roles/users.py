# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The users role (migrated from ``singleplatform-eng/users``, a fork of
# ``geerlingguy.users``).
#
# Manages login accounts on an Ubuntu host exactly as the role's seven tasks
# do: the ``groups_to_create`` groups (each with its optional gid), the
# per-user group for every user that names no primary group, the users
# themselves (primary and secondary groups, shell, GECOS comment, uid, home,
# home-directory creation, an optional password hash, an optional generated
# SSH key pair), the ``ssh_key`` public keys into each user's authorized-keys
# file, the ``profile`` block appended to each ``.profile``, and last the
# deletion of the ``users_deleted`` accounts and their per-user groups.
# :func:`users` is the whole task list and :class:`UsersVars` is
# ``defaults/main.yml``, bound from the inventory entry's ``users`` role
# mapping by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. A host outside the family
# returns before its first ``yield`` and emits no requests, no barriers and no
# log lines at all. The role carries no ``include_vars`` dispatch and no OS
# branches, so there is nothing to freeze beyond the guard.
#
# The stream is **one request wave**, in the role's own task order -- groups,
# per-user groups, users (each followed by its key pair), authorized keys,
# profiles, deletions -- preceded by at most one barrier:
#
# 0. ``fact(Users)`` -- only when some user sets ``update_password:
#    on_create`` *and* a password; the read snapshots who existed before the
#    run, which is what that option gates on (deviation D4).
# 1. Every request. No checkpoint and no handler exist to place: the role has
#    none, and no task's ``when`` reads the machine -- every gate
#    (``'group' not in item``, ``users_create_per_user_group``,
#    ``users_create_homedirs``, ``item.profile is defined``, the
#    ``ssh_key``/``skip_missing`` subelements) is a pure function of the
#    resolved variables. The driver applies each request before pulling the
#    next, so the same-wave order is the apply order: ``groupadd`` lands
#    before the ``useradd`` that names the group, the ``useradd`` before the
#    ``ssh-keygen`` and the ``authorized_keys`` install that need the account,
#    and the deletions last, after every creation -- exactly the play's order.
#
# The role publishes no outputs (no composing role consumes them), so the
# deploy returns ``None`` on every path, including the family guard's.
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: the ``password: '!'`` default (lock the account) maps to *no password
#   argument*. ``useradd`` already writes a locked (``!``) shadow entry, so a
#   new account's post-state is identical; re-asserting ``!`` on an existing
#   user could never converge, because the ``Users`` fact's shadow read runs
#   unprivileged and reads ``\"\"`` wherever ``/etc/shadow`` is not group-readable --
#   the request would then plan a change on every pass. An explicitly set
#   hash *is* passed through, with the same convergence caveat: it settles
#   where the read-back works, and re-asserts where it does not (a locked
#   account is the default here; ``update_password: on_create`` is the gate
#   that avoids the read-back entirely).
# * D2: an omitted ``groups`` key means ``usermod -G ''`` -- Ansible joins the
#   omitted list to the empty string, which *removes* every secondary group
#   -- so the plan passes the explicit empty tuple, not ``None`` (issue #59
#   gives ``()`` exactly that reconcile-to-none semantics).
# * D3: ``generate_ssh_key`` rides the dedicated :func:`~reemote.server.ssh_keypair`
#   operation (issue #60) as its own request after the account exists, with
#   the library's defaults (rsa/4096 where Ansible's ``user`` module defaults
#   to 2048 -- a stronger key, generated at most once); a passphrase-protected
#   pair is refused by the operation, and the role never set one. The key
#   path is passed explicitly as ``<configured home>/.ssh/id_rsa`` -- Ansible
#   generated the pair under the templated home too, and the explicit path
#   skips the operation's default ``Home`` fact read, which cannot resolve a
#   user the current pass has not created yet (a dry preview plans key
#   generation for a brand-new account).
# * D4: ``update_password: on_create`` becomes the conditional ``fact(Users)``
#   barrier above: the snapshot decides who "already existed", and a gated
#   user who did loses its password argument for this pass. Ansible compares
#   against the state as the task itself sees it; the difference (a user
#   created earlier *in the same run* gets its password) is unreachable here
#   and harmless -- the creation request would have set it anyway.
# * D5: ``blockinfile`` becomes a role-local pure block transform on
#   ``files.transform`` (the documented recipe): the markers read
#   ``# BEGIN/END REEMOTE BLOCK`` rather than ``ANSIBLE MANAGED BLOCK``,
#   ``create: true`` is free (an absent file transforms from ``None``), and
#   because install-family idempotence is *content*-only, each transform is
#   paired with a ``files.file`` request so an owner- or mode-drifted
#   ``.profile`` still converges, as the task's ``owner``/``group``/``mode``
#   did.
# * D6: the ``authorized_key`` task loops once *per key*; one
#   :func:`~reemote.server.user_authorized_keys` request per user carries the
#   whole tuple instead (append-only, ``delete_keys=False``, the same
#   net state). The path is split into the operation's directory/filename
#   arguments from the *configured* home, so a user whose actual home
#   drifted still gets its keys in the home the inventory names -- as the
#   role's ``{{ item.0.home | default(...) }}`` path template did.
# * D7: ``ensure_home`` is tied to ``create_homedirs``: with
#   ``createhome: no`` the role's task touches no home directory at all, and
#   reemote's default would also converge an existing home's ownership.
# * the family guard is the body's opening ``if``, not a ``when=`` on every
#   request, and the role's implicit gates become pydantic validation: a
#   blank username or group name raises ``ValidationError`` (a ``ValueError``)
#   during ``resolve_vars``, before any host is contacted.
#
# Known live limitation (a library gap, not a deviation from the role): on a
# real Ubuntu host the second pass does not converge. ``useradd -m`` writes the
# new home as ``0750`` (Ubuntu's ``login.defs`` ``HOME_MODE``) and the login
# user is not in that group, so the unprivileged idempotence reads under it --
# the ``.ssh``/``authorized_keys`` stat, the ``SshKeyPair`` fingerprint, the
# profile's contents -- see a present file as *absent*. The re-planned
# :func:`~reemote.server.ssh_keypair` then reaches ``ssh-keygen``'s "already
# exists / Overwrite (y/n)?" prompt, which reads EOF and exits 1, and the
# profile transform re-writes on every pass. Tracked as GitHub issue #71 (the
# ``server`` reads) alongside #70 (the ``files`` reads); the offline suite
# passes because ``SimulatedHost`` serves those seams whatever the home's mode.
# """

from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..hosts import Host
from ..modules import files, server
from ..modules.server import Users
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, fact

# --- role constants (defaults/main.yml, Ubuntu path only) ---------------------

#: The marked-block fences of the profile transform. The Ansible task used
#: blockinfile's default ``ANSIBLE MANAGED BLOCK`` marker; reemote's block
#: convention (``examples/modules/files/files_block.py``) is the deviation D5.
BLOCK_BEGIN = "# BEGIN REEMOTE BLOCK"
BLOCK_END = "# END REEMOTE BLOCK"

#: The mode of every managed ``.profile``, from the task's ``mode: 0644``.
PROFILE_MODE = "0644"


# --- variables ---------------------------------------------------------------


class GroupSpec(BaseModel):
    """One ``groups_to_create`` entry: a name and an optional fixed gid."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``item.name``: the group to ensure.
    name: str
    #: ``item.gid``: ``None`` lets the system choose the id; a set gid is
    #: reconciled on an existing group (issue #62).
    gid: int | None = None

    @field_validator("name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("group name must be a non-empty string")
        return value


class UserSpec(BaseModel):
    """One ``users`` entry, with the role's per-item keys.

    Every optional key keeps the Jinja ``| default(...)`` / ``'x' in item``
    distinction the role was written with: ``None`` means "the entry omitted
    it", and :func:`users_plan` applies the template's fallback.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``item.username``: the login name (the role's required key).
    username: str
    #: ``item.name``: the GECOS comment; ``None`` becomes Ansible's
    #: ``default('')`` -- an explicit empty comment, which *clears* one
    #: (issue #59), faithful to the role.
    name: str | None = None
    #: ``item.group``: the primary group; when absent the role creates (or
    #: assumes) a per-user group, or falls back to :attr:`UsersVars.group`.
    group: str | None = None
    #: ``item.groups``: the secondary groups; ``None`` becomes Ansible's
    #: ``join(',')`` of nothing -- the empty string that strips every
    #: secondary group (D2).
    groups: tuple[str, ...] | None = None
    #: ``item.append``: add :attr:`groups` to the existing membership
    #: instead of replacing it.
    append: bool = False
    #: ``item.shell``: ``None`` falls back to :attr:`UsersVars.default_shell`.
    shell: str | None = None
    #: ``item.password``: the *encrypted* hash. ``None`` takes the D1
    #: mapping of Ansible's ``'!'`` lock default (no argument at all).
    password: str | None = None
    #: ``item.uid``: a fixed uid (the role omits it when unset).
    uid: int | None = None
    #: ``item.gid``: only read by the per-user *group* task, which templates
    #: ``item.gid | default(item.uid)``; it is not a ``user``-module option.
    gid: int | None = None
    #: ``item.home``: ``None`` falls back to ``/home/<username>``.
    home: str | None = None
    #: ``item.generate_ssh_key``: a key pair is generated after the account
    #: exists (D3).
    generate_ssh_key: bool = False
    #: ``item.update_password``: Ansible's module default is ``always``.
    #: ``on_create`` gates the password on the pre-run ``fact(Users)`` read
    #: (D4).
    update_password: Literal["always", "on_create"] = "always"
    #: ``item.ssh_key``: the public key strings (the ``with_subelements``
    #: ``skip_missing`` makes an absent list empty).
    ssh_key: tuple[str, ...] = ()
    #: ``item.profile``: the blockinfile text for ``<home>/.profile`` (D5).
    profile: str | None = None

    @field_validator("username")
    @classmethod
    def _username_non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("username must be a non-empty string")
        return value

    @field_validator("groups", "ssh_key", mode="before")
    @classmethod
    def _normalize_sequence(cls, value: Any) -> Any:
        """A list becomes a tuple; a non-sequence falls through to pydantic."""
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @field_validator("groups", "ssh_key")
    @classmethod
    def _entries_non_empty(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return value
        if not all(isinstance(item, str) and item for item in value):
            raise ValueError("must hold non-empty strings")
        return value


class DeletedUserSpec(BaseModel):
    """One ``users_deleted`` entry: a name plus the removal flags."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``item.username``: the account to remove.
    username: str
    #: ``item.remove``: also delete the home directory and mail spool
    #: (``userdel -r``; issue #61). The role's ``| default(omit)`` is the
    #: module's ``no``.
    remove: bool = False
    #: ``item.force``: remove even while logged in (``userdel -f``).
    force: bool = False

    @field_validator("username")
    @classmethod
    def _username_non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("username must be a non-empty string")
        return value


class UsersVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``users`` role mapping, so ``groups_to_create`` there
    sets :attr:`groups_to_create`. The model is frozen and every value that
    reaches :func:`~reemote.core.op` is hashable, so sequences are ``tuple``
    and the nested specs are frozen models themselves (design doc §8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``users_create_per_user_group``: create a group named after each user
    #: (no primary group given) and make it that user's primary group.
    create_per_user_group: bool = True
    #: ``users_group``: the primary group when per-user groups are off.
    group: str = "users"
    #: ``users_default_shell``: the shell for a user that names none.
    default_shell: str = "/bin/bash"
    #: ``users_create_homedirs``: ``createhome`` for the user task, and the
    #: gate on the whole profile task.
    create_homedirs: bool = True
    #: ``users``: the accounts to ensure.
    users: tuple[UserSpec, ...] = ()
    #: ``users_deleted``: the accounts to remove, last, after the creations.
    deleted: tuple[DeletedUserSpec, ...] = ()
    #: ``groups_to_create``: the groups to ensure, first.
    groups_to_create: tuple[GroupSpec, ...] = ()
    #: ``authorized_keys_file``: the per-user path (relative to the home) the
    #: SSH keys task writes, e.g. ``.ssh/authorized_keys``.
    authorized_keys_file: str = ".ssh/authorized_keys"

    @field_validator("users", "deleted", "groups_to_create", mode="before")
    @classmethod
    def _normalize_sequence(cls, value: Any) -> Any:
        """A list becomes a tuple; a non-sequence falls through to pydantic."""
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @field_validator("group", "default_shell", "authorized_keys_file")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """An empty name is a typo, not "unset"."""
        if not value:
            raise ValueError("must be a non-empty string")
        return value


# --- frozen per-host plan ------------------------------------------------------


@dataclass(frozen=True)
class GroupTarget:
    """One ``server.group`` creation: name plus the templated gid."""

    name: str
    gid: int | None


@dataclass(frozen=True)
class UserTarget:
    """One ``server.user`` creation with every Jinja default resolved."""

    username: str
    primary_group: str
    groups: tuple[str, ...]
    append: bool
    shell: str
    comment: str
    uid: int | None
    home: str
    create_home: bool
    password: str | None
    only_on_create: bool
    generate_ssh_key: bool


@dataclass(frozen=True)
class KeysTarget:
    """One ``server.user_authorized_keys`` request: the split key path (D6)."""

    username: str
    group: str
    directory: str
    filename: str
    public_keys: tuple[str, ...]


@dataclass(frozen=True)
class ProfileTarget:
    """One ``files.transform`` + ``files.file`` pair (D5)."""

    username: str
    group: str
    path: str
    block: str


@dataclass(frozen=True)
class DeletedTarget:
    """One ``server.user(present=False)`` removal."""

    username: str
    remove_home: bool
    force: bool


@dataclass(frozen=True)
class UsersPlan:
    """One host's resolved task targets, built by :func:`users_plan`.

    Every Jinja default, ``when`` gate and ``| default(omit)`` fallback the
    role templated is decided once here, so each deploy below is a pure
    function of this one object -- and of the optional ``Users`` snapshot,
    which only :func:`create_users` consults. ``needs_users_read`` is set
    when some user gates a password on ``on_create`` (D4);
    ``deleted_groups`` is already empty when per-user groups are off.
    """

    groups: tuple[GroupTarget, ...]
    per_user_groups: tuple[GroupTarget, ...]
    users: tuple[UserTarget, ...]
    keys: tuple[KeysTarget, ...]
    profiles: tuple[ProfileTarget, ...]
    deleted_users: tuple[DeletedTarget, ...]
    deleted_groups: tuple[str, ...]
    needs_users_read: bool


# --- pure helpers ------------------------------------------------------------


def authorized_keys_paths(home: str, authorized_keys_file: str) -> tuple[str, str]:
    """Split the task's ``{{ home }}/{{ authorized_keys_file }}`` path.

    ``server.user_authorized_keys`` takes the directory and the file name
    separately; joining first and splitting at the last slash reproduces the
    template exactly, including a file name with no directory part (it lands
    in the home itself, as the role's literal path template would).
    """
    joined = f"{home}/{authorized_keys_file}"
    directory, _, filename = joined.rpartition("/")
    return directory or "/", filename


def profile_block_transform(block: str) -> Callable[[str | None], str]:
    """Build the pure, idempotent marked-block transform for one profile.

    The blockinfile mapping of construct-recipes 6, carried role-local (the
    ``examples/modules/files/files_block.py`` ``block_transform`` semantics
    narrowed to the plain append/replace case the task uses): the block
    lines fenced by :data:`BLOCK_BEGIN`/data:`BLOCK_END` replace an existing
    marked region in place, are appended when no markers exist (an absent
    file transforms from ``None``, which is the task's ``create: true``),
    and a byte-identical region is returned unchanged -- the fixpoint that
    makes a second pass a no-op.
    """
    block_lines = [BLOCK_BEGIN, *block.split("\n"), BLOCK_END]

    def transform(current: str | None) -> str:
        text = current or ""
        lines = text.splitlines()

        # Locate an existing marked region (begin marker, then end marker).
        start = stop = None
        if BLOCK_BEGIN in lines:
            i = lines.index(BLOCK_BEGIN)
            if BLOCK_END in lines[i + 1 :]:
                start, stop = i, lines.index(BLOCK_END, i + 1)

        if start is not None:
            if lines[start : stop + 1] == block_lines:
                return text  # fixpoint: byte-identical, no rewrite
            new_lines = lines[:start] + block_lines + lines[stop + 1 :]
        else:
            new_lines = lines + block_lines  # append (create: true for free)
        return "\n".join(new_lines) + "\n"

    return transform


def users_plan(hv: UsersVars) -> UsersPlan:
    """Freeze the whole task list from the resolved vars; no host is read.

    Reproduces each task's templating: the primary group falls back to the
    username (per-user groups on) or :attr:`UsersVars.group` (off), the
    per-user group's gid to ``item.gid | default(item.uid)``, the shell to
    :attr:`UsersVars.default_shell`, the home to ``/home/<username>``, the
    comment to ``''``, and the omitted ``groups`` list to the empty tuple
    (D2). The task ``when`` gates are applied here, so an empty section
    emits no requests at all.
    """
    groups = tuple(
        GroupTarget(name=spec.name, gid=spec.gid) for spec in hv.groups_to_create
    )

    per_user_groups: list[GroupTarget] = []
    user_targets: list[UserTarget] = []
    keys: list[KeysTarget] = []
    profiles: list[ProfileTarget] = []

    for spec in hv.users:
        if spec.group is None and hv.create_per_user_group:
            # The role's gid is item.gid | default(item.uid) | default(omit).
            gid = spec.gid if spec.gid is not None else spec.uid
            per_user_groups.append(GroupTarget(name=spec.username, gid=gid))
        primary_group = (
            spec.group
            if spec.group is not None
            else (spec.username if hv.create_per_user_group else hv.group)
        )
        home = spec.home if spec.home is not None else f"/home/{spec.username}"

        user_targets.append(
            UserTarget(
                username=spec.username,
                primary_group=primary_group,
                # Ansible's `groups: item.groups|join(',') else ''` -- the
                # empty string strips every secondary group (D2).
                groups=tuple(spec.groups) if spec.groups is not None else (),
                append=spec.append,
                shell=spec.shell if spec.shell is not None else hv.default_shell,
                comment=spec.name if spec.name is not None else "",
                uid=spec.uid,
                home=home,
                create_home=hv.create_homedirs,
                password=spec.password,  # None carries D1's locked default
                only_on_create=spec.update_password == "on_create",
                generate_ssh_key=spec.generate_ssh_key,
            )
        )

        if spec.ssh_key:
            directory, filename = authorized_keys_paths(
                home, hv.authorized_keys_file
            )
            keys.append(
                KeysTarget(
                    username=spec.username,
                    group=primary_group,
                    directory=directory,
                    filename=filename,
                    public_keys=tuple(spec.ssh_key),
                )
            )

        # `when: users_create_homedirs and item.profile is defined`
        if hv.create_homedirs and spec.profile is not None:
            profiles.append(
                ProfileTarget(
                    username=spec.username,
                    group=primary_group,
                    path=f"{home}/.profile",
                    block=spec.profile,
                )
            )

    deleted_users = tuple(
        DeletedTarget(username=spec.username, remove_home=spec.remove, force=spec.force)
        for spec in hv.deleted
    )
    deleted_groups = (
        tuple(spec.username for spec in hv.deleted)
        if hv.create_per_user_group
        else ()
    )

    return UsersPlan(
        groups=groups,
        per_user_groups=tuple(per_user_groups),
        users=tuple(user_targets),
        keys=tuple(keys),
        profiles=tuple(profiles),
        deleted_users=deleted_users,
        deleted_groups=deleted_groups,
        needs_users_read=any(
            t.only_on_create and t.password for t in user_targets
        ),
    )


# --- deploys -----------------------------------------------------------------


def create_groups(plan: UsersPlan) -> Deploy:
    """``Creating groups`` and ``Per-user group creation``, in task order.

    A requested gid is reconciled on an existing group (``groupmod -g``,
    issue #62), so the tasks' re-runs pin the id rather than only creating.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for target in plan.groups:
            yield op(
                server.group,
                name=f"Creating groups ({target.name})",
                group=target.name,
                gid=target.gid,
                sudo=True,
            )
        for target in plan.per_user_groups:
            yield op(
                server.group,
                name=f"Per-user group creation ({target.name})",
                group=target.name,
                gid=target.gid,
                sudo=True,
            )

    return deploy


def create_users(plan: UsersPlan, existing: dict[str, Any] | None) -> Deploy:
    """``User creation``, each account followed by its optional key pair.

    The Ansible task's templated defaults are all frozen in the plan; the
    one runtime input is the ``on_create`` snapshot: a user who existed
    before the run loses its password argument (D4). ``ensure_home`` rides
    ``create_home`` (D7). ``generate_ssh_key`` is its own
    ``server.ssh_keypair`` request right after the account (D3), so the
    ``.ssh`` directory lands in the home the previous step created -- the
    driver applies before pulling, so the order is the apply order. The key
    path is the operation's default shape (``.ssh/id_rsa`` under the home)
    built from the *configured* home, so no ``Home`` fact read happens at
    plan time.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for t in plan.users:
            password = t.password
            if (
                password is not None
                and t.only_on_create
                and existing is not None
                and t.username in existing
            ):
                password = None
            yield op(
                server.user,
                name=f"User creation ({t.username})",
                user=t.username,
                group=t.primary_group,
                groups=t.groups,
                append=t.append,
                shell=t.shell,
                password=password,
                comment=t.comment,
                uid=t.uid,
                home=t.home,
                create_home=t.create_home,
                ensure_home=t.create_home,
                sudo=True,
            )
            if t.generate_ssh_key:
                yield op(
                    server.ssh_keypair,
                    name=f"Generate SSH key for {t.username}",
                    user=t.username,
                    # The operation's default type is rsa, so this is exactly
                    # its own ``{home}/.ssh/id_{type}`` path -- stated from
                    # the plan instead of read from the host (D3).
                    path=f"{t.home}/.ssh/id_rsa",
                    sudo=True,
                )

    return deploy


def authorize_keys(plan: UsersPlan) -> Deploy:
    """``SSH keys``: one request per user carrying that user's key tuple.

    The role looped ``authorized_key`` once per key; the operation appends
    every missing key from one request (``delete_keys`` stays False -- the
    task never removed keys; D6). The explicit directory/filename pair keeps
    the configured home authoritative and bypasses the operation's own
    ``Home`` fact read, as the task's path template did.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for t in plan.keys:
            yield op(
                server.user_authorized_keys,
                name=f"SSH keys ({t.username})",
                user=t.username,
                public_keys=t.public_keys,
                group=t.group,
                authorized_key_directory=t.directory,
                authorized_key_filename=t.filename,
                sudo=True,
            )

    return deploy


def write_profiles(plan: UsersPlan) -> Deploy:
    """``Setup user profiles``: the block transform plus its metadata pair.

    Each user's ``profile`` text is ensured between the REEMOTE BLOCK
    markers of ``<home>/.profile`` (D5). The transform's install is
    content-idempotent and carries the task's ``owner``/``group``/``mode``
    onto every *write*; the paired ``files.file`` request is what converges
    a later owner- or mode-only drift, which the content check alone would
    never notice -- the blockinfile behaviour the task relied on.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for t in plan.profiles:
            yield op(
                files.transform,
                name=f"Setup user profiles ({t.username})",
                path=t.path,
                transform=profile_block_transform(t.block),
                mode=PROFILE_MODE,
                user=t.username,
                group=t.group,
                sudo=True,
            )
            yield op(
                files.file,
                name=f"Set profile ownership ({t.username})",
                path=t.path,
                user=t.username,
                group=t.group,
                mode=PROFILE_MODE,
                sudo=True,
            )

    return deploy


def remove_deleted(plan: UsersPlan) -> Deploy:
    """``Deleted user removal``, then ``Deleted per-user group removal``.

    ``remove``/``force`` render onto the one ``userdel`` (issue #61); the
    group task ran only with per-user groups on, and the plan already
    froze that gate, so an empty :attr:`UsersPlan.deleted_groups` is the
    whole skip. Deletions come last, as in the play.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for t in plan.deleted_users:
            yield op(
                server.user,
                name=f"Deleted user removal ({t.username})",
                user=t.username,
                present=False,
                remove_home=t.remove_home,
                force=t.force,
                sudo=True,
            )
        for name in plan.deleted_groups:
            yield op(
                server.group,
                name=f"Deleted per-user group removal ({name})",
                group=name,
                present=False,
                sudo=True,
            )

    return deploy


# --- the role ----------------------------------------------------------------


def users(v: VarsSet[UsersVars]) -> Deploy:
    """Ensure, key and profile the configured users on Ubuntu hosts.

    Args:
        v: The role's resolved variables, one :class:`UsersVars` per host
            (``resolve_vars(UsersVars, "users", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs --
        no other role consumes them -- so it returns ``None`` on every path,
        including the family guard's early return.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        plan = users_plan(hv)

        existing: dict[str, Any] | None = None
        if plan.needs_users_read:
            # Wave 0 (conditional): the pre-run snapshot an on_create
            # password gates on (D4). Nothing else in the role reads the
            # machine, so the whole task list below is a pure function of
            # the plan and, for every other configuration, no barrier is
            # taken at all.
            existing = yield fact(Users)

        # Wave 1: every request, in the play's task order -- the driver
        # applies each before pulling the next, so the groupadd-before-
        # useradd-before-keys-before-profiles ordering holds inside one
        # wave, and the deletions still land last, as in the play.
        yield from create_groups(plan)(host)
        yield from create_users(plan, existing)(host)
        yield from authorize_keys(plan)(host)
        yield from write_profiles(plan)(host)
        yield from remove_deleted(plan)(host)

    return deploy


__all__ = [
    "DeletedUserSpec",
    "GroupSpec",
    "UserSpec",
    "UsersVars",
    "users",
]
