# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``users`` role (§17.5, §18.11).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, users(v), host_factory=...)``. The factory is plain
(non-``async``) -- so it is called, not awaited.

The load-bearing assertion is the migration's shape claim: the whole task
list is **one request wave** with **no barrier at all** on the default path
-- no fact probe, no checkpoint, no handler, because no task ``when`` reads
the machine (every Ansible gate here is a function of the variables). The
one conditional barrier is the ``fact(Users)`` snapshot an
``update_password: on_create`` password gates on (deviation D4), and it
appears *only* when the configuration actually combines the two. That shape
is read straight out of ``report.records`` via ``_barrier_shape``.

The second pass is the whole idempotence claim of the role and needs no
simulation bridge: the ``Users``/``Groups``/``GroupDetails`` rows, the
``authorized_keys`` and ``ssh_key_pairs`` stores and the ``.profile`` file
seams are all maintained by the operations' own mutators across passes, so
every request reads back exactly the state it asked for -- including the
password, which the simulation stores in its ``os_users`` row (on a real
host the shadow read-back that makes an explicit hash converge needs
``/etc/shadow`` readable by the login user -- deviation D1's caveat).

The home contents carry the same offline/live asymmetry, in the other
direction: the simulation serves the ``.ssh`` stat, the key-pair
fingerprint and the authorized-keys/profile seams whatever the home's mode,
so the pinned second-pass tree is a clean no-op here -- while a *live*
Ubuntu home (created ``0750`` by ``useradd -m``) hides exactly those paths
from the unprivileged probes, so the rerun re-plans and the regenerated
``ssh-keygen`` collides with the existing key. That is GitHub issue #71
(with #70 for the profile half), a library gap rather than a role bug.
"""

import pytest

from reemote import run_waves
from reemote.modules.server import Users
from reemote.roles import UserSpec, UsersVars, users
from reemote.roles.users import (
    BLOCK_BEGIN,
    BLOCK_END,
    authorized_keys_paths,
    profile_block_transform,
    users_plan,
)
from reemote.rolevars import resolve_vars
from reemote.stages import CheckpointRequest, FactRequest
from tests.simulated_host import SimulatedHost

ALICE_KEY = "ssh-ed25519 AAAAC3NzaC1lZD1yYWxpY2UgYWxpY2UubGFwdG9w"
ALICE_PROFILE = "export EDITOR=vim"


def make_inventory(**overrides) -> list[dict]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride in the entry's ``roles`` mapping under ``"users"``,
    bound by ``resolve_vars``. The base mapping covers every task of the role
    on its default path: two ``groups_to_create`` entries (one with a pinned
    gid), a fully specified user (comment, shell, uid, home, secondary
    groups, profile, authorized key), a second user who takes an explicit
    primary group and a generated key pair, and one deletion with both
    ``remove`` and ``force``. The overrides drive the gates and branches.
    """
    entry = {
        "host": "ubuntu",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.10",
            "username": "supervisor",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "ubuntu",
        "tags": ["ubuntu_host", "apt_package_manager"],
        "roles": {
            "users": {
                "groups_to_create": [
                    {"name": "developers", "gid": 10000},
                    {"name": "staff"},
                ],
                "users": [
                    {
                        "username": "alice",
                        "name": "Alice Full",
                        "shell": "/bin/zsh",
                        "uid": 1500,
                        "groups": ["sudo"],
                        "profile": ALICE_PROFILE,
                        "ssh_key": [ALICE_KEY],
                    },
                    {
                        "username": "bob",
                        "group": "developers",
                        "generate_ssh_key": True,
                    },
                ],
                "deleted": [
                    {"username": "carol", "remove": True, "force": True},
                ],
            },
        },
    }
    entry["roles"]["users"].update(overrides)
    return [
        entry,
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def _user_row(name: str, uid: int, **fields) -> dict:
    """A complete ``os_users`` row for seeding (every ``Users`` fact key)."""
    row = {
        "home": f"/home/{name}",
        "comment": None,
        "shell": "/bin/bash",
        "group": name,
        "groups": [],
        "uid": uid,
        "gid": uid,
        "lastlog": None,
        "login_time": None,
        "password": "",
    }
    row.update(fields)
    return row


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock Ubuntu machine where none of the managed users exist yet.

    ``carol`` is the deletion target -- present as an account, a group, a
    home directory with a profile and a mail spool, all of which the
    ``userdel -r`` simulation must purge. ``alice`` and ``bob`` are absent
    (both get created), ``sudo`` and ``staff`` exist but ``developers`` does
    not, so its creation carries the pinned gid.
    """
    host.os_groups = ["sudo", "staff", "carol"]
    host.os_group_details = {
        "sudo": {"gid": 27, "members": ()},
        "staff": {"gid": 50, "members": ()},
        "carol": {"gid": 1200, "members": ()},
    }
    host.os_users = {
        "supervisor": _user_row("supervisor", 1000, groups=["sudo"]),
        "carol": _user_row("carol", 1200),
    }
    host.remote_files = {
        "/home/carol/.profile": b"legacy profile line\n",
        "/var/spool/mail/carol": b"",
    }
    host.existing_files = set(host.remote_files)
    host.file_stats = {
        "/home/carol/.profile": {"user": "carol", "group": "carol", "mode": 644},
    }
    host.directory_stats = {
        "/home/carol": {"user": "carol", "group": "carol", "mode": 755},
    }
    host.link_stats = {}
    host.links = {}
    host.authorized_keys = {}
    host.ssh_key_pairs = {}


def make_hosts(inventory: list[dict]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict], hosts: dict[str, SimulatedHost], *, dry: bool = False
):
    """Resolve variables, build the sync factory's deploy, and run one wave pass."""
    v = resolve_vars(UsersVars, "users", inventory)

    def factory(entry: dict) -> SimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = users(v)
    report, _hosts = await run_waves(inventory, deploy, host_factory=factory, dry=dry)
    return report


def changed_entries(report) -> list:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def ubuntu_names(report) -> set[str]:
    """Every Ubuntu log entry name of one pass."""
    return {entry.name for entry in report.merged().log if entry.host == "ubuntu"}


def ubuntu_log(report) -> dict[str, object]:
    """The Ubuntu host's log entries of one pass, keyed by name."""
    return {
        entry.name: entry for entry in report.merged().log if entry.host == "ubuntu"
    }


def _kind(barrier) -> tuple:
    """Classify one barrier record for the shape assertion."""
    if isinstance(barrier, FactRequest):
        return ("fact", barrier.fact_cls)
    if isinstance(barrier, CheckpointRequest):
        return ("checkpoint", barrier.name)
    return ("other", type(barrier).__name__)


def _barrier_shape(report):
    """The ubuntu host's ordered ``(wave, kind)`` barrier list for one pass."""
    ubuntu = [r for r in report.records if r.host == "ubuntu"]
    return [(r.wave, _kind(r.barrier)) for r in ubuntu if r.barrier is not None]


def _request_waves(report) -> set[int]:
    """The set of wave numbers that carried at least one ubuntu request."""
    return {
        r.wave
        for r in report.records
        if r.host == "ubuntu" and r.log is not None
    }


# --- the stream shape (§18.11's falsifier) ------------------------------------


async def test_stream_is_one_wave_with_no_barriers():
    """The role's whole task list is a single request wave, barrier-free.

    No ``fact`` probe, no checkpoint, no handler: every gate in the Ansible
    role is a function of the variables, so the driver's apply-before-pull
    ordering carries the task order (groups, users, keys, profiles,
    deletions) inside one wave. A speculatively added barrier or a
    checkpoint would break this.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert _barrier_shape(report) == []
    assert _request_waves(report) == {0}

    # The rerun keeps the shape: nothing grew a read that needs one.
    second = await run_role(inventory, hosts)
    assert _barrier_shape(second) == []


async def test_on_create_password_adds_the_users_gate():
    """D4: only an ``on_create`` *with* a password takes the ``fact(Users)`` barrier."""
    inventory = make_inventory(
        users=[
            {
                "username": "erin",
                "password": "$6$erin",
                "update_password": "on_create",
            },
        ],
        deleted=[],
    )
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert _barrier_shape(report) == [(0, ("fact", Users))]
    assert _request_waves(report) == {1}

    # erin did not exist at the snapshot, so on_create still sets her hash --
    # and the second pass, where she does exist, drops the password argument.
    host = hosts["ubuntu"]
    assert host.os_users["erin"]["password"] == "$6$erin"
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert _barrier_shape(second) == [(0, ("fact", Users))]


async def test_existing_user_keeps_its_password_out_of_the_request():
    """The snapshot's other half: a present user is *not* re-passworded."""
    inventory = make_inventory(
        users=[
            {
                "username": "dave",
                "password": "$6$new",
                "update_password": "on_create",
            },
        ],
        deleted=[],
    )
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    host.os_users["dave"] = _user_row("dave", 1300, password="$6$old")

    await run_role(inventory, hosts)
    assert host.os_users["dave"]["password"] == "$6$old"


# --- the first pass really deploys the role -----------------------------------


async def test_first_pass_creates_and_deletes_everything():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    # Groups: developers created with its pinned gid, staff already there,
    # alice's per-user group from the users list (bob names one, so not his).
    assert "developers" in host.os_groups
    assert "alice" in host.os_groups
    assert host.os_group_details["developers"]["gid"] == 10000

    # alice: everything the user task templated for her.
    alice = host.os_users["alice"]
    assert alice["group"] == "alice"
    assert alice["groups"] == ["sudo"]  # requested list, not an append
    assert alice["shell"] == "/bin/zsh"
    assert alice["comment"] == "Alice Full"
    assert alice["uid"] == 1500
    assert alice["home"] == "/home/alice"

    # bob: the explicit primary group, his own per-user group *not* created,
    # and his generated key pair under the resolved home.
    assert host.os_users["bob"]["group"] == "developers"
    assert "bob" not in host.os_groups
    assert "/home/bob/.ssh/id_rsa.pub" in host.ssh_key_pairs

    # The authorized key landed at the *configured* home's default path.
    assert host.authorized_keys["/home/alice/.ssh/authorized_keys"] == [ALICE_KEY]

    # The profile block was created between the REEMOTE markers, 0644.
    profile = "/home/alice/.profile"
    assert host.remote_files[profile].decode() == (
        f"{BLOCK_BEGIN}\n{ALICE_PROFILE}\n{BLOCK_END}\n"
    )
    assert host.file_stats[profile]["mode"] == 644

    # carol: gone as user and group, home subtree and mail spool purged.
    assert "carol" not in host.os_users
    assert "carol" not in host.os_groups
    assert "/home/carol/.profile" not in host.remote_files
    assert "/var/spool/mail/carol" not in host.remote_files
    assert "/home/carol" not in host.directory_stats

    # Every task ran under its stable name.
    assert ubuntu_names(report) == {
        "Creating groups (developers)",
        "Creating groups (staff)",
        "Per-user group creation (alice)",
        "User creation (alice)",
        "User creation (bob)",
        "Generate SSH key for bob",
        "SSH keys (alice)",
        "Setup user profiles (alice)",
        "Set profile ownership (alice)",
        "Deleted user removal (carol)",
        "Deleted per-user group removal (carol)",
    }


async def test_second_pass_is_a_noop():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # The migration's idempotence claim: accounts, gids, keys and the profile
    # block all read back exactly as requested, so nothing changes anywhere.
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_omitted_groups_strip_secondary_memberships():
    """D2: an entry with no ``groups`` key means ``usermod -G ''``."""
    inventory = make_inventory(
        users=[{"username": "supervisor", "group": "sudo"}], deleted=[]
    )
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    assert host.os_users["supervisor"]["groups"] == ["sudo"]

    await run_role(inventory, hosts)
    assert host.os_users["supervisor"]["groups"] == []

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_no_homedirs_skips_profile_and_home_requests():
    """``users_create_homedirs: false`` gates createhome, ensure_home and profiles."""
    inventory = make_inventory(create_homedirs=False)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert "Setup user profiles (alice)" not in log
    assert "Set profile ownership (alice)" not in log
    assert log["User creation (alice)"].changed  # the account task still runs

    # The profile file was still never written.
    assert "/home/alice/.profile" not in hosts["ubuntu"].remote_files


async def test_non_ubuntu_host_is_never_touched():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    skipped = hosts["web1"]

    # The family guard returns before the first yield, so the off-target host
    # is in no wave, emits no log line and its stores never move.
    assert not any(entry.host == "web1" for entry in report.merged().log)
    assert not any(r.host == "web1" for r in report.records)
    assert skipped.os_users == {}
    assert skipped.os_groups == []


async def test_dry_preview_plans_the_whole_wave_and_applies_nothing():
    """Barrier-free means the dry pass plans the entire wave (the git/nodejs case).

    There is no barrier to park on, so every request is planned -- and none
    applied: the host's stores stay exactly as seeded.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts, dry=True)

    assert changed_entries(report) == []
    assert "developers" not in host.os_groups
    assert "alice" not in host.os_users
    assert "carol" in host.os_users
    planned = [r for r in report.records if r.host == "ubuntu" and r.planned is not None]
    assert len(planned) == 11  # every request of the default configuration


async def test_empty_configuration_emits_nothing():
    # No groups, no users, no deletions: the body plans no request at all and
    # takes no barrier, so the host contributes no records whatsoever.
    inventory = make_inventory(groups_to_create=[], users=[], deleted=[])
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert _barrier_shape(report) == []
    assert _request_waves(report) == set()
    assert changed_entries(report) == []


# --- the pure helpers, unit-tested directly ------------------------------------


def test_profile_block_transform_is_a_fixpoint():
    transform = profile_block_transform("export EDITOR=vim")

    created = transform(None)  # create: true is free
    assert created == f"{BLOCK_BEGIN}\nexport EDITOR=vim\n{BLOCK_END}\n"
    assert transform(created) == created  # the second pass no-ops

    # An existing marked region is replaced in place, around-text kept.
    drifted = f"head\n{BLOCK_BEGIN}\nstale\n{BLOCK_END}\ntail\n"
    replaced = transform(drifted)
    assert replaced == f"head\n{created}tail\n"
    assert transform(replaced) == replaced

    # A file with no markers gets the block appended.
    assert transform("plain\n") == f"plain\n{created}"


def test_authorized_keys_paths_splits_like_the_template():
    assert authorized_keys_paths("/home/a", ".ssh/authorized_keys") == (
        "/home/a/.ssh",
        "authorized_keys",
    )
    # A bare file name lands in the home itself, as the path template would.
    assert authorized_keys_paths("/home/a", "keys") == ("/home/a", "keys")


def test_plan_freezes_the_role_templating():
    hv = UsersVars(
        users=[
            {"username": "alice", "groups": ["sudo"]},
            {"username": "bob", "group": "staff", "uid": 1600},
        ],
        deleted=[{"username": "gone"}],
    )
    plan = users_plan(hv)

    # alice: no group key -> per-user group, gid from item.uid (unset -> None);
    # bob: explicit group -> no per-user group at all.
    assert plan.per_user_groups and plan.per_user_groups[0].name == "alice"
    assert plan.per_user_groups[0].gid is None
    alice = plan.users[0]
    assert alice.primary_group == "alice"
    assert alice.groups == ("sudo",)
    assert alice.comment == "" and alice.shell == "/bin/bash"
    assert alice.home == "/home/alice" and alice.create_home is True
    # D1: no password means no password argument, not '!' (issue #59's
    # leave-alone semantics; useradd locks the account by itself).
    assert alice.password is None and alice.only_on_create is False
    assert plan.users[1].primary_group == "staff"

    # The deletion gate: per-user groups on -> the group removal rides along.
    assert plan.deleted_groups == ("gone",)
    assert plan.needs_users_read is False


def test_plan_gates_profiles_and_the_users_read():
    hv = UsersVars(
        create_homedirs=False,
        users=[{"username": "a", "profile": "x", "update_password": "on_create"}],
    )
    plan = users_plan(hv)
    # create_homedirs off -> no profile target; on_create *without* a
    # password -> no Users read.
    assert plan.profiles == ()
    assert plan.needs_users_read is False

    with_password = UsersVars(
        users=[{"username": "a", "profile": "x", "password": "h", "update_password": "on_create"}]
    )
    gated = users_plan(with_password)
    assert gated.needs_users_read is True
    assert gated.profiles[0].path == "/home/a/.profile"


def test_vars_reject_invalid_values():
    # pydantic's ValidationError is a ValueError; every gate fails while the
    # variables resolve, before any host is contacted.
    with pytest.raises(ValueError, match="non-empty"):
        UsersVars(users=[{"username": ""}])
    with pytest.raises(ValueError, match="non-empty"):
        UsersVars(group="")
    with pytest.raises(ValueError, match="non-empty"):
        UsersVars(users=[{"username": "a", "ssh_key": [""]}])
    with pytest.raises(ValueError, match="update_password"):
        UserSpec(username="a", update_password="sometimes")
    with pytest.raises(ValueError, match="non-empty"):
        UsersVars(groups_to_create=[{"name": ""}])


def test_lists_become_tuples():
    # A list from JSON is normalized so every value reaching op() is hashable.
    hv = UsersVars(users=[{"username": "a", "groups": ["sudo", "staff"]}])
    assert hv.users[0].groups == ("sudo", "staff")


# --- the whole run as the driver prints it --------------------------------------

# Captured from a real ``report.tree()`` of the base inventory, not hand-written:
# one wave, requests in task order, the deterministic spool digests included
# (both derive from fixed target paths, so they are stable across runs).
FIRST_PASS_TREE = """(root)
  wave 0
    ubuntu: Creating groups (developers) [changed: group developers absent, will create]
        $ sudo groupadd developers --gid 10000
    ubuntu: Creating groups (staff)  [no change: group staff already present]
    ubuntu: Per-user group creation (alice) [changed: group alice absent, will create]
        $ sudo groupadd alice --gid 1500
    ubuntu: User creation (alice)    [changed: user alice absent, will create; home /home/alice missing, will create]
        $ sudo useradd -d /home/alice -s /bin/zsh -g alice -G sudo --uid 1500 -c 'Alice Full' -m alice
        $ sudo mkdir -p /home/alice
        $ sudo chown alice:alice /home/alice
    ubuntu: User creation (bob)      [changed: user bob absent, will create; home /home/bob missing, will create]
        $ sudo useradd -d /home/bob -s /bin/bash -g developers -m bob
        $ sudo mkdir -p /home/bob
        $ sudo chown bob:developers /home/bob
    ubuntu: Generate SSH key for bob [changed: /home/bob/.ssh missing, will create; /home/bob/.ssh/id_rsa.pub missing, will generate]
        $ sudo mkdir -p /home/bob/.ssh
        $ sudo chmod 700 /home/bob/.ssh
        $ sudo chown bob:bob /home/bob/.ssh
        $ sudo sh -c 'su -s /bin/sh bob -c '"'"'ssh-keygen -q -t rsa -b 4096 -f /home/bob/.ssh/id_rsa -N '"'"'"'"'"'"'"'"''"'"'"'"'"'"'"'"''"'"''
    ubuntu: SSH keys (alice)         [changed: /home/alice/.ssh missing, will create; /home/alice/.ssh/authorized_keys missing, will create; 1 key(s) missing, will append]
        $ sudo mkdir -p /home/alice/.ssh
        $ sudo chmod 700 /home/alice/.ssh
        $ sudo chown alice:alice /home/alice/.ssh
        $ sudo touch /home/alice/.ssh/authorized_keys
        $ sudo chmod 600 /home/alice/.ssh/authorized_keys
        $ sudo chown alice:alice /home/alice/.ssh/authorized_keys
        $ scp <authorized keys> -> /tmp/reemote-server-user-authorized-keys-878a0ffff73c2341/authorized_keys
        $ sudo sh -c 'mkdir -p /home/alice/.ssh && cp /tmp/reemote-server-user-authorized-keys-878a0ffff73c2341/authorized_keys /home/alice/.ssh/authorized_keys && chmod 600 /home/alice/.ssh/authorized_keys && rm -rf /tmp/reemote-server-user-authorized-keys-878a0ffff73c2341'
    ubuntu: Setup user profiles (alice) [changed: content /home/alice/.profile is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-3ee8d38b246cc725/content
        $ sudo sh -c 'mkdir -p /home/alice && cp /tmp/reemote-install-3ee8d38b246cc725/content /home/alice/.profile && chown alice:alice /home/alice/.profile && chmod 644 /home/alice/.profile && rm -rf /tmp/reemote-install-3ee8d38b246cc725'
    ubuntu: Set profile ownership (alice) [no change: file /home/alice/.profile already exists]
    ubuntu: Deleted user removal (carol) [changed: user carol present, will remove]
        $ sudo userdel -rf carol
    ubuntu: Deleted per-user group removal (carol) [changed: group carol present, will remove]
        $ sudo groupdel carol"""

CONVERGED_TREE = """(root)
  wave 0
    ubuntu: Creating groups (developers) [no change: group developers already present with gid 10000]
    ubuntu: Creating groups (staff)  [no change: group staff already present]
    ubuntu: Per-user group creation (alice) [no change: group alice already present with gid 1500]
    ubuntu: User creation (alice)    [no change: user alice already as requested; directory /home/alice already exists]
    ubuntu: User creation (bob)      [no change: user bob already as requested; directory /home/bob already exists]
    ubuntu: Generate SSH key for bob [no change: directory /home/bob/.ssh already exists; key pair /home/bob/.ssh/id_rsa.pub already exists]
    ubuntu: SSH keys (alice)         [no change: directory /home/alice/.ssh already exists; file /home/alice/.ssh/authorized_keys already exists]
    ubuntu: Setup user profiles (alice) [no change: content already up to date (/home/alice/.profile)]
    ubuntu: Set profile ownership (alice) [no change: file /home/alice/.profile already exists]
    ubuntu: Deleted user removal (carol) [no change: user carol already absent]
    ubuntu: Deleted per-user group removal (carol) [no change: group carol already absent]"""


async def test_tree_snapshot():
    """The whole run as one tree: a single wave, every task in role order.

    The first tree is the create/delete pass with each operation's commands;
    the second is the rerun -- the same wave, every line a no-change, which is
    the role's idempotence claim printed out.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == FIRST_PASS_TREE

    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE

