# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``mailhog`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, mailhog(v), host_factory=...)``. The factory is plain
(non-``async``): the role's one read between tasks is the ``fact(server.Arch)``
gate, and the two binary fetches live *inside* ``files.download`` -- the
control-side ``_fetch_url_bytes`` seam the tests stub, so the downloads are
answered offline exactly as the kubectl/composer tests do. The stub answers
each URL with its *own* canned bytes, so the two binaries stay distinguishable
in ``remote_files`` and a swapped URL would miscompare.

The load-bearing assertion is the migration's idempotence claim: run the role,
then run it again against the same persistent hosts, and the second pass
reports no change anywhere -- the directory exists, both binaries' content
matches what the URLs serve, the unit file is the rendered text, the service
is up and enabled, and (deviation D7) no ``daemon-reload`` is issued, because
the reload is state-gated. The converged pass *does* re-fetch both URLs
control-side (deviation D3: content-only idempotence needs the bytes to
compare), which is asserted rather than hidden.

The tree snapshots below are pinned from a real simulated run; the spool
directory hashes are derived from the destination paths, so they are stable
across runs.
"""

import hashlib
from typing import Any

import pytest

from reemote import run_waves
from reemote.core import Privilege
from reemote.modules.files import files as files_module
from reemote.roles import MailhogVars, mailhog
from reemote.roles.mailhog import (
    UNIT_PATH,
    arch_for,
    mailhog_binary_url,
    mhsendmail_binary_url,
    render_unit,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from reemote.stages import FactRequest
from tests.simulated_host import SimulatedHost

INSTALL_DIR = "/opt/mailhog"
MH_BIN = "/opt/mailhog/mailhog"
MHS_BIN = "/opt/mailhog/mhsendmail"
MH_URL = (
    "https://github.com/mailhog/MailHog/releases/download/v1.0.0/MailHog_linux_amd64"
)
MHS_URL = "https://github.com/mailhog/mhsendmail/releases/download/v0.2.0/mhsendmail_linux_amd64"

DIR_NAME = "Ensure mailhog install directory exists."
MH_NAME = "Download the MailHog binary."
MHS_NAME = "Download the mhsendmail binary."
UNIT_NAME = "Copy mailhog systemd unit file into place."
SERVICE_NAME = "Ensure mailhog is enabled and will start on boot."

# Non-UTF-8 bytes drive files.download down the issue-#23 binary install
# branch, exactly like the real MailHog/mhsendmail ELF archives.
FAKE_MAILHOG = b"\x7fELF\x02\x01\x00\xff\xfe mailhog-canned-bytes \x00"
FAKE_MHSENDMAIL = b"\x7fELF\x02\x01\x00\xfe\xff mhsendmail-canned-bytes \x00"


@pytest.fixture
def canned_fetch(monkeypatch):
    """Replace the control-side download with a canned-bytes stub.

    ``files.download`` looks ``_fetch_url_bytes`` up as a module global at call
    time, so patching the module attribute redirects the fetch. Returns the
    list of URLs the stub was called with, so a test can assert the fetches
    happened (and that a skipped host and a ``dry`` preview fetch nothing).
    Each URL maps to its own bytes, so a mailhog/mhsendmail swap cannot pass
    the content comparisons unnoticed.
    """
    fetched: list[str] = []

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        if "mhsendmail" in url:
            return FAKE_MHSENDMAIL
        return FAKE_MAILHOG

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock x86_64 Ubuntu machine, no MailHog yet.

    Only ``/tmp`` exists besides (the download spools land there); the role's
    own ``files.directory`` request creates ``/opt/mailhog`` and the unit's
    install creates ``/etc/systemd/system``. The ``Arch`` fact answers from
    the defaulted ``arch`` field (``x86_64``).
    """
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.directory_stats = {
        "/tmp": {"mode": 1777, "user": "root", "group": "root"},
    }
    host.existing_files |= set(host.directory_stats)


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride in the entry's ``roles`` mapping under
    ``"mailhog"``, bound by ``resolve_vars``.
    """
    entry: dict[str, Any] = {
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
            "mailhog": {},
        },
    }
    entry["roles"]["mailhog"].update(overrides)
    return [
        entry,
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def make_hosts(inventory: list[dict[str, Any]]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve variables, build the sync factory's deploy, and run one wave pass."""
    v = resolve_vars(MailhogVars, "mailhog", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = mailhog(v)
    report, _hosts = await run_waves(
        inventory, deploy, host_factory=factory, dry=dry
    )
    return report


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def entry_names(report: Any) -> set[str]:
    """Every log entry name of one pass."""
    return {entry.name for entry in report.merged().log}


def host_records(report: Any, host: str) -> list[Any]:
    """Every record (request step or barrier) attributed to *host*."""
    return [record for record in report.records if record.host == host]


def barriers(report: Any, host: str = "ubuntu") -> list[Any]:
    """The barrier objects *host* parked on, in wave order."""
    return [record.barrier for record in host_records(report, host) if record.barrier]


def waves(report: Any, host: str = "ubuntu") -> list[int]:
    """The distinct wave numbers *host* emitted records in."""
    return sorted({record.wave for record in host_records(report, host)})


def entry_named(report: Any, name: str) -> Any:
    """The single ubuntu log entry called *name*."""
    return next(
        entry
        for entry in report.merged().log
        if entry.name == name and entry.host == "ubuntu"
    )


def request_for(report: Any, name: str) -> Any:
    """The planned ``OperationRequest`` called *name* (meta + kwargs live)."""
    record = next(
        record
        for record in report.records
        if record.planned is not None and record.planned.request.name == name
    )
    return record.planned.request


# --- the first pass really installs mailhog ------------------------------------


async def test_first_pass_installs_and_runs_mailhog(canned_fetch):
    """The full stream: Arch gate, directory, two downloads, unit, service."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The role fetched both amd64 release URLs of its default versions.
    assert canned_fetch == [MH_URL, MHS_URL]

    # All five requests ran, in the role's task order.
    assert entry_names(report) == {DIR_NAME, MH_NAME, MHS_NAME, UNIT_NAME, SERVICE_NAME}
    assert [e.name for e in changed_entries(report)] == [
        DIR_NAME,
        MH_NAME,
        MHS_NAME,
        UNIT_NAME,
        SERVICE_NAME,
    ]

    # The install directory was created root:root at 0755; both binaries
    # landed at 0755 with their own content; the unit is the rendered text.
    assert host.directory_stats[INSTALL_DIR] == {
        "mode": 755,
        "user": "root",
        "group": "root",
    }
    assert host.remote_files[MH_BIN] == FAKE_MAILHOG
    assert host.remote_files[MHS_BIN] == FAKE_MHSENDMAIL
    assert host.file_stats[MH_BIN]["mode"] == 755
    assert host.file_stats[MH_BIN]["user"] == "root"
    assert host.file_stats[MHS_BIN]["mode"] == 755
    assert host.remote_files[UNIT_PATH] == render_unit(INSTALL_DIR).encode()
    assert host.file_stats[UNIT_PATH]["mode"] == 755  # deviation D6

    # The service is up and enabled under its Debian unit name.
    assert host.services["mailhog.service"] == "active"
    assert "mailhog.service" in host.enabled_services


async def test_requests_are_privileged_spool_installs(canned_fetch):
    """Deviation D9: ``sudo=True`` on every request; the transfer itself never elevates."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    for name in (DIR_NAME, MH_NAME, MHS_NAME, UNIT_NAME, SERVICE_NAME):
        # ``op(sudo=True)`` normalizes onto the request as the Privilege enum.
        assert request_for(report, name).sudo is Privilege.ON, name

    # The unit install carries the rendered text and the role's path; the
    # service request is the whole D7 (running + enabled + gated reload).
    unit = request_for(report, UNIT_NAME).kwargs
    assert unit["dest"] == UNIT_PATH
    assert unit["content"] == render_unit(INSTALL_DIR)
    service = request_for(report, SERVICE_NAME).kwargs
    assert service["service"] == "mailhog"
    assert service["running"] is True
    assert service["enabled"] is True
    assert service["daemon_reload"] is True


async def test_non_ubuntu_host_is_never_touched(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.remote_files == {}
    assert skipped.directory_stats == {}
    assert skipped.services == {}
    # The family guard returns before the first yield, so the off-target host
    # emits no log entry, parks on no barrier and fetches nothing.
    assert not any(entry.host == "web1" for entry in report.merged().log)
    assert host_records(report, "web1") == []


# --- the second pass is a no-op -------------------------------------------------


async def test_second_pass_changes_nothing(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    # Deviation D3, asserted: the converged pass still fetches both URLs
    # control-side to compare content, but the install read-back
    # short-circuits, so nothing is written again.
    assert canned_fetch == [MH_URL, MHS_URL, MH_URL, MHS_URL]
    assert hosts["ubuntu"].remote_files[MH_BIN] == FAKE_MAILHOG
    assert hosts["ubuntu"].remote_files[UNIT_PATH] == render_unit(INSTALL_DIR).encode()


async def test_daemon_reload_only_rides_the_wave_that_wrote_a_unit(canned_fetch):
    """Deviation D7's state gate: reload on pass 1, none on pass 2.

    The unit install marks the service reload-pending; the same wave's
    ``systemd.service`` request consumes the mark with a ``daemon-reload``.
    The converged pass writes nothing, so the pending set stays empty and the
    request plans only the (already-true) running/enabled checks.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert "daemon-reload" in entry_named(first, SERVICE_NAME).reason
    assert hosts["ubuntu"].systemd_reload_pending == set()  # consumed in-wave

    second = await run_role(inventory, hosts)
    assert "daemon-reload" not in second.tree()
    assert "mailhog.service already running" in entry_named(
        second, SERVICE_NAME
    ).reason


# --- the dry preview parks at the gate ------------------------------------------


async def test_dry_preview_mutates_nothing(canned_fetch):
    """``dry=True`` parks at the ``Arch`` gate, so no request is even planned.

    Unlike the barrier-free roles, the URL-carrying downloads cannot be built
    before the architecture is read -- the preview is exactly the one barrier
    line and nothing else.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    before_dirs = dict(host.directory_stats)

    await run_role(inventory, hosts, dry=True)
    assert canned_fetch == []
    assert host.remote_files == {}
    assert host.file_stats == {}
    assert host.services == {}
    assert host.directory_stats == before_dirs


# --- the architecture gate -------------------------------------------------------


async def test_aarch64_installs_arm_binaries(canned_fetch):
    """The ``fact(server.Arch)`` result drives the release archive suffix (D4)."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["ubuntu"].arch = "aarch64"

    await run_role(inventory, hosts)
    assert canned_fetch == [
        "https://github.com/mailhog/MailHog/releases/download/v1.0.0/MailHog_linux_arm",
        "https://github.com/mailhog/mhsendmail/releases/download/v0.2.0/mhsendmail_linux_arm",
    ]
    assert hosts["ubuntu"].remote_files[MH_BIN] == FAKE_MAILHOG

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_other_machines_take_the_amd64_default(canned_fetch):
    """The role's ternary has no fail-fast: anything not-aarch64 is ``amd64``."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["ubuntu"].arch = "riscv64"

    await run_role(inventory, hosts)
    assert canned_fetch == [MH_URL, MHS_URL]


# --- the variables -----------------------------------------------------------------


async def test_version_overrides_change_both_urls(canned_fetch):
    """``mailhog_version``/``mhsendmail_version`` feed the URL formulas."""
    inventory = make_inventory(version="1.0.1", mhsendmail_version="0.3.0")
    hosts = make_hosts(inventory)

    await run_role(inventory, hosts)
    assert canned_fetch == [
        "https://github.com/mailhog/MailHog/releases/download/v1.0.1/MailHog_linux_amd64",
        "https://github.com/mailhog/mhsendmail/releases/download/v0.3.0/mhsendmail_linux_amd64",
    ]


async def test_full_binary_url_overrides_win(canned_fetch):
    """The ``*_binary_url`` knobs replace the whole formula (mirror stance)."""
    inventory = make_inventory(
        version="9.9.9",  # must be ignored: the full URLs win
        binary_url="https://mirror.example.com/MailHog",
        mhsendmail_binary_url="https://mirror.example.com/mhsendmail",
    )
    hosts = make_hosts(inventory)

    await run_role(inventory, hosts)
    assert canned_fetch == [
        "https://mirror.example.com/MailHog",
        "https://mirror.example.com/mhsendmail",
    ]
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_custom_install_dir_moves_everything(canned_fetch):
    """``mailhog_install_dir`` relocates the tree *and* the unit's ExecStart."""
    inventory = make_inventory(install_dir="/srv/mailcatcher/")  # trailing / stripped
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    await run_role(inventory, hosts)
    assert host.directory_stats["/srv/mailcatcher"]["mode"] == 755
    assert host.remote_files["/srv/mailcatcher/mailhog"] == FAKE_MAILHOG
    assert host.remote_files["/srv/mailcatcher/mhsendmail"] == FAKE_MHSENDMAIL
    unit = host.remote_files[UNIT_PATH].decode()
    assert "ExecStart=/srv/mailcatcher/mailhog\n" in unit

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


def test_typoed_role_variable_is_reported_not_ignored():
    inventory = make_inventory(instal_dir="/opt/mh")
    hosts = make_hosts(inventory)

    with pytest.raises(RoleVarsError, match="instal_dir"):
        resolve_vars(MailhogVars, "mailhog", inventory)
    # Nothing ran: the failure is at resolve time, before any host is
    # contacted, so both hosts keep their seeded stores untouched.
    assert hosts["ubuntu"].remote_files == {}
    assert hosts["web1"].remote_files == {}


# --- the pure helpers, unit-tested directly -----------------------------------------


def test_arch_for_is_the_roles_ternary():
    assert arch_for("aarch64") == "arm"
    assert arch_for("x86_64") == "amd64"
    # No fail-fast map (unlike kubectl): the role's answer for everything
    # else is "amd64", the only other archive MailHog publishes.
    assert arch_for("riscv64") == "amd64"
    assert arch_for("armv7l") == "amd64"


def test_binary_url_formulas():
    assert mailhog_binary_url("1.0.0", "amd64") == MH_URL
    assert mhsendmail_binary_url("0.2.0", "arm") == (
        "https://github.com/mailhog/mhsendmail/releases/download/v0.2.0/mhsendmail_linux_arm"
    )


def test_render_unit_is_byte_exact():
    """The f-string reproduces ``mailhog.unit.j2``' Jinja2 output verbatim (D5)."""
    assert render_unit("/opt/mailhog") == (
        "[Unit]\n"
        "Description=MailHog Email Catcher\n"
        "After=syslog.target network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        "ExecStart=/opt/mailhog/mailhog\n"
        "StandardOutput=journal\n"
        "Restart=on-failure\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    assert render_unit("/srv/x").count("ExecStart=/srv/x/mailhog") == 1


# --- variable validation -------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"install_dir": "opt/mailhog"}, "absolute"),
        ({"install_dir": ""}, "non-empty string"),
        ({"version": ""}, "non-empty string"),
        ({"version": "   "}, "non-empty string"),
        ({"version": "1.0/2"}, "plain release name"),
        ({"version": ".hidden"}, "plain release name"),
        ({"mhsendmail_version": "x/y"}, "plain release name"),
        ({"binary_url": "ftp://m/MailHog"}, "http\\(s\\) URL"),
        ({"mhsendmail_binary_url": "mailto:x"}, "http\\(s\\) URL"),
        ({"unknown_knob": True}, "unknown_knob"),
    ],
)
def test_vars_reject_invalid_values(kwargs, match):
    # pydantic's ValidationError is a ValueError; so is every validator raise.
    with pytest.raises(ValueError, match=match):
        MailhogVars(**kwargs)


def test_vars_normalization():
    assert MailhogVars(install_dir="/opt/mailhog/").install_dir == "/opt/mailhog"
    # A ``v``-prefixed version keeps the URL formula's single-v convention.
    assert MailhogVars(version="v1.0.1").version == "1.0.1"
    assert MailhogVars(mhsendmail_version=" 0.3.0 ").mhsendmail_version == "0.3.0"
    assert MailhogVars().binary_url is None


# --- the wave tree (design doc section 7.2) -------------------------------------------

# Pinned from a real simulated run (the captured output, verbatim). Two waves
# on every pass: the ``fact(server.Arch)`` gate, then the five requests in the
# role's task order; the converged rerun keeps the same shape, all no-change,
# and no daemon-reload line. ``dry`` parks at the gate, so its tree is the
# barrier alone.
FIRST_PASS_TREE = """(root)
  wave 0
    barrier: Arch → x86_64
  wave 1
    ubuntu: Ensure mailhog install directory exists. [changed: /opt/mailhog missing, will create]
        $ sudo mkdir -p /opt/mailhog
        $ sudo chmod 755 /opt/mailhog
        $ sudo chown root:root /opt/mailhog
    ubuntu: Download the MailHog binary. [changed: content /opt/mailhog/mailhog is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-5cd5d269279d9a3b/content
        $ sudo sh -c 'mkdir -p /opt/mailhog && cp /tmp/reemote-install-5cd5d269279d9a3b/content /opt/mailhog/mailhog && chown root:root /opt/mailhog/mailhog && chmod 755 /opt/mailhog/mailhog && rm -rf /tmp/reemote-install-5cd5d269279d9a3b'
    ubuntu: Download the mhsendmail binary. [changed: content /opt/mailhog/mhsendmail is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-b38a7168faa5f222/content
        $ sudo sh -c 'mkdir -p /opt/mailhog && cp /tmp/reemote-install-b38a7168faa5f222/content /opt/mailhog/mhsendmail && chown root:root /opt/mailhog/mhsendmail && chmod 755 /opt/mailhog/mhsendmail && rm -rf /tmp/reemote-install-b38a7168faa5f222'
    ubuntu: Copy mailhog systemd unit file into place. [changed: content /etc/systemd/system/mailhog.service is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-eb77057b04033477/content
        $ sudo sh -c 'mkdir -p /etc/systemd/system && cp /tmp/reemote-install-eb77057b04033477/content /etc/systemd/system/mailhog.service && chown root:root /etc/systemd/system/mailhog.service && chmod 755 /etc/systemd/system/mailhog.service && rm -rf /tmp/reemote-install-eb77057b04033477'
    ubuntu: Ensure mailhog is enabled and will start on boot. [changed: daemon-reload; mailhog.service not running (None), will start; mailhog.service not enabled, will enable]
        $ sudo systemctl daemon-reload
        $ sudo systemctl start mailhog.service
        $ sudo systemctl enable mailhog.service"""

CONVERGED_TREE = """(root)
  wave 0
    barrier: Arch → x86_64
  wave 1
    ubuntu: Ensure mailhog install directory exists. [no change: directory /opt/mailhog already exists]
    ubuntu: Download the MailHog binary. [no change: content already up to date (/opt/mailhog/mailhog)]
    ubuntu: Download the mhsendmail binary. [no change: content already up to date (/opt/mailhog/mhsendmail)]
    ubuntu: Copy mailhog systemd unit file into place. [no change: content already up to date (/etc/systemd/system/mailhog.service)]
    ubuntu: Ensure mailhog is enabled and will start on boot. [no change: mailhog.service already running; mailhog.service already enabled]"""

DRY_PREVIEW_TREE = """(root)
  wave 0
    barrier: Arch → unknown until run"""


async def test_tree_snapshot(canned_fetch):
    """The whole run as one tree: the gate wave, then the five requests.

    The second tree is what a rerun prints -- same two waves, every line a
    no-change, no daemon-reload -- and it is the preview ``dry`` deliberately
    refuses to fake: with the architecture unread, the URL-carrying wave
    cannot be planned at all.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == FIRST_PASS_TREE
    assert [type(b).__name__ for b in barriers(first)] == ["FactRequest"]
    assert all(isinstance(b, FactRequest) for b in barriers(first))
    assert waves(first) == [0, 1]

    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE

    dry = await run_role(make_inventory(), make_hosts(make_inventory()), dry=True)
    assert dry.tree() == DRY_PREVIEW_TREE


def test_binaries_are_distinctly_canned():
    """Guard on the fixture itself: the two canned payloads differ."""
    assert FAKE_MAILHOG != FAKE_MHSENDMAIL
    assert hashlib.sha1(FAKE_MAILHOG).hexdigest() != hashlib.sha1(
        FAKE_MHSENDMAIL
    ).hexdigest()
