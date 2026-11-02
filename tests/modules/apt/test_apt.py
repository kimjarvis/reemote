"""Tests for the apt module: ``AptAutoremovable``, ``AptHeldPackages``,
``AptKeys``, ``AptPackages``, ``AptPolicy``, ``AptSources`` and
``AptUpgradable``.

Covers the read-only state discovery in ``reemote5.modules.apt.apt``:
``AptKeys`` is exercised both over the real-transport path (parsing canned
``find`` + ``gpg --with-colons`` output, reusing pyinfra's own fact fixtures
verbatim) and through the ``SimulatedHost`` seam, plus the ``gather_fact``
fan-out with its ``directories`` argument forwarding. ``AptPackages`` is
exercised the same way against pyinfra's ``apt.AptPackages`` fixtures
(``apt list --installed`` output), plus its de-dup/sort and noise-filtering
edge cases, its ``apt_packages`` seam and the ``gather_fact`` fan-out.
``AptSources`` is exercised against all eleven pyinfra ``apt.AptSources``
fixtures (legacy ``.list`` and deb822 ``.sources`` output), with the two
documented usability deviations applied -- reemote5's own file marker and
list-valued options -- plus its ``apt_sources`` seam and ``gather_fact``
fan-out. ``AptHeldPackages`` is a reemote5 original (pyinfra ships no
counterpart), so it is exercised against canned realistic ``apt-mark
showhold`` output instead of fixture parity, plus its strip/dedup/sort
edge cases, its ``apt_held_packages`` seam and the ``gather_fact`` fan-out.
``AptPolicy`` is a reemote5 original too, exercised against canned
realistic ``apt-cache policy`` output (candidate, origins, ``(none)``
values, an unknown package), plus its ``apt_policy`` seam and the
``gather_fact`` fan-out with its ``package`` argument forwarding.
``AptUpgradable`` and ``AptAutoremovable`` are reemote5 originals as
well, exercised against canned realistic ``apt list --upgradable`` and
``apt-get --just-print autoremove`` simulation output (version jumps,
epoch prefixes, ``Remv``-only parsing), plus their ``apt_upgradable`` /
``apt_autoremovable`` seams and the ``gather_fact`` fan-out.
``SimulateOperationWillChange`` is exercised against pyinfra's own
``apt.SimulateOperationWillChange`` fact fixtures (``upgrade`` dry-run
output), plus its None-on-missing-summary deviation, its
``apt_simulations`` seam and the ``gather_fact`` argument forwarding.
The operation tests cover ``apt.deb`` plus the ``apt.dist_upgrade`` /
``apt.auto_remove`` pair pyinfra's single ``apt.dist_upgrade`` is split
into: simulation-gated idempotence, the standalone autoremove, descriptor
rendering parity with pyinfra's operation fixtures, the SimulatedHost
mutations and end-to-end lockstep runs. ``apt.packages`` is covered against
pyinfra's ``apt.packages`` operation fixtures (install/remove/purge,
version pins, ``latest``, ``no_recommends``/``allow_downgrades``, extra
args, ``wait_for_lock``, the ``update``/``upgrade`` prefixes), plus its
dropped ``cache_time``/``force`` options, the SimulatedHost mutations and
end-to-end / dry-run runs. ``apt.update`` and ``apt.upgrade`` are covered
as the standalone forms of those prefixes: the stateless update (dropped
``cache_time``) and the simulation-gated upgrade (dropped ``auto_remove``,
the ``apt.auto_remove`` split), against pyinfra's ``apt.update`` /
``apt.upgrade`` operation fixtures, plus the shared SimulatedHost
mutations and end-to-end / dry-run runs. ``apt.key`` is covered as the
non-idempotent stream install it is: yield order, dest resolution, str-src
and empty-dest rejection, the unprivileged spool transfer versus the
elevated ``sh -c`` install, the SimulatedHost spool-then-install effect and
end-to-end / dry-run / determinism-guard runs. ``apt.mark`` is covered as
the reemote5 original it is (pyinfra ships no hold-state operation):
hold/unhold descriptor rendering, fact-gated idempotence in both
directions against ``AptHeldPackages``, the SimulatedHost hold/unhold
mutations and end-to-end / dry-run / hold-then-unhold runs. ``apt.repo`` is covered
against pyinfra's ``apt.repo`` operation fixtures (add, add with a
``sources.list.d`` filename, add-existing noop, remove), with its two
documented deviations -- the ``sh -c``-wrapped append and the bare
``sed -i`` removal -- plus the SimulatedHost line mutations and
end-to-end / dry-run / repeat-noop runs. ``apt.sources_file`` is covered
against pyinfra's ``apt.sources_file`` operation fixtures (create,
create-idempotent, minimal, string args, update, the two missing-field
errors, remove), with its two documented deviations -- the spool-and-install
write pair replacing the ``files.put`` delegation and the non-idempotent
``rm -f`` removal -- plus the multi-value expansion idempotence, the
SimulatedHost spool/install/remove mutations and end-to-end / dry-run /
repeat-noop / determinism runs.

``asyncio_mode = "auto"`` lets the async tests below run without decorators.
"""

import io
import json
import shlex

import pytest

from reemote5.core import FactCache, OpContext, OperationError, op
from reemote5.drivers import (
    DeterminismError,
    command_shell,
    format_log,
    format_plan,
    gather_fact,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote5.hosts import Host
from reemote5.modules import apt
from reemote5.modules.apt.apt import (
    _KEY_SPOOL_PATH,
    _SOURCES_SPOOL_PATH,
    AptAutoremovable,
    AptAutoRemoveCommand,
    AptDistUpgradeCommand,
    AptHeldPackages,
    AptInstallCommand,
    AptInstallFixCommand,
    AptKeyInstallCommand,
    AptKeys,
    AptKeyUploadCommand,
    AptMarkHoldCommand,
    AptMarkUnholdCommand,
    AptPackages,
    AptPolicy,
    AptPpaCommand,
    AptRemoveCommand,
    AptRepoAddCommand,
    AptRepoRemoveCommand,
    AptSources,
    AptSourcesFileInstallCommand,
    AptSourcesFileRemoveCommand,
    AptSourcesFileUploadCommand,
    AptUpdateCommand,
    AptUpgradable,
    AptUpgradeCommand,
    DebInstallCommand,
    DebPackage,
    SimulateOperationWillChange,
)
from tests.simulated_host import SimulatedHost


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.last_shell: str | None = None

    async def run_shell(self, shell: str) -> str:
        self.last_shell = shell
        return self._stdout


# --- Command parity (pyinfra's apt.AptKeys / gpg.GpgKeyrings fixtures) --------

# The ``command`` field of pyinfra's ``apt.AptKeys/keys.json`` fixture: the
# two find sweeps over APT's three modern keyring directories.
DEFAULT_COMMAND = (
    "find \"/etc/apt/trusted.gpg.d\" \"/etc/apt/keyrings\" \"/usr/share/keyrings\""
    " -type f \\( -name '*.gpg' -o -name '*.kbx' \\) 2>/dev/null"
    " -exec sh -c 'echo \"KEYRING:$1\";"
    " gpg --list-keys --with-colons --keyring \"$1\" --no-default-keyring 2>/dev/null || true' _ {} \\; ;"
    " find \"/etc/apt/trusted.gpg.d\" \"/etc/apt/keyrings\" \"/usr/share/keyrings\""
    " -type f -name '*.asc' 2>/dev/null"
    " -exec sh -c 'echo \"KEYRING:$1\"; gpg --with-colons \"$1\" 2>/dev/null || true' _ {} \\;"
)

# The ``command`` field of pyinfra's ``gpg.GpgKeyrings/custom_directory.json``
# fixture, whose ``arg`` is the single directory ``/custom/keyring/path``.
CUSTOM_COMMAND = (
    "find \"/custom/keyring/path\""
    " -type f \\( -name '*.gpg' -o -name '*.kbx' \\) 2>/dev/null"
    " -exec sh -c 'echo \"KEYRING:$1\";"
    " gpg --list-keys --with-colons --keyring \"$1\" --no-default-keyring 2>/dev/null || true' _ {} \\; ;"
    " find \"/custom/keyring/path\""
    " -type f -name '*.asc' 2>/dev/null"
    " -exec sh -c 'echo \"KEYRING:$1\"; gpg --with-colons \"$1\" 2>/dev/null || true' _ {} \\;"
)


def test_apt_keys_command_matches_pyinfra():
    """The default command is pyinfra's fixture command verbatim: both find
    sweeps search APT's three modern keyring directories."""
    assert AptKeys.command() == DEFAULT_COMMAND
    assert AptKeys.command(None) == DEFAULT_COMMAND


def test_apt_keys_command_accepts_custom_directories():
    """A directories list replaces the defaults in both sweeps; a single
    string is treated as one directory (pyinfra's GpgKeyrings behavior)."""
    assert AptKeys.command(["/custom/keyring/path"]) == CUSTOM_COMMAND
    assert AptKeys.command("/custom/keyring/path") == CUSTOM_COMMAND


# --- Real-transport parity (pyinfra's apt.AptKeys/keys.json fixture) ----------

# The ``output`` field of pyinfra's ``apt.AptKeys/keys.json`` fixture: three
# Ubuntu signing keys, each in its own binary keyring file.
KEYS_OUTPUT = [
    "KEYRING:/etc/apt/trusted.gpg.d/ubuntu-keyring.gpg",
    "tru:t:1:1601454628:0:3:1:5",
    "pub:-:4096:1:3B4FE6ACC0B21F32:1336770936:::-:::scSC::::::23::0:",
    "fpr:::::::::790BC7277767219C42C86F933B4FE6ACC0B21F32:",
    "uid:-::::1336770936::B7A02867A0C1D32B594B36C00E20C8C57E397748::Ubuntu Archive Automatic Signing Key (2012) <ftpmaster@ubuntu.com>::::::::::0:",
    "KEYRING:/etc/apt/trusted.gpg.d/ubuntu-keyring2.gpg",
    "tru:t:1:1601454628:0:3:1:5:",
    "pub:-:4096:1:D94AA3F0EFE21092:1336774248:::-:::scSC::::::23::0:",
    "fpr:::::::::843938DF228D22F7B3742BC0D94AA3F0EFE21092:",
    "uid:-::::1336774248::77355A0B96082B2694009775B6490C605BD16B6F::Ubuntu CD Image Automatic Signing Key (2012) <cdimage@ubuntu.com>::::::::::0:",
    "KEYRING:/etc/apt/trusted.gpg.d/ubuntu-keyring3.gpg",
    "tru:t:1:1601454628:0:3:1:5",
    "pub:-:4096:1:871920D1991BC93C:1537196506:::-:::scSC::::::23::0:",
    "fpr:::::::::F6ECB3762474EDA9D21B7022871920D1991BC93C:",
    "uid:-::::1537196506::BE438F08F546424C0EA810FD722053597EB5127B::Ubuntu Archive Automatic Signing Key (2018) <ftpmaster@ubuntu.com>::::::::::0:",
]

# The fixture's ``fact`` field -- the flattened keys. Its first entry is also
# the documented example result in pyinfra's ``apt.AptKeys`` docstring.
UBUNTU_APT_KEYS = {
    "3B4FE6ACC0B21F32": {
        "validity": "-",
        "length": 4096,
        "subkeys": {},
        "fingerprint": "790BC7277767219C42C86F933B4FE6ACC0B21F32",
        "uid_hash": "B7A02867A0C1D32B594B36C00E20C8C57E397748",
        "uid": "Ubuntu Archive Automatic Signing Key (2012) <ftpmaster@ubuntu.com>",
    },
    "D94AA3F0EFE21092": {
        "validity": "-",
        "length": 4096,
        "subkeys": {},
        "fingerprint": "843938DF228D22F7B3742BC0D94AA3F0EFE21092",
        "uid_hash": "77355A0B96082B2694009775B6490C605BD16B6F",
        "uid": "Ubuntu CD Image Automatic Signing Key (2012) <cdimage@ubuntu.com>",
    },
    "871920D1991BC93C": {
        "validity": "-",
        "length": 4096,
        "subkeys": {},
        "fingerprint": "F6ECB3762474EDA9D21B7022871920D1991BC93C",
        "uid_hash": "BE438F08F546424C0EA810FD722053597EB5127B",
        "uid": "Ubuntu Archive Automatic Signing Key (2018) <ftpmaster@ubuntu.com>",
    },
}


async def test_apt_keys_parses_the_documented_example():
    """Over the real-transport path, ``AptKeys`` flattens the three keyring
    groups into pyinfra's documented example result (the keys.json fact)."""
    host = CannedShellHost("\n".join(KEYS_OUTPUT))
    assert await AptKeys.process(host) == UBUNTU_APT_KEYS
    assert host.last_shell == DEFAULT_COMMAND


# --- Parser edge cases (pyinfra's gpg.GpgKeyrings fixtures) -------------------

# The ``output`` field of pyinfra's ``gpg.GpgKeyrings/asc_only.json`` fixture:
# two armored keyrings whose keys each carry a subkey with its own
# fingerprint (and no uid of its own).
ASC_ONLY_OUTPUT = [
    "KEYRING:/etc/apt/trusted.gpg.d/debian-archive-bookworm-automatic.asc",
    "pub:-:4096:1:B7C5D7D6350947F8:1663251644:::-:::scSC::::::23::0:",
    "fpr:::::::::4D64390375060AA4D3E84D8FB7C5D7D6350947F8:",
    "uid:-::::1663251644::7B1EFED0F7198D8488DD53F0E95BB93FC4D0A10C::Debian Stable Release Key (12/bookworm) <debian-release@lists.debian.org>::::::::::0:",
    "sub:-:4096:1:16E0B0B5FC11C3A7:1663251644:::-:::e::::::23:",
    "fpr:::::::::CFD35E5D1F5CBDE15AE59C5E16E0B0B5FC11C3A7:",
    "KEYRING:/etc/apt/trusted.gpg.d/debian-archive-bookworm-stable.asc",
    "pub:-:4096:1:4F368D5D67269BAC:1663251677:::-:::scSC::::::23::0:",
    "fpr:::::::::80E46CDDD2798E51FB74F84E4F368D5D67269BAC:",
    "uid:-::::1663251677::BB28BDBA35F9A1A92F44E4C96A9E86FC66F9D4E6::Debian Archive Automatic Signing Key (12/bookworm) <ftpmaster@debian.org>::::::::::0:",
    "sub:-:4096:1:D7AB25251C0A3EE3:1663251677:::-:::e::::::23:",
    "fpr:::::::::C5691F97B9C30A5CF0A3B1F9D7AB25251C0A3EE3:",
]

# The fixture's keys, flattened out of their per-keyring ``keys`` blocks.
ASC_ONLY_KEYS = {
    "B7C5D7D6350947F8": {
        "validity": "-",
        "length": 4096,
        "subkeys": {
            "16E0B0B5FC11C3A7": {
                "validity": "-",
                "length": 4096,
                "fingerprint": "CFD35E5D1F5CBDE15AE59C5E16E0B0B5FC11C3A7",
            }
        },
        "fingerprint": "4D64390375060AA4D3E84D8FB7C5D7D6350947F8",
        "uid_hash": "7B1EFED0F7198D8488DD53F0E95BB93FC4D0A10C",
        "uid": "Debian Stable Release Key (12/bookworm) <debian-release@lists.debian.org>",
    },
    "4F368D5D67269BAC": {
        "validity": "-",
        "length": 4096,
        "subkeys": {
            "D7AB25251C0A3EE3": {
                "validity": "-",
                "length": 4096,
                "fingerprint": "C5691F97B9C30A5CF0A3B1F9D7AB25251C0A3EE3",
            }
        },
        "fingerprint": "80E46CDDD2798E51FB74F84E4F368D5D67269BAC",
        "uid_hash": "BB28BDBA35F9A1A92F44E4C96A9E86FC66F9D4E6",
        "uid": "Debian Archive Automatic Signing Key (12/bookworm) <ftpmaster@debian.org>",
    },
}


async def test_apt_keys_parses_subkeys_from_armored_keyrings():
    """A ``sub`` record becomes a subkeys entry keyed by its own id, and the
    ``fpr`` after it attaches to the subkey, not the primary key."""
    host = CannedShellHost("\n".join(ASC_ONLY_OUTPUT))
    assert await AptKeys.process(host) == ASC_ONLY_KEYS


# The ``output`` field of pyinfra's ``gpg.GpgKeyrings/mixed_formats.json``
# fixture: one ``.gpg``, one ``.asc`` and one ``.kbx`` keyring; the latter two
# have an empty ``uid_hash`` field.
MIXED_FORMATS_OUTPUT = [
    "KEYRING:/etc/apt/trusted.gpg.d/debian-archive.gpg",
    "tru:t:1:1601454628:0:3:1:5",
    "pub:-:4096:1:73A4F27B8DD47936:1663251644:::-:::scSC::::::23::0:",
    "fpr:::::::::4D64390375060AA4D3E84D8F73A4F27B8DD47936:",
    "uid:-::::1663251644::7B1EFED0F7198D8488DD53F0E95BB93FC4D0A10C::Debian Archive Automatic Signing Key (11/bullseye) <ftpmaster@debian.org>::::::::::0:",
    "KEYRING:/etc/apt/trusted.gpg.d/ubuntu-keyring.asc",
    "pub:-:4096:1:1E9377A2BA9EF27F:1336770936:::-:::scSC::::::23::0:",
    "fpr:::::::::790BC7277767219C42C86F931E9377A2BA9EF27F:",
    "uid:-::::1336770936::::Ubuntu Archive Signing Key <ubuntu-archive@lists.ubuntu.com>::::::::::0:",
    "KEYRING:/etc/apt/trusted.gpg.d/docker.kbx",
    "tru:t:1:1601454628:0:3:1:5",
    "pub:-:4096:1:9DC858229FC7DD38:1498167007:::-:::scSC::::::23::0:",
    "fpr:::::::::9DC858229FC7DD38B3088BD89DC858229FC7DD38:",
    "uid:-::::1498167007::::Docker Release (CE deb) <docker@docker.com>::::::::::0:",
]

MIXED_FORMATS_KEYS = {
    "73A4F27B8DD47936": {
        "validity": "-",
        "length": 4096,
        "subkeys": {},
        "fingerprint": "4D64390375060AA4D3E84D8F73A4F27B8DD47936",
        "uid_hash": "7B1EFED0F7198D8488DD53F0E95BB93FC4D0A10C",
        "uid": "Debian Archive Automatic Signing Key (11/bullseye) <ftpmaster@debian.org>",
    },
    "1E9377A2BA9EF27F": {
        "validity": "-",
        "length": 4096,
        "subkeys": {},
        "fingerprint": "790BC7277767219C42C86F931E9377A2BA9EF27F",
        "uid_hash": "",
        "uid": "Ubuntu Archive Signing Key <ubuntu-archive@lists.ubuntu.com>",
    },
    "9DC858229FC7DD38": {
        "validity": "-",
        "length": 4096,
        "subkeys": {},
        "fingerprint": "9DC858229FC7DD38B3088BD89DC858229FC7DD38",
        "uid_hash": "",
        "uid": "Docker Release (CE deb) <docker@docker.com>",
    },
}


async def test_apt_keys_mixes_gpg_asc_and_kbx_formats():
    """Keys from binary (.gpg/.kbx) and armored (.asc) keyrings merge into
    one flat dict; an empty uid_hash field stays the empty string."""
    host = CannedShellHost("\n".join(MIXED_FORMATS_OUTPUT))
    assert await AptKeys.process(host) == MIXED_FORMATS_KEYS


async def test_apt_keys_empty_output_reads_as_empty_dict():
    """No keyring files found (pyinfra's empty_directory fixture) -- the find
    sweeps print nothing, so the fact reports {}."""
    host = CannedShellHost("")
    assert await AptKeys.process(host) == {}


async def test_apt_keys_duplicate_key_id_last_keyring_wins():
    """The same key id in two keyring files flattens to the last one's
    details -- pyinfra's documented last-one-wins merge order."""
    stdout = """\
KEYRING:/etc/apt/keyrings/old.gpg
pub:-:4096:1:3B4FE6ACC0B21F32:1336770936:::-:::scSC::::::23::0:
uid:-::::1336770936::OLDHASH::Old Copy <old@example.com>::::::::::0:
KEYRING:/usr/share/keyrings/new.gpg
pub:-:4096:1:3B4FE6ACC0B21F32:1336770936:::-:::scSC::::::23::0:
uid:-::::1336770936::NEWHASH::New Copy <new@example.com>::::::::::0:
"""
    host = CannedShellHost(stdout)
    keys = await AptKeys.process(host)
    assert keys["3B4FE6ACC0B21F32"]["uid"] == "New Copy <new@example.com>"
    assert keys["3B4FE6ACC0B21F32"]["uid_hash"] == "NEWHASH"
    assert len(keys) == 1


async def test_apt_keys_ignores_records_before_the_first_marker():
    """Colon records outside any keyring group (before the first KEYRING:
    line) are ignored, exactly like pyinfra's grouping parser."""
    stdout = "\n".join(
        [
            "pub:-:4096:1:0000000000000000:1:::-:::scSC::::::23::0:",
            "uid:-::::1::::Stray Key <stray@example.com>::::::::::0:",
            *KEYS_OUTPUT,
        ]
    )
    host = CannedShellHost(stdout)
    assert await AptKeys.process(host) == UBUNTU_APT_KEYS


async def test_apt_keys_marker_only_keyring_contributes_nothing():
    """An unreadable keyring emits its marker but no records (a missing gpg
    is swallowed by the command's ``|| true``); it contributes no keys."""
    stdout = "\n".join(
        [
            "KEYRING:/etc/apt/keyrings/broken.gpg",
            KEYS_OUTPUT[0],
            *KEYS_OUTPUT[1:5],
        ]
    )
    host = CannedShellHost(stdout)
    assert await AptKeys.process(host) == {
        "3B4FE6ACC0B21F32": UBUNTU_APT_KEYS["3B4FE6ACC0B21F32"]
    }
    # The marker alone -- every keyring unreadable -- reads as {}.
    assert await AptKeys.process(CannedShellHost("KEYRING:/etc/apt/keyrings/broken.gpg")) == {}


# --- SimulatedHost seam -------------------------------------------------------


async def test_apt_keys_reads_the_simulated_seam():
    """A SimulatedHost exposes ``apt_keys`` directly, bypassing the shell;
    the directories argument does not filter the already-flattened seam."""
    host = SimulatedHost(name="h", apt_keys=UBUNTU_APT_KEYS)
    assert await AptKeys.process(host) == UBUNTU_APT_KEYS
    assert await AptKeys.process(host, ["/custom/keyring/path"]) == UBUNTU_APT_KEYS
    # The default seam is {} -- a simulated machine with no keyrings found.
    assert await AptKeys.process(SimulatedHost(name="h")) == {}


async def test_apt_keys_seam_returns_copies():
    """Mutating the returned dict, a key's details or its subkeys never
    touches the simulated state."""
    host = SimulatedHost(name="h", apt_keys=ASC_ONLY_KEYS)
    keys = await AptKeys.process(host)
    keys["B7C5D7D6350947F8"]["uid"] = "mutated"
    keys["B7C5D7D6350947F8"]["subkeys"]["16E0B0B5FC11C3A7"]["length"] = 1
    keys["B7C5D7D6350947F8"]["subkeys"]["FFFFFFFFFFFFFFFF"] = {}
    del keys["4F368D5D67269BAC"]
    assert await AptKeys.process(host) == ASC_ONLY_KEYS


# --- gather_fact fan-out ------------------------------------------------------


async def test_gather_fact_apt_keys_forwards_directories():
    """gather_fact forwards the directories argument to every host's process
    and keys the results by host name in inventory order."""
    host1 = CannedShellHost("\n".join(KEYS_OUTPUT))
    host1.name = "web1"
    host2 = CannedShellHost("")
    host2.name = "web2"
    keys = await gather_fact([host1, host2], AptKeys, ["/custom/keyring/path"])
    assert keys == {"web1": UBUNTU_APT_KEYS, "web2": {}}
    assert host1.last_shell == CUSTOM_COMMAND
    assert host2.last_shell == CUSTOM_COMMAND


async def test_gather_fact_apt_keys_reads_simulated_seams():
    """Across a simulated fleet each host answers from its own seam state."""
    host1 = SimulatedHost(name="web1", apt_keys=UBUNTU_APT_KEYS)
    host2 = SimulatedHost(name="web2")
    keys = await gather_fact([host1, host2], AptKeys)
    assert keys == {"web1": UBUNTU_APT_KEYS, "web2": {}}


# --- AptPackages: command parity (pyinfra's apt.AptPackages fixtures) ---------


def test_apt_packages_command_matches_pyinfra():
    """The command is pyinfra's fixture command verbatim: apt list with the
    stderr progress notice suppressed."""
    assert AptPackages.COMMAND == "apt list --installed 2>/dev/null"


# --- Real-transport parity (pyinfra's apt.AptPackages/packages.yaml fixture) --

# The ``output`` field of pyinfra's ``apt.AptPackages/packages.yaml`` fixture:
# the Listing... header plus five installed packages, one with an
# architecture suffix (libc6:i386) and two with epoch-prefixed versions.
PACKAGES_OUTPUT = [
    "Listing...",
    "adduser/jammy,jammy,now 3.118ubuntu5 all [installed,automatic]",
    "docker-ce/jammy,now 5:24.0.7-1~ubuntu.22.04~jammy amd64 [installed]",
    "htop/jammy 3.0.5-7build2 amd64 [installed,automatic]",
    "libc6:i386/jammy,now 2.35-0ubuntu3.4 i386 [installed,automatic]",
    "zabbix-agent2/jammy,now 1:6.4.7-1+ubuntu22.04 amd64 [installed,automatic]",
]

# The fixture's ``fact`` field -- the documented list-of-versions shape.
UBUNTU_APT_PACKAGES = {
    "adduser": ["3.118ubuntu5"],
    "docker-ce": ["5:24.0.7-1~ubuntu.22.04~jammy"],
    "htop": ["3.0.5-7build2"],
    "libc6:i386": ["2.35-0ubuntu3.4"],
    "zabbix-agent2": ["1:6.4.7-1+ubuntu22.04"],
}


async def test_apt_packages_parses_the_fixture():
    """Over the real-transport path, ``AptPackages`` parses the fixture's
    rows into pyinfra's fixture fact, keeping the :i386 suffix and the
    epoch-prefixed versions intact."""
    host = CannedShellHost("\n".join(PACKAGES_OUTPUT))
    assert await AptPackages.process(host) == UBUNTU_APT_PACKAGES
    assert host.last_shell == AptPackages.COMMAND


async def test_apt_packages_listing_only_reads_as_empty_dict():
    """Only the Listing... header (pyinfra's empty.yaml fixture) -- no
    package rows match, so the fact reports {}."""
    host = CannedShellHost("Listing...\n")
    assert await AptPackages.process(host) == {}
    # Completely empty stdout reads as {} too (pyinfra's default = dict).
    assert await AptPackages.process(CannedShellHost("")) == {}


# --- Parser edge cases ---------------------------------------------------------


async def test_apt_packages_ignores_noise_and_non_installed_rows():
    """Blank lines, warning noise and rows not marked ``[installed`` (an
    upgradable-only listing) never match the anchored regex."""
    stdout = "\n".join(
        [
            "Listing...",
            "",
            "WARNING: apt does not have a stable CLI interface.",
            "htop/jammy 3.0.5-7build2 amd64 [upgradable from: 3.0.4-1]",
            *PACKAGES_OUTPUT[1:],
        ]
    )
    host = CannedShellHost(stdout)
    assert await AptPackages.process(host) == UBUNTU_APT_PACKAGES


async def test_apt_packages_dedups_and_sorts_versions():
    """The same name listed twice contributes one sorted, de-duplicated
    version list -- pyinfra's parse_packages set semantics, returned as
    the documented sorted list."""
    stdout = """\
Listing...
htop/jammy,now 3.0.5-7build2 amd64 [installed,automatic]
htop/jammy-security,now 3.0.5-7build2 amd64 [installed]
htop/jammy,now 3.0.4-1 amd64 [installed,automatic]
"""
    host = CannedShellHost(stdout)
    assert await AptPackages.process(host) == {"htop": ["3.0.4-1", "3.0.5-7build2"]}


# --- SimulatedHost seam -------------------------------------------------------


async def test_apt_packages_reads_the_simulated_seam():
    """A SimulatedHost exposes ``apt_packages`` directly, bypassing the
    shell; the default seam is {} -- no installed packages listed."""
    host = SimulatedHost(name="h", apt_packages=UBUNTU_APT_PACKAGES)
    assert await AptPackages.process(host) == UBUNTU_APT_PACKAGES
    assert await AptPackages.process(SimulatedHost(name="h")) == {}


async def test_apt_packages_seam_returns_copies():
    """Mutating the returned dict or a version list never touches the
    simulated state."""
    host = SimulatedHost(name="h", apt_packages=UBUNTU_APT_PACKAGES)
    packages = await AptPackages.process(host)
    packages["htop"].append("9.9")
    packages["stray"] = ["1.0"]
    del packages["adduser"]
    assert await AptPackages.process(host) == UBUNTU_APT_PACKAGES


# --- gather_fact fan-out ------------------------------------------------------


async def test_gather_fact_apt_packages_reads_simulated_seams():
    """Across a simulated fleet each host answers from its own seam state,
    keyed by host name in inventory order."""
    host1 = SimulatedHost(name="web1", apt_packages=UBUNTU_APT_PACKAGES)
    host2 = SimulatedHost(name="web2")
    packages = await gather_fact([host1, host2], AptPackages)
    assert packages == {"web1": UBUNTU_APT_PACKAGES, "web2": {}}


async def test_gather_fact_apt_packages_over_canned_shells():
    """The fan-out drives every host's real-transport path with the same
    COMMAND and keys the parsed results by host name."""
    host1 = CannedShellHost("\n".join(PACKAGES_OUTPUT))
    host1.name = "web1"
    host2 = CannedShellHost("Listing...\n")
    host2.name = "web2"
    packages = await gather_fact([host1, host2], AptPackages)
    assert packages == {"web1": UBUNTU_APT_PACKAGES, "web2": {}}
    assert host1.last_shell == AptPackages.COMMAND
    assert host2.last_shell == AptPackages.COMMAND


# --- AptSources: command parity (pyinfra's apt.AptSources fixtures) -----------

# pyinfra's fixture command cats the same three globs behind its
# ``##PYINFRA_FILE`` marker inside an ``sh -c`` wrapper; reemote5 renames
# the marker to its own ``#reemote5-aptsource`` (like DnfRepositories'
# filename marker) and drops the wrapper -- run_shell already provides a
# shell.
SOURCES_COMMAND = (
    "for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list"
    " /etc/apt/sources.list.d/*.sources; do "
    '[ -e "$f" ] || continue; printf "#reemote5-aptsource %s\\n" "$f"; cat "$f"; echo; '
    "done"
)


def test_apt_sources_command_cats_the_sources_files():
    """The command cats the legacy sources file and both .list/.sources
    globs, each behind reemote5's own file marker line."""
    assert AptSources.COMMAND == SOURCES_COMMAND


def _sources_stdout(output: list[str]) -> str:
    """Join a pyinfra fixture's output lines, renaming its file marker.

    The fixture lines are otherwise verbatim; only the ``##PYINFRA_FILE``
    prefix becomes reemote5's own FILE_MARKER (the documented rename).
    """
    return "\n".join(
        line.replace("##PYINFRA_FILE ", f"{AptSources.FILE_MARKER} ", 1)
        if line.startswith("##PYINFRA_FILE ")
        else line
        for line in output
    )


def _list_valued(fact: list[dict]) -> list[dict]:
    """A pyinfra fixture fact with every option value wrapped into a list.

    pyinfra collapses single-valued options to a bare string; reemote5's
    documented usability deviation keeps every value a list, so fixture
    parity compares against this normalization.
    """
    return [
        {
            **repo,
            "options": {
                key: value if isinstance(value, list) else [value]
                for key, value in repo["options"].items()
            },
        }
        for repo in fact
    ]


# --- Real-transport parity (pyinfra's apt.AptSources/sources.json fixture) ----

# The ``output`` field of pyinfra's ``apt.AptSources/sources.json`` fixture:
# two legacy files, comma-split arch options, a signed-by keyring and a
# stray "nope" line the regex must ignore.
SOURCES_OUTPUT = [
    "##PYINFRA_FILE /etc/apt/sources.list",
    "deb http://archive.ubuntu.com/ubuntu trusty restricted",
    "##PYINFRA_FILE /etc/apt/sources.list.d/mongodb.list",
    "deb-src [arch=amd64,i386] http://archive.ubuntu.com/ubuntu trusty main",
    "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse",
    "nope",
]

# The fixture's ``fact`` field -- pyinfra's AptRepo entries as plain dicts.
SOURCES_FACT = [
    {
        "url": "http://archive.ubuntu.com/ubuntu",
        "distribution": "trusty",
        "type": "deb",
        "components": ["restricted"],
        "options": {},
    },
    {
        "url": "http://archive.ubuntu.com/ubuntu",
        "distribution": "trusty",
        "type": "deb-src",
        "components": ["main"],
        "options": {"arch": ["amd64", "i386"]},
    },
    {
        "url": "https://repo.mongodb.org/apt/ubuntu",
        "distribution": "jammy/mongodb-org/7.0",
        "type": "deb",
        "components": ["multiverse"],
        "options": {
            "arch": ["amd64", "arm64"],
            "signed-by": "/usr/share/keyrings/mongodb-server-7.0.gpg",
        },
    },
]


async def test_apt_sources_parses_the_legacy_fixture():
    """Over the real-transport path, ``AptSources`` parses both legacy files
    into the fixture fact (with list-valued options), ignoring the stray
    line; the signed-by scalar wraps into a one-entry list."""
    host = CannedShellHost(_sources_stdout(SOURCES_OUTPUT))
    assert await AptSources.process(host) == _list_valued(SOURCES_FACT)
    assert host.last_shell == SOURCES_COMMAND


# --- deb822 parity (pyinfra's apt.AptSources/deb822_sources.json fixture) -----

# The ``output`` field of pyinfra's ``deb822_sources.json`` fixture: one
# file, two stanzas -- a deb+deb-src Debian stanza with Architectures and
# Signed-By, and a plain VS Code stanza.
DEB822_OUTPUT = [
    "##PYINFRA_FILE /etc/apt/sources.list.d/test.sources",
    "Types: deb deb-src",
    "URIs: http://deb.debian.org/debian",
    "Suites: bookworm",
    "Components: main contrib",
    "Architectures: amd64",
    "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg",
    "",
    "Types: deb",
    "URIs: https://packages.microsoft.com/repos/code",
    "Suites: stable",
    "Components: main",
    "",
]

DEB822_FACT = [
    {
        "type": "deb",
        "url": "http://deb.debian.org/debian",
        "distribution": "bookworm",
        "components": ["main", "contrib"],
        "options": {
            "arch": "amd64",
            "signed-by": "/usr/share/keyrings/debian-archive-keyring.gpg",
        },
    },
    {
        "type": "deb-src",
        "url": "http://deb.debian.org/debian",
        "distribution": "bookworm",
        "components": ["main", "contrib"],
        "options": {
            "arch": "amd64",
            "signed-by": "/usr/share/keyrings/debian-archive-keyring.gpg",
        },
    },
    {
        "type": "deb",
        "url": "https://packages.microsoft.com/repos/code",
        "distribution": "stable",
        "components": ["main"],
        "options": {},
    },
]


async def test_apt_sources_parses_deb822_stanzas():
    """Blank-line-separated stanzas each expand: the Types: deb deb-src
    stanza yields one entry per type with its arch/signed-by options
    (now one-entry lists), the second stanza a plain entry."""
    host = CannedShellHost(_sources_stdout(DEB822_OUTPUT))
    assert await AptSources.process(host) == _list_valued(DEB822_FACT)


async def test_apt_sources_expands_multi_uris_and_suites():
    """pyinfra's ``deb822_multi_uris_suites.json`` fixture: two URIs and two
    Suites expand into the four type-then-uri-then-suite combinations."""
    output = [
        "##PYINFRA_FILE /etc/apt/sources.list.d/multi.sources",
        "Types: deb",
        "URIs: http://deb.debian.org/debian https://mirror.example.com/debian",
        "Suites: bookworm bookworm-updates",
        "Components: main contrib",
        "",
    ]
    fact = [
        {"type": "deb", "url": "http://deb.debian.org/debian", "distribution": "bookworm", "components": ["main", "contrib"], "options": {}},
        {"type": "deb", "url": "http://deb.debian.org/debian", "distribution": "bookworm-updates", "components": ["main", "contrib"], "options": {}},
        {"type": "deb", "url": "https://mirror.example.com/debian", "distribution": "bookworm", "components": ["main", "contrib"], "options": {}},
        {"type": "deb", "url": "https://mirror.example.com/debian", "distribution": "bookworm-updates", "components": ["main", "contrib"], "options": {}},
    ]
    host = CannedShellHost(_sources_stdout(output))
    assert await AptSources.process(host) == fact


async def test_apt_sources_keeps_inline_gpg_key_whole():
    """pyinfra's ``deb822_inline_gpg_key.json`` fixture: an inline armored
    Signed-By block folds its continuation lines into one signed-by entry
    holding the whole key text."""
    output = [
        "##PYINFRA_FILE /etc/apt/sources.list.d/inline-key.sources",
        "Types: deb",
        "URIs: http://deb.debian.org/debian",
        "Suites: trixie",
        "Components: main",
        "Signed-By:",
        " -----BEGIN PGP PUBLIC KEY BLOCK-----",
        " mQINBFABCDEFGH==",
        " -----END PGP PUBLIC KEY BLOCK-----",
        "",
    ]
    host = CannedShellHost(_sources_stdout(output))
    assert await AptSources.process(host) == [
        {
            "type": "deb",
            "url": "http://deb.debian.org/debian",
            "distribution": "trixie",
            "components": ["main"],
            "options": {
                "signed-by": [
                    "-----BEGIN PGP PUBLIC KEY BLOCK-----\nmQINBFABCDEFGH==\n-----END PGP PUBLIC KEY BLOCK-----"
                ]
            },
        }
    ]


# --- Parser edge cases (the remaining pyinfra apt.AptSources fixtures) --------


@pytest.mark.parametrize(
    ("output", "fact"),
    [
        pytest.param(
            [
                "##PYINFRA_FILE /etc/apt/sources.list",
                "deb http://archive.ubuntu.com/ubuntu trusty restricted pi4",
                "",
            ],
            [
                {
                    "url": "http://archive.ubuntu.com/ubuntu",
                    "distribution": "trusty",
                    "type": "deb",
                    "components": ["restricted", "pi4"],
                    "options": {},
                }
            ],
            id="component_with_number",
        ),
        pytest.param(
            [
                "##PYINFRA_FILE /etc/apt/sources.list.d/nvidia-container-toolkit.list",
                "deb [signed-by=/etc/apt/keyrings/nvidia-container-toolkit-keyring.gpg] https://nvidia.github.io/libnvidia-container/stable/ubuntu18.04/amd64 /",
                "#deb [signed-by=/etc/apt/keyrings/nvidia-container-toolkit-keyring.gpg] https://nvidia.github.io/libnvidia-container/experimental/ubuntu18.04/amd64 /",
            ],
            [
                {
                    "url": "https://nvidia.github.io/libnvidia-container/stable/ubuntu18.04/amd64",
                    "distribution": "/",
                    "type": "deb",
                    "components": [],
                    "options": {
                        "signed-by": "/etc/apt/keyrings/nvidia-container-toolkit-keyring.gpg"
                    },
                }
            ],
            id="flat_repo_signed_by",
        ),
        pytest.param(
            [
                "##PYINFRA_FILE /etc/apt/sources.list.d/with-comment.sources",
                "Types: deb",
                "# This is an inline comment, should be ignored",
                "URIs: http://deb.debian.org/debian",
                "Suites: bookworm",
                "Components: main",
                "",
            ],
            [
                {
                    "type": "deb",
                    "url": "http://deb.debian.org/debian",
                    "distribution": "bookworm",
                    "components": ["main"],
                    "options": {},
                }
            ],
            id="deb822_comment_in_stanza",
        ),
        pytest.param(
            [
                "##PYINFRA_FILE /etc/apt/sources.list.d/disabled.sources",
                "Types: deb",
                "URIs: http://deb.debian.org/debian",
                "Suites: bookworm",
                "Components: main",
                "Enabled: no",
                "",
            ],
            [],
            id="deb822_enabled_no",
        ),
        pytest.param(
            [
                "##PYINFRA_FILE /etc/apt/sources.list.d/bad.sources",
                "Types: deb",
                "URIs: http://deb.debian.org/debian",
                "",
            ],
            [],
            id="deb822_malformed",
        ),
        pytest.param(
            [
                "##PYINFRA_FILE /etc/apt/sources.list.d/multipath.sources",
                "Types: deb",
                "URIs: http://deb.debian.org/debian",
                "Suites: bookworm",
                "Components: main",
                "Signed-By: /usr/share/keyrings/key1.gpg",
                " /usr/share/keyrings/key2.gpg",
                "",
            ],
            [
                {
                    "type": "deb",
                    "url": "http://deb.debian.org/debian",
                    "distribution": "bookworm",
                    "components": ["main"],
                    "options": {
                        "signed-by": [
                            "/usr/share/keyrings/key1.gpg",
                            "/usr/share/keyrings/key2.gpg",
                        ]
                    },
                }
            ],
            id="deb822_signed_by_multiline_paths",
        ),
        pytest.param(
            [
                "##PYINFRA_FILE /etc/apt/sources.list.d/trusted.sources",
                "Types: deb",
                "URIs: http://internal.example.com/debian",
                "Suites: stable",
                "Components: main",
                "Trusted: yes",
                "",
            ],
            [
                {
                    "type": "deb",
                    "url": "http://internal.example.com/debian",
                    "distribution": "stable",
                    "components": ["main"],
                    "options": {"trusted": "yes"},
                }
            ],
            id="deb822_trusted",
        ),
    ],
)
async def test_apt_sources_fixture_edge_cases(output: list[str], fact: list[dict]):
    """Each remaining pyinfra fixture parses to its fact (options wrapped
    into lists): digit-bearing components, flat /-suite repos, inline
    comments, disabled and malformed stanzas, multi-path Signed-By
    continuation lines and Trusted: yes."""
    host = CannedShellHost(_sources_stdout(output))
    assert await AptSources.process(host) == _list_valued(fact)


async def test_apt_sources_entries_are_plain_json_printable_dicts():
    """The usability deviation: a single-valued option stays a one-entry
    list (no str-or-list mix), and each entry is a plain dict that
    json.dumps serializes directly -- no AptRepo hybrid."""
    stdout = "\n".join(
        [
            f"{AptSources.FILE_MARKER} /etc/apt/sources.list.d/example.list",
            "deb [arch=amd64 signed-by=/usr/share/keyrings/example.gpg] https://example.com/apt stable main",
        ]
    )
    host = CannedShellHost(stdout)
    sources = await AptSources.process(host)
    assert sources == [
        {
            "type": "deb",
            "url": "https://example.com/apt",
            "distribution": "stable",
            "components": ["main"],
            "options": {"arch": ["amd64"], "signed-by": ["/usr/share/keyrings/example.gpg"]},
        }
    ]
    assert isinstance(sources[0], dict)
    assert json.loads(json.dumps(sources)) == sources


async def test_apt_sources_empty_output_reads_as_empty_list():
    """No sources files at all reads as [] (pyinfra's default = list); a
    marker with no content (an empty file) contributes nothing."""
    assert await AptSources.process(CannedShellHost("")) == []
    marker_only = f"{AptSources.FILE_MARKER} /etc/apt/sources.list"
    assert await AptSources.process(CannedShellHost(marker_only)) == []


async def test_apt_sources_ignores_lines_before_the_first_marker():
    """Lines outside any file group (before the first marker) are ignored,
    exactly like pyinfra's grouping parser."""
    stdout = _sources_stdout(
        ["deb http://stray.example.com/ubuntu noble main", *SOURCES_OUTPUT]
    )
    host = CannedShellHost(stdout)
    assert await AptSources.process(host) == _list_valued(SOURCES_FACT)


# --- SimulatedHost seam -------------------------------------------------------


async def test_apt_sources_reads_the_simulated_seam():
    """A SimulatedHost exposes ``apt_sources`` directly, bypassing the
    shell; the default seam is [] -- no sources files configured."""
    sources = _list_valued(SOURCES_FACT)
    host = SimulatedHost(name="h", apt_sources=sources)
    assert await AptSources.process(host) == sources
    assert await AptSources.process(SimulatedHost(name="h")) == []


async def test_apt_sources_seam_returns_copies():
    """Mutating an entry, its components, an option list or the options
    dict never touches the simulated state."""
    sources = _list_valued(SOURCES_FACT)
    host = SimulatedHost(name="h", apt_sources=sources)
    result = await AptSources.process(host)
    result[1]["components"].append("universe")
    result[1]["options"]["arch"].append("arm64")
    result[1]["options"]["trusted"] = ["yes"]
    result[2]["url"] = "mutated"
    del result[0]
    assert await AptSources.process(host) == sources


# --- gather_fact fan-out ------------------------------------------------------


async def test_gather_fact_apt_sources_over_canned_shells():
    """The fan-out drives every host's real-transport path with the same
    COMMAND and keys the parsed results by host name in inventory order."""
    host1 = CannedShellHost(_sources_stdout(SOURCES_OUTPUT))
    host1.name = "web1"
    host2 = CannedShellHost("")
    host2.name = "web2"
    sources = await gather_fact([host1, host2], AptSources)
    assert sources == {"web1": _list_valued(SOURCES_FACT), "web2": []}
    assert host1.last_shell == SOURCES_COMMAND
    assert host2.last_shell == SOURCES_COMMAND


async def test_gather_fact_apt_sources_reads_simulated_seams():
    """Across a simulated fleet each host answers from its own seam state."""
    sources = _list_valued(SOURCES_FACT)
    host1 = SimulatedHost(name="web1", apt_sources=sources)
    host2 = SimulatedHost(name="web2")
    assert await gather_fact([host1, host2], AptSources) == {"web1": sources, "web2": []}


# --- AptHeldPackages: command (a reemote5 original, no pyinfra fixture) -------


def test_apt_held_packages_command_runs_apt_mark_showhold():
    """The command is the plain hold listing: apt-mark showhold, one held
    package name per line."""
    assert AptHeldPackages.COMMAND == "apt-mark showhold"


# --- Real transport (canned realistic apt-mark showhold output) ---------------

# Realistic ``apt-mark showhold`` output: four held packages in arbitrary
# dpkg order, one with an architecture suffix. pyinfra ships no fixture
# for this fact -- it is a reemote5 original.
HELD_OUTPUT = [
    "linux-image-generic",
    "docker-ce",
    "libc6:i386",
    "linux-headers-generic",
]

# The parsed answer: de-duplicated, sorted names, the :i386 suffix intact.
HELD_PACKAGES = [
    "docker-ce",
    "libc6:i386",
    "linux-headers-generic",
    "linux-image-generic",
]


async def test_apt_held_packages_parses_realistic_output():
    """Over the real-transport path, ``AptHeldPackages`` parses the showhold
    lines into the sorted name list, keeping the :i386 suffix intact."""
    host = CannedShellHost("\n".join(HELD_OUTPUT))
    assert await AptHeldPackages.process(host) == HELD_PACKAGES
    assert host.last_shell == AptHeldPackages.COMMAND


async def test_apt_held_packages_empty_output_reads_as_empty_list():
    """Nothing held reads as [] -- apt-mark showhold prints no output at
    all when no package is marked hold."""
    assert await AptHeldPackages.process(CannedShellHost("")) == []
    assert await AptHeldPackages.process(CannedShellHost("\n\n")) == []


# --- Parser edge cases ---------------------------------------------------------


async def test_apt_held_packages_strips_blank_lines_and_dedups():
    """Surrounding whitespace is stripped, blank lines are skipped and a
    name listed twice contributes one sorted entry."""
    stdout = """\
  linux-image-generic  

docker-ce
linux-image-generic
\t
libc6:i386
linux-headers-generic
"""
    host = CannedShellHost(stdout)
    assert await AptHeldPackages.process(host) == HELD_PACKAGES


# --- SimulatedHost seam -------------------------------------------------------


async def test_apt_held_packages_reads_the_simulated_seam():
    """A SimulatedHost exposes ``apt_held_packages`` directly, bypassing the
    shell; the default seam is [] -- nothing marked hold."""
    host = SimulatedHost(name="h", apt_held_packages=HELD_PACKAGES)
    assert await AptHeldPackages.process(host) == HELD_PACKAGES
    assert await AptHeldPackages.process(SimulatedHost(name="h")) == []


async def test_apt_held_packages_seam_returns_copies():
    """Mutating the returned list never touches the simulated state."""
    host = SimulatedHost(name="h", apt_held_packages=HELD_PACKAGES)
    held = await AptHeldPackages.process(host)
    held.append("stray")
    held.remove("docker-ce")
    assert await AptHeldPackages.process(host) == HELD_PACKAGES


# --- gather_fact fan-out ------------------------------------------------------


async def test_gather_fact_apt_held_packages_reads_simulated_seams():
    """Across a simulated fleet each host answers from its own seam state,
    keyed by host name in inventory order."""
    host1 = SimulatedHost(name="web1", apt_held_packages=HELD_PACKAGES)
    host2 = SimulatedHost(name="web2")
    held = await gather_fact([host1, host2], AptHeldPackages)
    assert held == {"web1": HELD_PACKAGES, "web2": []}


async def test_gather_fact_apt_held_packages_over_canned_shells():
    """The fan-out drives every host's real-transport path with the same
    COMMAND and keys the parsed results by host name."""
    host1 = CannedShellHost("\n".join(HELD_OUTPUT))
    host1.name = "web1"
    host2 = CannedShellHost("")
    host2.name = "web2"
    held = await gather_fact([host1, host2], AptHeldPackages)
    assert held == {"web1": HELD_PACKAGES, "web2": []}
    assert host1.last_shell == AptHeldPackages.COMMAND
    assert host2.last_shell == AptHeldPackages.COMMAND


# --- AptPolicy: command (a reemote5 original, no pyinfra fixture) --------------


def test_apt_policy_command_quotes_the_package():
    """The command runs apt-cache policy under LANG=C (untranslated field
    labels) with the package name shlex-quoted."""
    assert AptPolicy.command("nginx") == "LANG=C apt-cache policy nginx"
    assert (
        AptPolicy.command("strange; rm -rf /")
        == "LANG=C apt-cache policy 'strange; rm -rf /'"
    )


# --- Real transport (canned realistic apt-cache policy output) ----------------

# Realistic ``apt-cache policy nginx`` output on Ubuntu noble: an upgrade is
# available from noble-updates, the installed version came from
# noble-security (the *** row, also in the local dpkg status), and the base
# noble version is still offered. pyinfra ships no fixture for this fact --
# it is a reemote5 original.
NGINX_POLICY_OUTPUT = """\
nginx:
  Installed: 1.24.0-2ubuntu7.1
  Candidate: 1.24.0-2ubuntu7.3
  Version table:
     1.24.0-2ubuntu7.3 500
        500 http://archive.ubuntu.com/ubuntu noble-updates/main amd64 Packages
 *** 1.24.0-2ubuntu7.1 500
        500 http://security.ubuntu.com/ubuntu noble-security/main amd64 Packages
        100 /var/lib/dpkg/status
     1.24.0-2ubuntu7 500
        500 http://archive.ubuntu.com/ubuntu noble/main amd64 Packages
"""

# The parsed answer: the *** marker dropped, origins stripped of their
# leading priority, rows in apt's printed order.
NGINX_POLICY = {
    "installed": "1.24.0-2ubuntu7.1",
    "candidate": "1.24.0-2ubuntu7.3",
    "versions": [
        {
            "version": "1.24.0-2ubuntu7.3",
            "priority": 500,
            "origins": [
                "http://archive.ubuntu.com/ubuntu noble-updates/main amd64 Packages",
            ],
        },
        {
            "version": "1.24.0-2ubuntu7.1",
            "priority": 500,
            "origins": [
                "http://security.ubuntu.com/ubuntu noble-security/main amd64 Packages",
                "/var/lib/dpkg/status",
            ],
        },
        {
            "version": "1.24.0-2ubuntu7",
            "priority": 500,
            "origins": [
                "http://archive.ubuntu.com/ubuntu noble/main amd64 Packages",
            ],
        },
    ],
}


async def test_apt_policy_parses_realistic_output():
    """Over the real-transport path, ``AptPolicy`` parses the installed and
    candidate versions plus the full version table with its origins."""
    host = CannedShellHost(NGINX_POLICY_OUTPUT)
    assert await AptPolicy.process(host, "nginx") == NGINX_POLICY
    assert host.last_shell == "LANG=C apt-cache policy nginx"


async def test_apt_policy_result_is_json_printable():
    """The answer is a plain dict of scalars, lists and dicts -- json.dumps
    round-trips it directly, like every gather_fact example needs."""
    host = CannedShellHost(NGINX_POLICY_OUTPUT)
    policy = await AptPolicy.process(host, "nginx")
    assert isinstance(policy, dict)
    assert json.loads(json.dumps(policy)) == NGINX_POLICY


# --- Parser edge cases ---------------------------------------------------------


async def test_apt_policy_not_installed_reads_none_installed():
    """A known but uninstalled package prints ``Installed: (none)`` -- that
    maps to None, the candidate stays, and no *** row or dpkg status origin
    appears."""
    stdout = """\
htop:
  Installed: (none)
  Candidate: 3.2.2-2
  Version table:
     3.2.2-2 500
        500 http://archive.ubuntu.com/ubuntu noble/main amd64 Packages
"""
    host = CannedShellHost(stdout)
    assert await AptPolicy.process(host, "htop") == {
        "installed": None,
        "candidate": "3.2.2-2",
        "versions": [
            {
                "version": "3.2.2-2",
                "priority": 500,
                "origins": [
                    "http://archive.ubuntu.com/ubuntu noble/main amd64 Packages",
                ],
            },
        ],
    }


async def test_apt_policy_unknown_package_reads_as_none():
    """An unknown package prints nothing on stdout (apt's "Unable to locate
    package" notice goes to stderr) -- the fact reports None."""
    assert await AptPolicy.process(CannedShellHost(""), "no-such-package") is None


async def test_apt_policy_pinned_out_candidate_reads_none():
    """A package pinned below zero prints ``Candidate: (none)`` with a
    negative-priority version table -- the candidate maps to None and the
    negative priority parses as an int."""
    stdout = """\
docker-ce:
  Installed: (none)
  Candidate: (none)
  Version table:
     5:24.0.7-1~ubuntu.22.04~jammy -1
        500 https://download.docker.com/linux/ubuntu jammy/stable amd64 Packages
"""
    host = CannedShellHost(stdout)
    assert await AptPolicy.process(host, "docker-ce") == {
        "installed": None,
        "candidate": None,
        "versions": [
            {
                "version": "5:24.0.7-1~ubuntu.22.04~jammy",
                "priority": -1,
                "origins": [
                    "https://download.docker.com/linux/ubuntu jammy/stable amd64 Packages",
                ],
            },
        ],
    }


async def test_apt_policy_installed_only_keeps_epoch_and_dpkg_status():
    """A locally installed package no repository offers still lists its
    dpkg status row: the epoch-prefixed version stays intact and the
    installed version is also the candidate."""
    stdout = """\
legacy-tool:
  Installed: 2:1.0-3
  Candidate: 2:1.0-3
  Version table:
 *** 2:1.0-3 100
        100 /var/lib/dpkg/status
"""
    host = CannedShellHost(stdout)
    assert await AptPolicy.process(host, "legacy-tool") == {
        "installed": "2:1.0-3",
        "candidate": "2:1.0-3",
        "versions": [
            {"version": "2:1.0-3", "priority": 100, "origins": ["/var/lib/dpkg/status"]},
        ],
    }


# --- SimulatedHost seam -------------------------------------------------------


async def test_apt_policy_reads_the_simulated_seam():
    """A SimulatedHost exposes ``apt_policy`` keyed by package name,
    bypassing the shell; an absent name (and the default {} seam) reads as
    None, like an unknown package."""
    host = SimulatedHost(name="h", apt_policy={"nginx": NGINX_POLICY})
    assert await AptPolicy.process(host, "nginx") == NGINX_POLICY
    assert await AptPolicy.process(host, "no-such-package") is None
    assert await AptPolicy.process(SimulatedHost(name="h"), "nginx") is None


async def test_apt_policy_seam_returns_copies():
    """Mutating the returned dict, a version entry or its origins list never
    touches the simulated state."""
    host = SimulatedHost(name="h", apt_policy={"nginx": NGINX_POLICY})
    policy = await AptPolicy.process(host, "nginx")
    policy["candidate"] = "mutated"
    policy["versions"][1]["origins"].append("http://stray.example.com")
    policy["versions"][0]["priority"] = 1
    del policy["versions"][2]
    assert await AptPolicy.process(host, "nginx") == NGINX_POLICY


# --- gather_fact fan-out ------------------------------------------------------


async def test_gather_fact_apt_policy_forwards_package():
    """gather_fact forwards the package argument to every host's process and
    keys the results by host name in inventory order."""
    host1 = CannedShellHost(NGINX_POLICY_OUTPUT)
    host1.name = "web1"
    host2 = CannedShellHost("")
    host2.name = "web2"
    policies = await gather_fact([host1, host2], AptPolicy, "nginx")
    assert policies == {"web1": NGINX_POLICY, "web2": None}
    assert host1.last_shell == "LANG=C apt-cache policy nginx"
    assert host2.last_shell == "LANG=C apt-cache policy nginx"


async def test_gather_fact_apt_policy_reads_simulated_seams():
    """Across a simulated fleet each host answers from its own seam state."""
    host1 = SimulatedHost(name="web1", apt_policy={"nginx": NGINX_POLICY})
    host2 = SimulatedHost(name="web2")
    assert await gather_fact([host1, host2], AptPolicy, "nginx") == {
        "web1": NGINX_POLICY,
        "web2": None,
    }


# --- AptUpgradable: command (a reemote5 original, no pyinfra fixture) ----------


def test_apt_upgradable_command_lists_upgradable():
    """The command lists the upgradable rows with the same stderr redirect
    as AptPackages: apt list --upgradable 2>/dev/null."""
    assert AptUpgradable.COMMAND == "apt list --upgradable 2>/dev/null"


# --- Real transport (canned realistic apt list --upgradable output) -----------

# Realistic ``apt list --upgradable`` output on Ubuntu noble: the
# ``Listing... Done`` header, a comma-separated multi-suite source-info,
# an epoch-prefixed version jump and an architecture-suffixed name.
# pyinfra ships no fixture for this fact -- it is a reemote5 original.
UPGRADABLE_OUTPUT = """\
Listing... Done
libssl3t64/noble-updates 3.0.13-0ubuntu3.5 amd64 [upgradable from: 3.0.13-0ubuntu3.4]
vim/noble-updates,noble-security 2:9.1.0016-1ubuntu7.6 amd64 [upgradable from: 2:9.1.0016-1ubuntu7.4]
libc6:i386/noble-updates 2.39-0ubuntu8.4 i386 [upgradable from: 2.39-0ubuntu8.3]
"""

# The parsed answer: name -> {installed, candidate}, the :i386 suffix and
# the epoch prefixes intact, suite and architecture columns dropped.
UPGRADABLE_PACKAGES = {
    "libssl3t64": {
        "installed": "3.0.13-0ubuntu3.4",
        "candidate": "3.0.13-0ubuntu3.5",
    },
    "vim": {
        "installed": "2:9.1.0016-1ubuntu7.4",
        "candidate": "2:9.1.0016-1ubuntu7.6",
    },
    "libc6:i386": {
        "installed": "2.39-0ubuntu8.3",
        "candidate": "2.39-0ubuntu8.4",
    },
}


async def test_apt_upgradable_parses_realistic_output():
    """Over the real-transport path, ``AptUpgradable`` parses each row into
    its installed -> candidate version jump, epochs and :i386 intact."""
    host = CannedShellHost(UPGRADABLE_OUTPUT)
    assert await AptUpgradable.process(host) == UPGRADABLE_PACKAGES
    assert host.last_shell == AptUpgradable.COMMAND


async def test_apt_upgradable_result_is_json_printable():
    """The answer is a plain dict of string-valued dicts -- json.dumps
    round-trips it directly, like every gather_fact example needs."""
    host = CannedShellHost(UPGRADABLE_OUTPUT)
    upgrades = await AptUpgradable.process(host)
    assert isinstance(upgrades, dict)
    assert json.loads(json.dumps(upgrades)) == UPGRADABLE_PACKAGES


async def test_apt_upgradable_empty_output_reads_as_empty_dict():
    """A fully up-to-date machine prints only the header (or nothing at
    all) -- both read as {}."""
    assert await AptUpgradable.process(CannedShellHost("")) == {}
    assert await AptUpgradable.process(CannedShellHost("Listing... Done\n")) == {}


# --- Parser edge cases ---------------------------------------------------------


async def test_apt_upgradable_ignores_noise_and_non_upgradable_rows():
    """Blank lines, the header and rows without the ``[upgradable from:``
    tail (an ``[installed`` row, a truncated row) never contribute."""
    stdout = """\
Listing... Done

adduser/noble,now 3.137ubuntu1 all [installed,automatic]
vim/noble-updates 2:9.1.0016-1ubuntu7.6 amd64 [upgradable from: 2:9.1.0016-1ubuntu7.4]
broken/noble-updates 1.0 amd64 [upgradable from:
"""
    host = CannedShellHost(stdout)
    assert await AptUpgradable.process(host) == {
        "vim": {
            "installed": "2:9.1.0016-1ubuntu7.4",
            "candidate": "2:9.1.0016-1ubuntu7.6",
        },
    }


async def test_apt_upgradable_duplicate_name_last_row_wins():
    """A name listed twice keeps its last row (apt prints one row per
    package; the parser stays deterministic if that ever changes)."""
    stdout = """\
vim/noble-updates 2:9.1.0016-1ubuntu7.5 amd64 [upgradable from: 2:9.1.0016-1ubuntu7.4]
vim/noble-security 2:9.1.0016-1ubuntu7.6 amd64 [upgradable from: 2:9.1.0016-1ubuntu7.4]
"""
    host = CannedShellHost(stdout)
    assert await AptUpgradable.process(host) == {
        "vim": {
            "installed": "2:9.1.0016-1ubuntu7.4",
            "candidate": "2:9.1.0016-1ubuntu7.6",
        },
    }


# --- SimulatedHost seam -------------------------------------------------------


async def test_apt_upgradable_reads_the_simulated_seam():
    """A SimulatedHost exposes ``apt_upgradable`` directly, bypassing the
    shell; the default seam is {} -- a fully up-to-date machine."""
    host = SimulatedHost(name="h", apt_upgradable=UPGRADABLE_PACKAGES)
    assert await AptUpgradable.process(host) == UPGRADABLE_PACKAGES
    assert await AptUpgradable.process(SimulatedHost(name="h")) == {}


async def test_apt_upgradable_seam_returns_copies():
    """Mutating the returned dict or one of its entry dicts never touches
    the simulated state."""
    host = SimulatedHost(name="h", apt_upgradable=UPGRADABLE_PACKAGES)
    upgrades = await AptUpgradable.process(host)
    upgrades["vim"]["candidate"] = "mutated"
    del upgrades["libssl3t64"]
    upgrades["stray"] = {"installed": "1", "candidate": "2"}
    assert await AptUpgradable.process(host) == UPGRADABLE_PACKAGES


# --- gather_fact fan-out ------------------------------------------------------


async def test_gather_fact_apt_upgradable_reads_simulated_seams():
    """Across a simulated fleet each host answers from its own seam state,
    keyed by host name in inventory order."""
    host1 = SimulatedHost(name="web1", apt_upgradable=UPGRADABLE_PACKAGES)
    host2 = SimulatedHost(name="web2")
    upgrades = await gather_fact([host1, host2], AptUpgradable)
    assert upgrades == {"web1": UPGRADABLE_PACKAGES, "web2": {}}


async def test_gather_fact_apt_upgradable_over_canned_shells():
    """The fan-out drives every host's real-transport path with the same
    COMMAND and keys the parsed results by host name."""
    host1 = CannedShellHost(UPGRADABLE_OUTPUT)
    host1.name = "web1"
    host2 = CannedShellHost("Listing... Done\n")
    host2.name = "web2"
    upgrades = await gather_fact([host1, host2], AptUpgradable)
    assert upgrades == {"web1": UPGRADABLE_PACKAGES, "web2": {}}
    assert host1.last_shell == AptUpgradable.COMMAND
    assert host2.last_shell == AptUpgradable.COMMAND


# --- AptAutoremovable: command (a reemote5 original, no pyinfra fixture) -------


def test_apt_autoremovable_command_simulates_the_autoremove():
    """The command only simulates -- apt-get --just-print autoremove, with
    the stderr redirect swallowing the unprivileged lock warnings."""
    assert AptAutoremovable.COMMAND == "apt-get --just-print autoremove 2>/dev/null"


# --- Real transport (canned realistic autoremove-simulation output) -----------

# Realistic ``apt-get --just-print autoremove`` output on Ubuntu noble:
# progress lines, the indented removal summary, the count line, then one
# machine-readable Remv row per removable package. pyinfra ships no
# fixture for this fact -- it is a reemote5 original.
AUTOREMOVE_OUTPUT = """\
Reading package lists... Done
Building dependency tree... Done
Reading state information... Done
The following packages will be REMOVED:
  libllvm17t64 linux-headers-6.8.0-45 linux-headers-6.8.0-45-generic
0 upgraded, 0 newly installed, 3 to remove and 12 not upgraded.
Remv linux-headers-6.8.0-45-generic [6.8.0-45.45]
Remv linux-headers-6.8.0-45 [6.8.0-45.45]
Remv libllvm17t64 [1:17.0.6-9ubuntu1]
"""

# The parsed answer: only the Remv names, de-duplicated and sorted, the
# bracketed versions dropped.
AUTOREMOVABLE_PACKAGES = [
    "libllvm17t64",
    "linux-headers-6.8.0-45",
    "linux-headers-6.8.0-45-generic",
]


async def test_apt_autoremovable_parses_realistic_output():
    """Over the real-transport path, ``AptAutoremovable`` collects only the
    Remv rows into the sorted name list -- never the summary names."""
    host = CannedShellHost(AUTOREMOVE_OUTPUT)
    assert await AptAutoremovable.process(host) == AUTOREMOVABLE_PACKAGES
    assert host.last_shell == AptAutoremovable.COMMAND


async def test_apt_autoremovable_nothing_to_remove_reads_as_empty_list():
    """A machine with nothing autoremovable prints the progress and count
    lines but no Remv row -- that reads as [], and so does no output."""
    stdout = """\
Reading package lists... Done
Building dependency tree... Done
Reading state information... Done
0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.
"""
    assert await AptAutoremovable.process(CannedShellHost(stdout)) == []
    assert await AptAutoremovable.process(CannedShellHost("")) == []


# --- Parser edge cases ---------------------------------------------------------


async def test_apt_autoremovable_ignores_summary_without_remv_rows():
    """The indented names under ``The following packages will be REMOVED:``
    alone never count -- only the machine-readable Remv rows do."""
    stdout = """\
The following packages will be REMOVED:
  libllvm17t64 linux-headers-6.8.0-45
"""
    assert await AptAutoremovable.process(CannedShellHost(stdout)) == []


async def test_apt_autoremovable_dedups_sorts_and_keeps_arch_suffix():
    """A name in two Remv rows contributes one entry, the result is sorted
    and an architecture-suffixed name keeps its suffix."""
    stdout = """\
Remv linux-image-generic [6.8.0-45.45]
Remv libc6:i386 [2.39-0ubuntu8.3]
Remv docker-ce [5:24.0.7-1~ubuntu.22.04~jammy]
Remv linux-image-generic [6.8.0-45.45]
"""
    host = CannedShellHost(stdout)
    assert await AptAutoremovable.process(host) == [
        "docker-ce",
        "libc6:i386",
        "linux-image-generic",
    ]


# --- SimulatedHost seam -------------------------------------------------------


async def test_apt_autoremovable_reads_the_simulated_seam():
    """A SimulatedHost exposes ``apt_autoremovable`` directly, bypassing the
    shell; the default seam is [] -- nothing an autoremove would remove."""
    host = SimulatedHost(name="h", apt_autoremovable=AUTOREMOVABLE_PACKAGES)
    assert await AptAutoremovable.process(host) == AUTOREMOVABLE_PACKAGES
    assert await AptAutoremovable.process(SimulatedHost(name="h")) == []


async def test_apt_autoremovable_seam_returns_copies():
    """Mutating the returned list never touches the simulated state."""
    host = SimulatedHost(name="h", apt_autoremovable=AUTOREMOVABLE_PACKAGES)
    removable = await AptAutoremovable.process(host)
    removable.append("stray")
    removable.remove("libllvm17t64")
    assert await AptAutoremovable.process(host) == AUTOREMOVABLE_PACKAGES


# --- gather_fact fan-out ------------------------------------------------------


async def test_gather_fact_apt_autoremovable_reads_simulated_seams():
    """Across a simulated fleet each host answers from its own seam state,
    keyed by host name in inventory order."""
    host1 = SimulatedHost(name="web1", apt_autoremovable=AUTOREMOVABLE_PACKAGES)
    host2 = SimulatedHost(name="web2")
    removable = await gather_fact([host1, host2], AptAutoremovable)
    assert removable == {"web1": AUTOREMOVABLE_PACKAGES, "web2": []}


async def test_gather_fact_apt_autoremovable_over_canned_shells():
    """The fan-out drives every host's real-transport path with the same
    COMMAND and keys the parsed results by host name."""
    host1 = CannedShellHost(AUTOREMOVE_OUTPUT)
    host1.name = "web1"
    host2 = CannedShellHost("")
    host2.name = "web2"
    removable = await gather_fact([host1, host2], AptAutoremovable)
    assert removable == {"web1": AUTOREMOVABLE_PACKAGES, "web2": []}
    assert host1.last_shell == AptAutoremovable.COMMAND
    assert host2.last_shell == AptAutoremovable.COMMAND


# --- DebPackage fact (pyinfra parity) ----------------------------------------

# A standalone ``.deb`` already on the target host (the kind ``files.put``
# uploads before ``apt.deb`` installs it), reused across the fact and operation
# tests below.
DEB_FILE_PATH = "/tmp/google-chrome-stable_current_amd64.deb"
DEB_FILE_NAME = "google-chrome-stable"
DEB_FILE_VERSION = "131.0.6778.85-1"
DEB_FILE_INFO = {"name": DEB_FILE_NAME, "version": DEB_FILE_VERSION}


def test_deb_package_command_matches_pyinfra():
    """The command is pyinfra's ``deb.DebPackage/package.json`` fixture command
    verbatim: inspect a ``.deb`` file, else query the installed package."""
    assert DebPackage.command("package-name") == (
        "test -f package-name && case package-name in "
        "*.deb) dpkg -I package-name ;; "
        "*) dpkg -s package-name 2>/dev/null || true ;; esac || "
        "dpkg -s package-name 2>/dev/null || true"
    )


def test_deb_package_command_quotes_the_path():
    """A path with spaces and shell metacharacters is shell-quoted in every
    position (pyinfra's ``package_needs_quotes.json`` fixture)."""
    quoted = "'/tmp/my package; whoami.deb'"
    assert DebPackage.command("/tmp/my package; whoami.deb") == (
        f"test -f {quoted} && case {quoted} in "
        f"*.deb) dpkg -I {quoted} ;; "
        f"*) dpkg -s {quoted} 2>/dev/null || true ;; esac || "
        f"dpkg -s {quoted} 2>/dev/null || true"
    )


async def test_deb_package_parses_control_fields():
    """Over the real-transport path, ``DebPackage`` parses the ``Package:`` and
    ``Version:`` control fields (pyinfra's ``package.json`` fact)."""
    host = CannedShellHost("Package: package-name\nVersion: 1")
    expected = {"name": "package-name", "version": "1"}
    assert await DebPackage.process(host, "package-name") == expected


# The ``output`` field of pyinfra's ``deb.DebPackage/whitespace.json`` fixture:
# ``dpkg -I`` archive metadata whose control fields are tab-indented.
DPKG_I_OUTPUT = """\
 new Debian package, version 2.0.
 size 73275616 bytes: control archive=1079 bytes.
     123 bytes,     5 lines      changelog
       2 bytes,     1 lines      compat
     336 bytes,     9 lines      control
     147 bytes,     4 lines      copyright
      14 bytes,     1 lines      install
     237 bytes,    10 lines   *  postinst             #!/bin/sh
     377 bytes,    20 lines   *  prerm                #!/bin/sh
     156 bytes,    11 lines   *  rules                #!/usr/bin/make
 Version:\t\t8.1.21-0
 Maintainer:\t\tIBM
 Section:\t\tUtilities/Archiving
 Homepage:\t\thttp://www.ibm.com/software/tivoli/products/storage-mgr/
 Package:\t\ttivsm-api64
 Architecture:\t\tamd64
 Description:\t\tThis is the IBM Storage Protect Linux API
 Pre-Depends:\t\tgskcrypt64 (>= 8.0-55.31),
                                   gskssl64   (>= 8.0-55.31)
"""


async def test_deb_package_parses_tab_indented_dpkg_i_output():
    """``dpkg -I``'s tab-indented ``Package:``/``Version:`` fields parse after
    stripping (pyinfra's ``whitespace.json`` fact); the archive-listing noise
    and the ``Pre-Depends`` continuation line are ignored."""
    host = CannedShellHost(DPKG_I_OUTPUT)
    expected = {"name": "tivsm-api64", "version": "8.1.21-0"}
    assert await DebPackage.process(host, "/root/tivsm-api64.amd64.deb") == expected


async def test_deb_package_absent_parses_to_none():
    """No ``Package:``/``Version:`` line reads as None -- reemote5's
    None-on-absent convention (pyinfra returns an empty dict)."""
    host = CannedShellHost("")
    assert await DebPackage.process(host, "random-file.txt") is None


async def test_deb_package_reads_the_simulated_seam():
    """A SimulatedHost answers a ``.deb`` path from ``deb_files`` and an
    installed name from ``apt_packages``; an unknown key reads as None."""
    host = SimulatedHost(
        name="h",
        deb_files={DEB_FILE_PATH: DEB_FILE_INFO},
        apt_packages={DEB_FILE_NAME: [DEB_FILE_VERSION]},
    )
    assert await DebPackage.process(host, DEB_FILE_PATH) == DEB_FILE_INFO
    expected = {"name": DEB_FILE_NAME, "version": DEB_FILE_VERSION}
    assert await DebPackage.process(host, DEB_FILE_NAME) == expected
    assert await DebPackage.process(host, "absent") is None


async def test_deb_package_seam_returns_copies():
    """Mutating the returned dict never touches the simulated state."""
    host = SimulatedHost(name="h", deb_files={DEB_FILE_PATH: DEB_FILE_INFO})
    info = await DebPackage.process(host, DEB_FILE_PATH)
    info["version"] = "tampered"
    assert await DebPackage.process(host, DEB_FILE_PATH) == DEB_FILE_INFO


# --- apt.deb operation (pyinfra parity) --------------------------------------

# The three-pass install sequence pyinfra's ``apt.deb`` yields (its ``add.json``
# fixture commands), rendered for ``DEB_FILE_PATH``.
DPKG_INSTALL_SHELL = f"dpkg --force-confdef --force-confold -i {DEB_FILE_PATH}"
DPKG_INSTALL_TOLERANT_SHELL = f"{DPKG_INSTALL_SHELL} 2> /dev/null || true"
APT_INSTALL_FIX_SHELL = (
    "DEBIAN_FRONTEND=noninteractive apt-get -y "
    '-o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" '
    "install -f"
)
APT_REMOVE_SHELL = (
    "DEBIAN_FRONTEND=noninteractive apt-get -y "
    '-o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" '
    "remove test"
)
INSTALL_COMMANDS = [
    DebInstallCommand(src=DEB_FILE_PATH, ignore_deps_errors=True),
    AptInstallFixCommand(),
    DebInstallCommand(src=DEB_FILE_PATH),
]


async def test_apt_deb_installs_an_absent_package():
    """present=True with a ``.deb`` whose package is not installed yields the
    three-pass install sequence and records the change."""
    host = SimulatedHost(name="h", deb_files={DEB_FILE_PATH: DEB_FILE_INFO})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.deb(ctx, src=DEB_FILE_PATH)]
    assert ctx.changed
    assert ctx.reason == f"deb {DEB_FILE_PATH} not installed, will install"
    assert commands == INSTALL_COMMANDS


async def test_apt_deb_no_change_when_installed_version_matches():
    """The file's package installed at the file's exact version is a no-op."""
    host = SimulatedHost(
        name="h",
        deb_files={DEB_FILE_PATH: DEB_FILE_INFO},
        apt_packages={DEB_FILE_NAME: [DEB_FILE_VERSION]},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.deb(ctx, src=DEB_FILE_PATH)]
    assert not ctx.changed
    assert ctx.reason == f"deb {DEB_FILE_PATH} is installed"
    assert commands == []


async def test_apt_deb_reinstalls_when_installed_version_differs():
    """A different installed version does not match the file, so present=True
    installs again -- the version-comparison idempotence pyinfra performs."""
    host = SimulatedHost(
        name="h",
        deb_files={DEB_FILE_PATH: DEB_FILE_INFO},
        apt_packages={DEB_FILE_NAME: ["130.0.6723.58-1"]},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.deb(ctx, src=DEB_FILE_PATH)]
    assert ctx.changed
    assert commands == INSTALL_COMMANDS


async def test_apt_deb_removes_an_installed_package():
    """present=False removes the file's package by name when that exact version
    is installed (pyinfra's ``remove_existing.json`` fixture)."""
    host = SimulatedHost(
        name="h",
        deb_files={DEB_FILE_PATH: DEB_FILE_INFO},
        apt_packages={DEB_FILE_NAME: [DEB_FILE_VERSION]},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.deb(ctx, src=DEB_FILE_PATH, present=False)]
    assert ctx.changed
    assert ctx.reason == f"deb {DEB_FILE_PATH} installed, will remove"
    assert commands == [AptRemoveCommand(packages=(DEB_FILE_NAME,))]


async def test_apt_deb_removal_noop_when_not_installed():
    """present=False with the file present but its package not installed is a
    no-op (pyinfra's ``remove_no_exist.json`` fixture)."""
    host = SimulatedHost(name="h", deb_files={DEB_FILE_PATH: DEB_FILE_INFO})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.deb(ctx, src=DEB_FILE_PATH, present=False)]
    assert not ctx.changed
    assert ctx.reason == f"deb {DEB_FILE_PATH} is not installed"
    assert commands == []


async def test_apt_deb_removal_noop_when_file_absent():
    """A present=False whose ``.deb`` is absent cannot resolve a package name, so
    it is a no-op -- the limitation the docstring records for an un-downloadable
    source."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.deb(ctx, src=DEB_FILE_PATH, present=False)]
    assert not ctx.changed
    assert ctx.reason == f"deb {DEB_FILE_PATH} is not installed"
    assert commands == []


async def test_apt_deb_rejects_a_url_src():
    """pyinfra downloads a URL ``.deb``; reemote5 has no download operation, so a
    URL src raises instead of silently doing nothing (mirroring dnf.rpm)."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="does not support a URL src"):
        _ = [
            c
            async for c in apt.deb(
                ctx, src="https://dl.google.com/linux/direct/google-chrome.deb"
            )
        ]


async def test_apt_deb_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors pyinfra's global-arguments slot; op() already strips
    the ``name``/``sudo`` meta-parameters, the rest is ignored."""
    host = SimulatedHost(name="h", deb_files={DEB_FILE_PATH: DEB_FILE_INFO})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.deb(ctx, src=DEB_FILE_PATH, future_flag=True)]
    assert commands == INSTALL_COMMANDS


async def test_apt_deb_wait_for_lock_true():
    """wait_for_lock=True threads the 60-second lock timeout into the apt-get
    passes (pyinfra's ``add_wait_for_lock.yaml`` fixture)."""
    host = SimulatedHost(name="h", deb_files={DEB_FILE_PATH: DEB_FILE_INFO})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.deb(ctx, src=DEB_FILE_PATH, wait_for_lock=True)]
    assert commands == [
        DebInstallCommand(src=DEB_FILE_PATH, ignore_deps_errors=True),
        AptInstallFixCommand(wait_for_lock=True),
        DebInstallCommand(src=DEB_FILE_PATH),
    ]
    assert "-o DPkg::Lock::Timeout=60" in commands[1].shell()


async def test_apt_deb_wait_for_lock_int():
    """An int wait_for_lock sets that timeout in seconds on the removal pass."""
    host = SimulatedHost(
        name="h",
        deb_files={DEB_FILE_PATH: DEB_FILE_INFO},
        apt_packages={DEB_FILE_NAME: [DEB_FILE_VERSION]},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apt.deb(ctx, src=DEB_FILE_PATH, present=False, wait_for_lock=30)
    ]
    assert commands == [AptRemoveCommand(packages=(DEB_FILE_NAME,), wait_for_lock=30)]
    assert "-o DPkg::Lock::Timeout=30" in commands[0].shell()


def test_deb_install_command_shell_and_elevation():
    """The descriptor renders the plain and tolerant ``dpkg -i`` forms and
    elevates as a whole under sudo -- the ``|| true`` fallback needs no root, so
    the ``dpkg`` arm stays elevated (unlike dnf.rpm's rejected ``|| rpm -i``)."""
    assert DebInstallCommand(src=DEB_FILE_PATH).shell() == DPKG_INSTALL_SHELL
    tolerant = DebInstallCommand(src=DEB_FILE_PATH, ignore_deps_errors=True)
    assert tolerant.shell() == DPKG_INSTALL_TOLERANT_SHELL
    assert command_shell(tolerant, sudo=True) == f"sudo {DPKG_INSTALL_TOLERANT_SHELL}"
    # A path with a space is shell-quoted.
    assert (
        DebInstallCommand(src="/tmp/my pkg.deb").shell()
        == "dpkg --force-confdef --force-confold -i '/tmp/my pkg.deb'"
    )


def test_apt_install_fix_command_shell_matches_pyinfra():
    """The dependency-fix pass renders pyinfra's ``install -f`` command, with the
    lock timeout inserted before the dpkg options when requested."""
    assert AptInstallFixCommand().shell() == APT_INSTALL_FIX_SHELL
    assert AptInstallFixCommand(wait_for_lock=True).shell() == (
        "DEBIAN_FRONTEND=noninteractive apt-get -y -o DPkg::Lock::Timeout=60 "
        '-o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" '
        "install -f"
    )
    assert command_shell(AptInstallFixCommand(), sudo=True) == (
        f"sudo {APT_INSTALL_FIX_SHELL}"
    )


def test_apt_remove_command_shell_matches_pyinfra():
    """The removal pass renders pyinfra's ``remove_existing.json`` command and
    shell-quotes each package name."""
    assert AptRemoveCommand(packages=("test",)).shell() == APT_REMOVE_SHELL
    assert AptRemoveCommand(packages=("my pkg",)).shell().endswith("remove 'my pkg'")


async def test_apt_deb_simulated_seam_mutates_state():
    """``send_command`` maps the install descriptors onto ``apt_packages`` and
    the removal descriptor back out; the middle fix pass has no state effect."""
    host = SimulatedHost(name="h", deb_files={DEB_FILE_PATH: DEB_FILE_INFO})
    for cmd in INSTALL_COMMANDS:
        await send_command(host, cmd)
    assert host.apt_packages == {DEB_FILE_NAME: [DEB_FILE_VERSION]}
    await send_command(host, AptRemoveCommand(packages=(DEB_FILE_NAME,)))
    assert host.apt_packages == {}


async def test_apt_deb_end_to_end_install_then_noop():
    """Through run(): the first apply installs the ``.deb`` into the simulated
    state; a second run sees the matching installed version and is a no-op."""

    def deb_deploy(host):
        yield op(apt.deb, name="Install Chrome from a .deb", src=DEB_FILE_PATH, sudo=True)

    host = SimulatedHost(name="h", deb_files={DEB_FILE_PATH: DEB_FILE_INFO})

    def factory(entry):
        return host  # reuse ONE host so the second run sees the first's install

    _plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], deb_deploy, host_factory=factory
    )
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == tuple(INSTALL_COMMANDS)
    assert host.apt_packages == {DEB_FILE_NAME: [DEB_FILE_VERSION]}
    log = format_log(report)
    assert f"sudo {DPKG_INSTALL_SHELL}" in log
    assert f"sudo {APT_INSTALL_FIX_SHELL}" in log

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], deb_deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert not entry2.changed
    assert entry2.reason == f"deb {DEB_FILE_PATH} is installed"


async def test_apt_deb_dry_run_sends_nothing():
    """dry=True materializes the plan (changed, three commands) but mutates no
    simulated state -- the install is only planned, never sent."""

    def deb_deploy(host):
        yield op(apt.deb, name="Install Chrome", src=DEB_FILE_PATH, sudo=True)

    host = SimulatedHost(name="h", deb_files={DEB_FILE_PATH: DEB_FILE_INFO})
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        deb_deploy,
        dry=True,
        host_factory=lambda entry: host,
    )
    assert report is None
    assert plans["h"][0].changed
    assert plans["h"][0].commands == tuple(INSTALL_COMMANDS)
    assert host.apt_packages == {}


async def test_apt_deb_remove_after_install_in_one_deploy():
    """The apply phase's fresh per-step fact cache sees the install, so a following
    present=False in the same deploy removes it -- while the prepare plan (which
    never sends) still shows the removal as a no-op."""

    def deb_deploy(host):
        yield op(apt.deb, name="install", src=DEB_FILE_PATH)
        yield op(apt.deb, name="remove", src=DEB_FILE_PATH, present=False)

    host = SimulatedHost(name="h", deb_files={DEB_FILE_PATH: DEB_FILE_INFO})
    runtimes, plans = await run_prepare([host], deb_deploy)
    assert plans["h"][0].changed  # install: file present, package absent
    assert not plans["h"][1].changed  # remove: still absent during prepare
    report = await run_apply(runtimes, plans=plans)
    install, remove = report.log
    assert install.changed
    assert remove.changed
    assert remove.reason == f"deb {DEB_FILE_PATH} installed, will remove"
    assert host.apt_packages == {}


# --- SimulateOperationWillChange fact (pyinfra parity) ------------------------

# The ``command`` field of pyinfra's ``apt.SimulateOperationWillChange``
# fixtures: the non-interactive prefix plus ``LC_ALL=C`` and ``--dry-run``.
SIMULATE_COMMAND = (
    "LC_ALL=C DEBIAN_FRONTEND=noninteractive apt-get -y "
    '-o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" '
    "upgrade --dry-run"
)

# The ``output`` field of pyinfra's ``apt.SimulateOperationWillChange/
# upgrade.json`` fixture, verbatim: the simulation banner, progress lines,
# the indented upgrade summary, the count line, then Inst/Conf detail rows.
SIM_UPGRADE_OUTPUT = [
    "NOTE: This is only a simulation!",
    "      apt-get needs root privileges for real execution.",
    "      Keep also in mind that locking is deactivated,",
    "      so don't depend on the relevance to the real current situation!",
    "Reading package lists... Done",
    "Building dependency tree... Done",
    "Reading state information... Done",
    "Calculating upgrade... Done",
    "The following packages will be upgraded:",
    "  apache2-bin firefox-esr gir1.2-javascriptcoregtk-4.0 gir1.2-javascriptcoregtk-4.1 gir1.2-webkit2-4.0",
    "  gir1.2-webkit2-4.1 libgsf-1-114 libgsf-1-common libgsf-bin libjavascriptcoregtk-4.0-18",
    "  libjavascriptcoregtk-4.1-0 libjavascriptcoregtk-6.0-1 libwebkit2gtk-4.0-37 libwebkit2gtk-4.1-0",
    "  libwebkitgtk-6.0-4",
    "15 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.",
    "Inst apache2-bin [2.4.62-1~deb12u1] (2.4.62-1~deb12u2 Debian-Security:12/stable-security [amd64])",
    "Inst firefox-esr [128.3.0esr-1~deb12u1] (128.3.1esr-1~deb12u1 Debian-Security:12/stable-security [amd64])",
    "Inst gir1.2-webkit2-4.0 [2.44.3-1~deb12u1] (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64]) []",
    "Inst gir1.2-javascriptcoregtk-4.0 [2.44.3-1~deb12u1] (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64]) []",
    "Inst libwebkit2gtk-4.0-37 [2.44.3-1~deb12u1] (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64]) []",
    "Inst libjavascriptcoregtk-4.0-18 [2.44.3-1~deb12u1] (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64])",
    "Inst gir1.2-webkit2-4.1 [2.44.3-1~deb12u1] (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64]) []",
    "Inst gir1.2-javascriptcoregtk-4.1 [2.44.3-1~deb12u1] (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64]) []",
    "Inst libwebkit2gtk-4.1-0 [2.44.3-1~deb12u1] (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64]) []",
    "Inst libjavascriptcoregtk-4.1-0 [2.44.3-1~deb12u1] (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64])",
    "Inst libgsf-1-common [1.14.50-1] (1.14.50-1+deb12u1 Debian-Security:12/stable-security [all])",
    "Inst libgsf-1-114 [1.14.50-1] (1.14.50-1+deb12u1 Debian-Security:12/stable-security [amd64])",
    "Inst libgsf-bin [1.14.50-1] (1.14.50-1+deb12u1 Debian-Security:12/stable-security [amd64])",
    "Inst libwebkitgtk-6.0-4 [2.44.3-1~deb12u1] (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64]) []",
    "Inst libjavascriptcoregtk-6.0-1 [2.44.3-1~deb12u1] (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64])",
    "Conf apache2-bin (2.4.62-1~deb12u2 Debian-Security:12/stable-security [amd64])",
    "Conf firefox-esr (128.3.1esr-1~deb12u1 Debian-Security:12/stable-security [amd64])",
    "Conf gir1.2-webkit2-4.0 (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64])",
    "Conf gir1.2-javascriptcoregtk-4.0 (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64])",
    "Conf libwebkit2gtk-4.0-37 (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64])",
    "Conf libjavascriptcoregtk-4.0-18 (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64])",
    "Conf gir1.2-webkit2-4.1 (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64])",
    "Conf gir1.2-javascriptcoregtk-4.1 (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64])",
    "Conf libwebkit2gtk-4.1-0 (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64])",
    "Conf libjavascriptcoregtk-4.1-0 (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64])",
    "Conf libgsf-1-common (1.14.50-1+deb12u1 Debian-Security:12/stable-security [all])",
    "Conf libgsf-1-114 (1.14.50-1+deb12u1 Debian-Security:12/stable-security [amd64])",
    "Conf libgsf-bin (1.14.50-1+deb12u1 Debian-Security:12/stable-security [amd64])",
    "Conf libwebkitgtk-6.0-4 (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64])",
    "Conf libjavascriptcoregtk-6.0-1 (2.46.0-2~deb12u1 Debian-Security:12/stable-security [amd64])",
]

# The fixture's ``fact`` field: fifteen proposed upgrades, nothing else.
SIM_UPGRADE_FACT = {
    "upgraded": 15,
    "newly_installed": 0,
    "removed": 0,
    "not_upgraded": 0,
}

# The ``output`` field of pyinfra's ``upgrade-nochanges.json`` fixture.
SIM_NOCHANGES_OUTPUT = [
    "NOTE: This is only a simulation!",
    "apt-get needs root privileges for real execution.",
    "Keep also in mind that locking is deactivated,",
    "so don't depend on the relevance to the real current situation!",
    "Reading package lists... Done",
    "Building dependency tree... Done",
    "Reading state information... Done",
    "Calculating upgrade... Done",
    "0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.",
]

SIM_ZERO_FACT = {"upgraded": 0, "newly_installed": 0, "removed": 0, "not_upgraded": 0}

# The proposed counts of pyinfra's ``apt.dist_upgrade/dist_upgrade_autoremove
# .json`` operation fixture -- nonzero in every field.
DIST_UPGRADE_CHANGES = {
    "upgraded": 10,
    "newly_installed": 3,
    "removed": 2,
    "not_upgraded": 3,
}


def test_simulate_command_matches_pyinfra():
    """The command is pyinfra's fixture command verbatim: LC_ALL=C, the
    non-interactive prefix, then the subcommand with --dry-run."""
    assert SimulateOperationWillChange.command("upgrade") == SIMULATE_COMMAND


async def test_simulate_parses_the_upgrade_fixture():
    """Over the real-transport path, the count line parses into pyinfra's
    upgrade.json fact; the Inst/Conf rows are never mistaken for it."""
    host = CannedShellHost("\n".join(SIM_UPGRADE_OUTPUT))
    assert await SimulateOperationWillChange.process(host, "upgrade") == SIM_UPGRADE_FACT
    assert host.last_shell == SIMULATE_COMMAND


async def test_simulate_parses_the_nochanges_fixture():
    """pyinfra's upgrade-nochanges.json fixture: every count zero."""
    host = CannedShellHost("\n".join(SIM_NOCHANGES_OUTPUT))
    assert await SimulateOperationWillChange.process(host, "upgrade") == SIM_ZERO_FACT


def test_simulate_parse_edge_cases():
    """No summary line reads as None (the deviation from pyinfra's
    exception); a summary with all four counts parses; the first summary
    line wins."""
    assert SimulateOperationWillChange.parse([]) is None
    assert SimulateOperationWillChange.parse(SIM_UPGRADE_OUTPUT[:8]) is None
    summary = "10 upgraded, 3 newly installed, 2 to remove and 3 not upgraded."
    assert SimulateOperationWillChange.parse([summary]) == DIST_UPGRADE_CHANGES
    twice = [summary, "15 upgraded, 0 newly installed, 0 to remove and 0 not upgraded."]
    assert SimulateOperationWillChange.parse(twice) == DIST_UPGRADE_CHANGES


async def test_simulate_reads_the_simulated_seam():
    """A SimulatedHost exposes ``apt_simulations`` keyed by subcommand
    phrase; an absent phrase reads as None, like a failed simulation."""
    host = SimulatedHost(name="h", apt_simulations={"dist-upgrade": DIST_UPGRADE_CHANGES})
    assert await SimulateOperationWillChange.process(host, "dist-upgrade") == (
        DIST_UPGRADE_CHANGES
    )
    assert await SimulateOperationWillChange.process(host, "autoremove") is None
    assert await SimulateOperationWillChange.process(SimulatedHost(name="h"), "x") is None


async def test_simulate_seam_returns_copies():
    """Mutating the returned dict never touches the simulated state."""
    host = SimulatedHost(name="h", apt_simulations={"dist-upgrade": DIST_UPGRADE_CHANGES})
    changes = await SimulateOperationWillChange.process(host, "dist-upgrade")
    changes["upgraded"] = 999
    assert host.apt_simulations["dist-upgrade"] == DIST_UPGRADE_CHANGES


async def test_gather_fact_simulate_forwards_the_command_argument():
    """The fan-out forwards the subcommand phrase to every host and keys
    the answers by host name in inventory order."""
    host1 = SimulatedHost(name="web1", apt_simulations={"dist-upgrade": DIST_UPGRADE_CHANGES})
    host2 = SimulatedHost(name="web2")
    changes = await gather_fact([host1, host2], SimulateOperationWillChange, "dist-upgrade")
    assert changes == {"web1": DIST_UPGRADE_CHANGES, "web2": None}


async def test_gather_fact_simulate_over_canned_shells():
    """The fan-out drives every host's real-transport path with the same
    rendered command; a host printing no summary line answers None."""
    host1 = CannedShellHost("\n".join(SIM_UPGRADE_OUTPUT))
    host1.name = "web1"
    host2 = CannedShellHost("")
    host2.name = "web2"
    changes = await gather_fact([host1, host2], SimulateOperationWillChange, "upgrade")
    assert changes == {"web1": SIM_UPGRADE_FACT, "web2": None}
    assert host1.last_shell == SIMULATE_COMMAND
    assert host2.last_shell == SIMULATE_COMMAND


# --- apt.dist_upgrade operation (pyinfra parity) ------------------------------

# The ``commands`` field of pyinfra's ``apt.dist_upgrade/dist_upgrade.json``
# fixture -- the bare dist-upgrade, never ``--autoremove`` (the split).
DIST_UPGRADE_SHELL = (
    "DEBIAN_FRONTEND=noninteractive apt-get -y "
    '-o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" '
    "dist-upgrade"
)

# The ``commands`` field of pyinfra's ``dist_upgrade_wait_for_lock.yaml``
# fixture: the lock timeout inserted before the dpkg options.
DIST_UPGRADE_LOCK_SHELL = (
    "DEBIAN_FRONTEND=noninteractive apt-get -y -o DPkg::Lock::Timeout=60 "
    '-o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" '
    "dist-upgrade"
)

DIST_UPGRADE_REASON = (
    "dist-upgrade will change packages (10 upgraded, 3 newly installed, "
    "2 to remove, 3 not upgraded)"
)


async def test_apt_dist_upgrade_runs_when_changes_proposed():
    """Nonzero simulation counts yield the dist-upgrade and record the
    change with the proposed counts in the reason."""
    host = SimulatedHost(name="h", apt_simulations={"dist-upgrade": DIST_UPGRADE_CHANGES})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.dist_upgrade(ctx)]
    assert ctx.changed
    assert ctx.reason == DIST_UPGRADE_REASON
    assert commands == [AptDistUpgradeCommand()]


async def test_apt_dist_upgrade_noop_when_no_changes_proposed():
    """All-zero counts are pyinfra's noop: nothing yields and the reason
    mirrors its noop_description verbatim."""
    host = SimulatedHost(name="h", apt_simulations={"dist-upgrade": SIM_ZERO_FACT})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.dist_upgrade(ctx)]
    assert not ctx.changed
    assert ctx.reason == "dist-upgrade skipped, no changes would be performed"
    assert commands == []


async def test_apt_dist_upgrade_runs_anyway_on_failed_simulation():
    """A failed simulation (None -- no seam entry, no summary line) runs
    the real command anyway, pyinfra's ``not changes`` fallback."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.dist_upgrade(ctx)]
    assert ctx.changed
    assert ctx.reason == "dist-upgrade simulation failed, will run anyway"
    assert commands == [AptDistUpgradeCommand()]


async def test_apt_dist_upgrade_counts_not_upgraded_too():
    """Only ``not_upgraded`` nonzero still counts as a proposed change --
    pyinfra's condition checks all four counts."""
    simulation = {**SIM_ZERO_FACT, "not_upgraded": 3}
    host = SimulatedHost(name="h", apt_simulations={"dist-upgrade": simulation})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.dist_upgrade(ctx)]
    assert ctx.changed
    assert commands == [AptDistUpgradeCommand()]


async def test_apt_dist_upgrade_wait_for_lock():
    """wait_for_lock=True threads the 60-second timeout (pyinfra's
    ``dist_upgrade_wait_for_lock.yaml``); an int sets that many seconds."""
    host = SimulatedHost(name="h", apt_simulations={"dist-upgrade": DIST_UPGRADE_CHANGES})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.dist_upgrade(ctx, wait_for_lock=True)]
    assert commands == [AptDistUpgradeCommand(wait_for_lock=True)]
    assert commands[0].shell() == DIST_UPGRADE_LOCK_SHELL
    ctx30 = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands30 = [c async for c in apt.dist_upgrade(ctx30, wait_for_lock=30)]
    assert "-o DPkg::Lock::Timeout=30" in commands30[0].shell()


async def test_apt_dist_upgrade_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors pyinfra's global-arguments slot; op() already
    strips the ``name``/``sudo`` meta-parameters, the rest is ignored."""
    host = SimulatedHost(name="h", apt_simulations={"dist-upgrade": DIST_UPGRADE_CHANGES})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.dist_upgrade(ctx, future_flag=True)]
    assert commands == [AptDistUpgradeCommand()]


def test_apt_dist_upgrade_command_shell_matches_pyinfra():
    """The descriptor renders pyinfra's dist_upgrade.json command verbatim
    -- never ``--autoremove`` (split into apt.auto_remove) -- and elevates
    as a whole under sudo/su."""
    assert AptDistUpgradeCommand().shell() == DIST_UPGRADE_SHELL
    assert "--autoremove" not in AptDistUpgradeCommand().shell()
    assert AptDistUpgradeCommand(wait_for_lock=True).shell() == DIST_UPGRADE_LOCK_SHELL
    assert command_shell(AptDistUpgradeCommand(), sudo=True) == f"sudo {DIST_UPGRADE_SHELL}"
    assert command_shell(AptDistUpgradeCommand(), su=True) == (
        f"su -c {shlex.quote(DIST_UPGRADE_SHELL)}"
    )


async def test_apt_dist_upgrade_simulated_seam_mutates_state():
    """``send_command`` applies the pending upgrades into ``apt_packages``,
    clears ``apt_upgradable`` and zeroes the dist-upgrade simulation."""
    host = SimulatedHost(
        name="h",
        apt_simulations={"dist-upgrade": DIST_UPGRADE_CHANGES},
        apt_packages={"vim": ["2:9.1.0016-1ubuntu7.4"]},
        apt_upgradable={"vim": {"installed": "2:9.1.0016-1ubuntu7.4", "candidate": "2:9.1.0016-1ubuntu7.6"}},
    )
    await send_command(host, AptDistUpgradeCommand())
    assert host.apt_packages == {"vim": ["2:9.1.0016-1ubuntu7.6"]}
    assert host.apt_upgradable == {}
    assert host.apt_simulations["dist-upgrade"] == SIM_ZERO_FACT


async def test_apt_dist_upgrade_end_to_end_upgrade_then_noop():
    """Through run(): the first apply dist-upgrades the simulated state;
    a second run sees the zeroed simulation and is a no-op."""

    def upgrade_deploy(host):
        yield op(apt.dist_upgrade, name="Dist-upgrade all packages", sudo=True)

    host = SimulatedHost(
        name="h",
        apt_simulations={"dist-upgrade": DIST_UPGRADE_CHANGES},
        apt_upgradable={"vim": {"installed": "2:9.1-1", "candidate": "2:9.1-2"}},
    )

    def factory(entry):
        return host  # reuse ONE host so the second run sees the first's upgrade

    _plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], upgrade_deploy, host_factory=factory
    )
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (AptDistUpgradeCommand(),)
    assert host.apt_packages == {"vim": ["2:9.1-2"]}
    log = format_log(report)
    assert f"sudo {DIST_UPGRADE_SHELL}" in log

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], upgrade_deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert not entry2.changed
    assert entry2.reason == "dist-upgrade skipped, no changes would be performed"


async def test_apt_dist_upgrade_dry_run_sends_nothing():
    """dry=True materializes the plan (changed, one command) but mutates no
    simulated state -- the upgrade is only planned, never sent."""

    def upgrade_deploy(host):
        yield op(apt.dist_upgrade, name="Dist-upgrade", sudo=True)

    host = SimulatedHost(name="h", apt_simulations={"dist-upgrade": DIST_UPGRADE_CHANGES})
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        upgrade_deploy,
        dry=True,
        host_factory=lambda entry: host,
    )
    assert report is None
    assert plans["h"][0].changed
    assert plans["h"][0].commands == (AptDistUpgradeCommand(),)
    assert host.apt_simulations["dist-upgrade"] == DIST_UPGRADE_CHANGES


# --- apt.auto_remove operation (a reemote5 original standalone) ---------------

# The autoremove rendering: the same non-interactive prefix as pyinfra's
# dist-upgrade fixtures, with the standalone ``autoremove`` subcommand.
AUTOREMOVE_SHELL = (
    "DEBIAN_FRONTEND=noninteractive apt-get -y "
    '-o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" '
    "autoremove"
)


async def test_apt_auto_remove_removes_orphaned_dependencies():
    """A non-empty AptAutoremovable list yields the autoremove; the reason
    names the (already sorted) packages."""
    host = SimulatedHost(name="h", apt_autoremovable=AUTOREMOVABLE_PACKAGES)
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.auto_remove(ctx)]
    assert ctx.changed
    assert ctx.reason == (
        "autoremovable packages will be removed: "
        "libllvm17t64, linux-headers-6.8.0-45, linux-headers-6.8.0-45-generic"
    )
    assert commands == [AptAutoRemoveCommand()]


async def test_apt_auto_remove_noop_when_nothing_removable():
    """An empty list is a no-op -- the standalone cleanup converges."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.auto_remove(ctx)]
    assert not ctx.changed
    assert ctx.reason == "no autoremovable packages"
    assert commands == []


async def test_apt_auto_remove_wait_for_lock():
    """wait_for_lock threads the DPkg::Lock::Timeout option into the
    autoremove, like the other apt-get operations."""
    host = SimulatedHost(name="h", apt_autoremovable=AUTOREMOVABLE_PACKAGES)
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.auto_remove(ctx, wait_for_lock=True)]
    assert commands == [AptAutoRemoveCommand(wait_for_lock=True)]
    assert "-o DPkg::Lock::Timeout=60" in commands[0].shell()
    ctx30 = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands30 = [c async for c in apt.auto_remove(ctx30, wait_for_lock=30)]
    assert "-o DPkg::Lock::Timeout=30" in commands30[0].shell()


async def test_apt_auto_remove_absorbs_unknown_kwargs():
    """``**kwargs`` absorbs pyinfra's global arguments."""
    host = SimulatedHost(name="h", apt_autoremovable=AUTOREMOVABLE_PACKAGES)
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.auto_remove(ctx, future_flag=True)]
    assert commands == [AptAutoRemoveCommand()]


def test_apt_auto_remove_command_shell_and_elevation():
    """The descriptor renders the non-interactive autoremove and elevates
    as a whole under sudo/su."""
    assert AptAutoRemoveCommand().shell() == AUTOREMOVE_SHELL
    assert command_shell(AptAutoRemoveCommand(), sudo=True) == f"sudo {AUTOREMOVE_SHELL}"
    assert command_shell(AptAutoRemoveCommand(), su=True) == (
        f"su -c {shlex.quote(AUTOREMOVE_SHELL)}"
    )


async def test_apt_auto_remove_simulated_seam_mutates_state():
    """``send_command`` pops the removable names from ``apt_packages``,
    clears ``apt_autoremovable`` and zeroes the autoremove simulation;
    packages still in use stay installed."""
    host = SimulatedHost(
        name="h",
        apt_autoremovable=list(AUTOREMOVABLE_PACKAGES),
        apt_packages={name: ["1.0"] for name in [*AUTOREMOVABLE_PACKAGES, "vim"]},
    )
    await send_command(host, AptAutoRemoveCommand())
    assert host.apt_packages == {"vim": ["1.0"]}
    assert host.apt_autoremovable == []
    assert host.apt_simulations["autoremove"] == SIM_ZERO_FACT


async def test_apt_auto_remove_end_to_end_remove_then_noop():
    """Through run(): the first apply autoremoves the simulated orphans;
    a second run sees the empty list and is a no-op."""

    def cleanup_deploy(host):
        yield op(apt.auto_remove, name="Remove orphaned dependencies", sudo=True)

    host = SimulatedHost(
        name="h",
        apt_autoremovable=list(AUTOREMOVABLE_PACKAGES),
        apt_packages={name: ["1.0"] for name in AUTOREMOVABLE_PACKAGES},
    )

    def factory(entry):
        return host

    _plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], cleanup_deploy, host_factory=factory
    )
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (AptAutoRemoveCommand(),)
    assert host.apt_packages == {}
    log = format_log(report)
    assert f"sudo {AUTOREMOVE_SHELL}" in log

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], cleanup_deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert not entry2.changed
    assert entry2.reason == "no autoremovable packages"


async def test_apt_dist_upgrade_then_auto_remove_in_one_deploy():
    """The upgrade-then-cleanup composition the split enables: the apply
    phase's fresh per-step fact cache sees the upgrade before the
    autoremove materializes, and both land in lockstep order."""

    def upgrade_and_clean(host):
        yield op(apt.dist_upgrade, name="Dist-upgrade all packages", sudo=True)
        yield op(apt.auto_remove, name="Remove orphaned dependencies", sudo=True)

    host = SimulatedHost(
        name="h",
        apt_simulations={"dist-upgrade": DIST_UPGRADE_CHANGES},
        apt_upgradable={"vim": {"installed": "2:9.1-1", "candidate": "2:9.1-2"}},
        apt_autoremovable=list(AUTOREMOVABLE_PACKAGES),
        apt_packages={"vim": ["2:9.1-1"], **{name: ["1.0"] for name in AUTOREMOVABLE_PACKAGES}},
    )
    runtimes, plans = await run_prepare([host], upgrade_and_clean)
    assert plans["h"][0].changed  # upgrade: changes proposed
    assert plans["h"][1].changed  # cleanup: orphans already present in pre-state
    report = await run_apply(runtimes, plans=plans)
    upgrade, cleanup = report.log
    assert upgrade.changed
    assert upgrade.commands == (AptDistUpgradeCommand(),)
    assert cleanup.changed
    assert cleanup.commands == (AptAutoRemoveCommand(),)
    assert host.apt_packages == {"vim": ["2:9.1-2"]}
    assert host.apt_upgradable == {}
    assert host.apt_autoremovable == []
    log = format_log(report)
    assert log.index(f"sudo {DIST_UPGRADE_SHELL}") < log.index(f"sudo {AUTOREMOVE_SHELL}")


# --- apt.key operation (pyinfra apt.key, install half) ------------------------

# A stand-in ASCII-armored GPG public key. apt.key installs the *content* of an
# IO stream (never a path), so the tests only need opaque, stable bytes; the
# simulated host stores them verbatim at the keyring path (it models no gpg
# --dearmor conversion).
GPG_KEY_BYTES = (
    b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
    b"mQIN reemote5 test key material\n"
    b"-----END PGP PUBLIC KEY BLOCK-----\n"
)

# The keyring path a relative dest="docker.gpg" resolves to, pyinfra's
# /etc/apt/keyrings/ convention for signed-by= references.
KEYRING_DEST = "/etc/apt/keyrings/docker.gpg"


async def test_apt_key_yields_upload_then_install():
    """apt.key is non-idempotent: it always reports a change and yields the
    spool transfer followed by the elevated install, reading no fact. A
    relative dest resolves under /etc/apt/keyrings/."""
    host = SimulatedHost(name="h")
    stream = io.BytesIO(GPG_KEY_BYTES)
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.key(ctx, src=stream, dest="docker.gpg")]
    assert ctx.changed
    assert ctx.reason == f"install GPG key from <stream> to {KEYRING_DEST}"
    assert commands == [
        AptKeyUploadCommand(src=stream, dest=_KEY_SPOOL_PATH),
        AptKeyInstallCommand(spool=_KEY_SPOOL_PATH, dest=KEYRING_DEST),
    ]


async def test_apt_key_absolute_dest_used_verbatim():
    """An absolute dest bypasses the keyrings resolution, and an explicit name
    is never rewritten: upstream's .gpg enforcement applies only to names it
    derives from a URL, which the stream form cannot do."""
    host = SimulatedHost(name="h")
    stream = io.BytesIO(GPG_KEY_BYTES)
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in apt.key(ctx, src=stream, dest="/usr/share/keyrings/vendor.asc")
    ]
    assert commands[1] == AptKeyInstallCommand(
        spool=_KEY_SPOOL_PATH, dest="/usr/share/keyrings/vendor.asc"
    )


async def test_apt_key_rejects_a_str_src():
    """pyinfra's apt.key takes a filename/URL the target host reads; reemote5
    takes an IO stream, so a str src raises rather than being treated as a
    controller-side path."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="does not support a filename/URL src"):
        _ = [
            c
            async for c in apt.key(
                ctx, src="https://download.docker.com/linux/debian/gpg", dest="docker.gpg"
            )
        ]


async def test_apt_key_rejects_an_empty_dest():
    """dest is required (an anonymous stream provides no name to derive one
    from), so an empty string raises instead of resolving to the keyrings
    directory itself."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="non-empty dest"):
        _ = [c async for c in apt.key(ctx, src=io.BytesIO(GPG_KEY_BYTES), dest="")]


async def test_apt_key_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors pyinfra's global-arguments slot; op() already strips
    the name/sudo meta-parameters, the rest is ignored."""
    host = SimulatedHost(name="h")
    stream = io.BytesIO(GPG_KEY_BYTES)
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apt.key(ctx, src=stream, dest="docker.gpg", future_flag=True)
    ]
    assert commands == [
        AptKeyUploadCommand(src=stream, dest=_KEY_SPOOL_PATH),
        AptKeyInstallCommand(spool=_KEY_SPOOL_PATH, dest=KEYRING_DEST),
    ]


def test_apt_key_upload_command_renders_as_an_unprivileged_scp():
    """The spool transfer has no shell(); it renders via __str__ and -- being a
    TransferCommand -- is never elevated, even though apt.key's request is
    sudo=True for the install half: the log shows a plain ``scp ...``, never a
    misleading ``sudo scp ...``."""
    stream = io.BytesIO(GPG_KEY_BYTES)
    cmd = AptKeyUploadCommand(src=stream, dest=_KEY_SPOOL_PATH)
    assert not hasattr(cmd, "shell")
    assert str(cmd) == f"scp <stream> -> {_KEY_SPOOL_PATH}"
    assert command_shell(cmd) == f"scp <stream> -> {_KEY_SPOOL_PATH}"
    assert command_shell(cmd, sudo=True) == f"scp <stream> -> {_KEY_SPOOL_PATH}"


def test_apt_key_install_command_shell_and_elevation():
    """The install wraps mkdir, the dearmor-or-copy sniff, chmod and the spool
    cleanup in one ``sh -c`` so every step runs under the elevated shell (a
    bare ``sudo ... && rm`` would leave the cleanup unprivileged). The paths
    are safe tokens, so shlex.quote leaves them unquoted; the armor marker
    carries spaces and is quoted."""
    cmd = AptKeyInstallCommand(spool=_KEY_SPOOL_PATH, dest=KEYRING_DEST)
    shell = cmd.shell()
    assert shell.startswith("sh -c ")
    inner = shlex.split(shell)[2]
    assert inner == (
        "mkdir -p /etc/apt/keyrings && "
        f"if grep -q 'BEGIN PGP PUBLIC KEY BLOCK' {_KEY_SPOOL_PATH}; then "
        f"gpg --batch --dearmor -o {KEYRING_DEST} {_KEY_SPOOL_PATH}; "
        f"else cp {_KEY_SPOOL_PATH} {KEYRING_DEST}; fi && "
        f"chmod 0644 {KEYRING_DEST} && rm -f {_KEY_SPOOL_PATH}"
    )
    assert command_shell(cmd, sudo=True).startswith("sudo sh -c ")


def test_apt_key_install_command_quotes_unsafe_paths():
    """A dest carrying shell metacharacters is quoted everywhere it appears --
    the -o target, the cp destination, the chmod operand and the mkdir
    parent."""
    dest = "/etc/apt/keyrings/my key;rm.gpg"
    cmd = AptKeyInstallCommand(spool=_KEY_SPOOL_PATH, dest=dest)
    inner = shlex.split(cmd.shell())[2]
    quoted = shlex.quote(dest)
    assert f"gpg --batch --dearmor -o {quoted} {_KEY_SPOOL_PATH}" in inner
    assert f"cp {_KEY_SPOOL_PATH} {quoted}" in inner
    assert f"chmod 0644 {quoted}" in inner
    assert "mkdir -p /etc/apt/keyrings" in inner


async def test_apt_key_simulated_seam_spools_then_installs():
    """send_command maps the transfer onto remote_files (via put_file) and the
    install onto the keyring path while clearing the spool file -- the two-step
    effect of one apt.key request. The keyring also lands in existing_files and
    file_stats (root, mode 644), so the file facts stay consistent."""
    host = SimulatedHost(name="h")
    stream = io.BytesIO(GPG_KEY_BYTES)
    await send_command(host, AptKeyUploadCommand(src=stream, dest=_KEY_SPOOL_PATH))
    assert host.remote_files[_KEY_SPOOL_PATH] == GPG_KEY_BYTES
    await send_command(
        host, AptKeyInstallCommand(spool=_KEY_SPOOL_PATH, dest=KEYRING_DEST)
    )
    assert host.remote_files[KEYRING_DEST] == GPG_KEY_BYTES
    assert _KEY_SPOOL_PATH not in host.remote_files
    assert KEYRING_DEST in host.existing_files
    assert host.file_stats[KEYRING_DEST] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(GPG_KEY_BYTES),
    }


async def test_apt_key_end_to_end_installs_over_run():
    """Through run(): the apply phase spools the stream by scp then installs it,
    so the simulated host holds the keyring file and the spool file is gone.
    The stream is one object captured by the deploy closure, so both phases
    pass the identical object and the determinism guard holds. The log shows
    the transfer unprivileged and only the install elevated."""
    stream = io.BytesIO(GPG_KEY_BYTES)

    def key_deploy(host):
        yield op(apt.key, name="Add Docker apt GPG key", src=stream, dest="docker.gpg", sudo=True)

    host = SimulatedHost(name="h")

    def factory(entry):
        return host

    _plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], key_deploy, host_factory=factory
    )
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (
        AptKeyUploadCommand(src=stream, dest=_KEY_SPOOL_PATH),
        AptKeyInstallCommand(spool=_KEY_SPOOL_PATH, dest=KEYRING_DEST),
    )
    assert host.remote_files[KEYRING_DEST] == GPG_KEY_BYTES
    assert _KEY_SPOOL_PATH not in host.remote_files
    log = format_log(report)
    assert f"    $ scp <stream> -> {_KEY_SPOOL_PATH}" in log
    assert "    $ sudo sh -c " in log


async def test_apt_key_dry_run_plans_without_installing():
    """dry=True materializes the plan (non-idempotent -> changed, two commands)
    but sends nothing, so no keyring file and no spool file is written."""
    stream = io.BytesIO(GPG_KEY_BYTES)

    def key_deploy(host):
        yield op(apt.key, name="Add Docker apt GPG key", src=stream, dest="docker.gpg", sudo=True)

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        key_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert len(planned.commands) == 2
    assert host.remote_files == {}
    assert host.existing_files == set()


async def test_apt_key_fresh_stream_per_phase_trips_determinism():
    """The stream is compared by identity, so a deploy that builds a new one per
    invocation diverges between the prepare and apply phases and the guard
    raises -- the reason the example hoists its stream to a module constant."""

    def key_deploy(host):
        yield op(
            apt.key,
            name="Add Docker apt GPG key",
            src=io.BytesIO(GPG_KEY_BYTES),
            dest="docker.gpg",
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], key_deploy)
    with pytest.raises(DeterminismError):
        await run_apply(runtimes, plans=plans)


# --- apt.mark operation (a reemote5 original) ----------------------------------

# The hold/unhold renderings: bare ``apt-mark`` verbs with shell-quoted
# names -- no non-interactive wrapper, since ``apt-mark`` is not ``apt-get``.
HOLD_SHELL = "apt-mark hold docker-ce linux-image-generic"
UNHOLD_SHELL = "apt-mark unhold docker-ce"


def test_apt_mark_command_shell_and_elevation():
    """Both descriptors render the bare ``apt-mark`` verb and elevate as a
    whole under sudo/su; the names are shell-quoted."""
    hold = AptMarkHoldCommand(packages=("docker-ce", "linux-image-generic"))
    unhold = AptMarkUnholdCommand(packages=("docker-ce",))
    assert hold.shell() == HOLD_SHELL
    assert unhold.shell() == UNHOLD_SHELL
    assert command_shell(hold, sudo=True) == f"sudo {HOLD_SHELL}"
    assert command_shell(hold, su=True) == f"su -c {shlex.quote(HOLD_SHELL)}"
    assert command_shell(unhold, sudo=True) == f"sudo {UNHOLD_SHELL}"
    assert command_shell(unhold, su=True) == f"su -c {shlex.quote(UNHOLD_SHELL)}"
    assert AptMarkHoldCommand(packages=("weird pkg",)).shell() == (
        "apt-mark hold 'weird pkg'"
    )


async def test_apt_mark_holds_only_the_unheld_names():
    """held=True yields one hold for only the names not already held, in
    request order; architecture-suffixed names pass through verbatim."""
    host = SimulatedHost(name="h", apt_held_packages=["docker-ce"])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in apt.mark(
            ctx, packages=("docker-ce", "linux-image-generic", "libc6:i386")
        )
    ]
    assert ctx.changed
    assert ctx.reason == "2 package(s) to hold"
    assert commands == [
        AptMarkHoldCommand(packages=("linux-image-generic", "libc6:i386"))
    ]


async def test_apt_mark_hold_noop_when_all_held():
    """Every requested name already held is a no-op with no commands."""
    host = SimulatedHost(
        name="h", apt_held_packages=["docker-ce", "linux-image-generic"]
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in apt.mark(ctx, packages=("docker-ce", "linux-image-generic"))
    ]
    assert not ctx.changed
    assert ctx.reason == "all packages held"
    assert commands == []


async def test_apt_mark_unholds_only_the_held_names():
    """held=False yields one unhold for only the names currently held; an
    unheld name never reaches the command."""
    host = SimulatedHost(name="h", apt_held_packages=["docker-ce", "vim"])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in apt.mark(ctx, packages=("docker-ce", "nginx"), held=False)
    ]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to unhold"
    assert commands == [AptMarkUnholdCommand(packages=("docker-ce",))]


async def test_apt_mark_unhold_noop_when_none_held():
    """No requested name held is a no-op with no commands."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apt.mark(ctx, packages=("docker-ce",), held=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "no held packages to unhold"
    assert commands == []


async def test_apt_mark_accepts_a_single_string():
    """A bare string is one package name (the apt.packages normalization)."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.mark(ctx, packages="docker-ce")]
    assert ctx.changed
    assert commands == [AptMarkHoldCommand(packages=("docker-ce",))]


async def test_apt_mark_empty_request_is_a_noop():
    """An empty packages list requests nothing and reads no fact."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.mark(ctx, packages=[])]
    assert not ctx.changed
    assert ctx.reason == "no packages requested"
    assert commands == []


async def test_apt_mark_absorbs_unknown_kwargs():
    """``**kwargs`` absorbs pyinfra's global arguments."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.mark(ctx, packages=("docker-ce",), future_flag=True)]
    assert commands == [AptMarkHoldCommand(packages=("docker-ce",))]


async def test_apt_mark_simulated_hold_seam_mutates_state():
    """``send_command`` merges the names into ``apt_held_packages`` sorted
    and de-duplicated, and ``AptHeldPackages`` reads the mutation back;
    holding never touches the installed versions."""
    installed = {"docker-ce": ["5:27.0"], "vim": ["2:9.1"]}
    host = SimulatedHost(
        name="h", apt_held_packages=["vim"], apt_packages=dict(installed)
    )
    await send_command(host, AptMarkHoldCommand(packages=("docker-ce", "vim", "libc6:i386")))
    assert host.apt_held_packages == ["docker-ce", "libc6:i386", "vim"]
    assert host.apt_packages == installed
    assert await AptHeldPackages.process(host) == ["docker-ce", "libc6:i386", "vim"]


async def test_apt_mark_simulated_unhold_seam_tolerates_absent():
    """``send_command`` drops the held marks; an unheld name is tolerated
    (the real ``apt-mark unhold`` exits 0 on one)."""
    host = SimulatedHost(name="h", apt_held_packages=["docker-ce", "vim"])
    await send_command(host, AptMarkUnholdCommand(packages=("docker-ce", "nginx")))
    assert host.apt_held_packages == ["vim"]
    assert await AptHeldPackages.process(host) == ["vim"]


async def test_apt_mark_end_to_end_hold_then_noop():
    """Through run(): the same hold request twice -- the prepare phase plans
    both as changes (nothing is sent, the cache persists), the apply phase
    holds on the first and reports the second as a no-op through the fresh
    per-step fact cache."""

    def hold_deploy(host):
        yield op(
            apt.mark,
            name="Hold critical packages",
            packages=("docker-ce", "linux-image-generic"),
            sudo=True,
        )
        yield op(
            apt.mark,
            name="Re-run hold (no-op)",
            packages=("docker-ce", "linux-image-generic"),
            sudo=True,
        )

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], hold_deploy, host_factory=lambda entry: host
    )
    assert plans["h"][0].changed
    assert plans["h"][1].changed
    first, second = report.log
    assert first.changed
    assert first.commands == (
        AptMarkHoldCommand(packages=("docker-ce", "linux-image-generic")),
    )
    assert not second.changed
    assert second.reason == "all packages held"
    assert second.commands == ()
    assert host.apt_held_packages == ["docker-ce", "linux-image-generic"]
    log = format_log(report)
    assert f"sudo {HOLD_SHELL}" in log


async def test_apt_mark_hold_then_unhold_in_one_deploy():
    """The apply phase's fresh per-step fact cache sees the hold before the
    unhold materializes -- even though the prepare phase planned the unhold
    as a no-op against the nothing-held pre-state -- and both land in
    lockstep order, leaving the host as it started."""

    def cycle_deploy(host):
        yield op(apt.mark, name="Hold docker-ce", packages=("docker-ce",), sudo=True)
        yield op(
            apt.mark,
            name="Release docker-ce",
            packages=("docker-ce",),
            held=False,
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], cycle_deploy)
    assert plans["h"][0].changed  # hold: nothing held pre-deploy
    assert not plans["h"][1].changed  # unhold: pre-state holds nothing
    report = await run_apply(runtimes, plans=plans)
    hold, unhold = report.log
    assert hold.changed
    assert hold.commands == (AptMarkHoldCommand(packages=("docker-ce",)),)
    assert unhold.changed
    assert unhold.commands == (AptMarkUnholdCommand(packages=("docker-ce",)),)
    assert host.apt_held_packages == []
    log = format_log(report)
    assert log.index("sudo apt-mark hold docker-ce") < log.index(f"sudo {UNHOLD_SHELL}")


async def test_apt_mark_dry_run_plans_without_holding():
    """dry=True materializes the plan (changed, one hold command) but sends
    nothing, so no name is marked held."""

    def hold_deploy(host):
        yield op(apt.mark, name="Hold docker-ce", packages=("docker-ce",), sudo=True)

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        hold_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert planned.commands == (AptMarkHoldCommand(packages=("docker-ce",)),)
    assert host.apt_held_packages == []


# --- apt.packages operation (pyinfra parity) ---------------------------------

# The non-interactive prefix every pyinfra apt.packages fixture command
# shares (its ``noninteractive_apt`` wrapper, without the dropped ``force``).
APT_GET_PREFIX = (
    "DEBIAN_FRONTEND=noninteractive apt-get -y "
    '-o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" '
)


async def test_apt_packages_installs_an_absent_package():
    """pyinfra's ``add_package.json``: a single absent package yields one
    install whose shell matches the fixture command verbatim."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.packages(ctx, packages=["git"])]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to install"
    assert commands == [AptInstallCommand(packages=("git",))]
    assert commands[0].shell() == f"{APT_GET_PREFIX}install git"


async def test_apt_packages_version_pins_match_exact_versions():
    """pyinfra's ``add_packages.json``: a pinned package is present only at
    that exact version -- ``something=1.0`` installs over the installed 0.9
    while ``another=1.1`` is already satisfied."""
    host = SimulatedHost(
        name="h", apt_packages={"something": ["0.9"], "another": ["1.1"]}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in apt.packages(
            ctx, packages=["git", "python-pip", "something=1.0", "another=1.1"]
        )
    ]
    assert ctx.changed
    assert commands == [AptInstallCommand(packages=("git", "python-pip", "something=1.0"))]
    assert commands[0].shell() == (
        f"{APT_GET_PREFIX}install git python-pip something=1.0"
    )


async def test_apt_packages_no_change_when_all_present():
    """Every requested package installed (pins at their exact versions) is a
    no-op with no commands."""
    host = SimulatedHost(
        name="h", apt_packages={"git": ["2.43"], "something": ["1.0"]}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apt.packages(ctx, packages=["git", "something=1.0"])
    ]
    assert not ctx.changed
    assert ctx.reason == "all packages present"
    assert commands == []


async def test_apt_packages_no_recommends():
    """pyinfra's ``add_package_no_recommends.json``: the flag lands between
    ``install`` and the names."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apt.packages(ctx, packages=["git"], no_recommends=True)
    ]
    assert commands[0].shell() == (
        f"{APT_GET_PREFIX}install --no-install-recommends git"
    )


async def test_apt_packages_allow_downgrades():
    """pyinfra's ``add_package_allow_downgrades.json``: ``--allow-downgrades``
    lands between ``install`` and the names."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apt.packages(ctx, packages=["git"], allow_downgrades=True)
    ]
    assert commands[0].shell() == f"{APT_GET_PREFIX}install --allow-downgrades git"


async def test_apt_packages_extra_install_args():
    """pyinfra's ``install_with_args.json``: the extra argument is appended
    verbatim after the flags, before the names."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in apt.packages(
            ctx, packages=["git"], extra_install_args="--extra-install-arg"
        )
    ]
    assert commands[0].shell() == f"{APT_GET_PREFIX}install --extra-install-arg git"


async def test_apt_packages_removes_installed_packages():
    """pyinfra's ``remove_packages.json``: only the installed names are
    removed, in request order, with the fixture's ``remove`` command."""
    host = SimulatedHost(name="h", apt_packages={"git": [""], "python-pip": [""]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in apt.packages(
            ctx, packages=["git", "python-pip", "another"], present=False
        )
    ]
    assert ctx.changed
    assert ctx.reason == "2 package(s) to remove"
    assert commands == [AptRemoveCommand(packages=("git", "python-pip"))]
    assert commands[0].shell() == f"{APT_GET_PREFIX}remove git python-pip"


async def test_apt_packages_remove_extra_uninstall_args():
    """pyinfra's ``remove_packages_with_args.json``: the extra uninstall
    argument lands between ``remove`` and the names."""
    host = SimulatedHost(name="h", apt_packages={"git": [""]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in apt.packages(
            ctx,
            packages=["git"],
            present=False,
            extra_uninstall_args="--extra-arg",
        )
    ]
    assert commands[0].shell() == f"{APT_GET_PREFIX}remove --extra-arg git"


async def test_apt_packages_purge():
    """pyinfra's ``purge_packages.yaml``: ``purge=True`` switches the verb so
    configuration files go with the package."""
    host = SimulatedHost(name="h", apt_packages={"git": [""], "python-pip": [""]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in apt.packages(
            ctx, packages=["git", "python-pip"], present=False, purge=True
        )
    ]
    assert commands == [AptRemoveCommand(packages=("git", "python-pip"), purge=True)]
    assert commands[0].shell() == f"{APT_GET_PREFIX}purge git python-pip"


async def test_apt_packages_removal_respects_version_pins():
    """A pinned removal only fires when that exact version is installed, and
    passes the pin through verbatim (pyinfra's ``inst_vers``)."""
    host = SimulatedHost(name="h", apt_packages={"something": ["0.9"]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in apt.packages(ctx, packages=["something=1.0"], present=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "no packages to remove"
    assert commands == []
    ctx2 = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands2 = [
        c
        async for c in apt.packages(ctx2, packages=["something=0.9"], present=False)
    ]
    assert ctx2.changed
    assert commands2 == [AptRemoveCommand(packages=("something=0.9",))]


async def test_apt_packages_latest_upgrades_installed_unpinned():
    """pyinfra's ``upgrade_packages.json``: ``latest=True`` installs the
    absent package first, then upgrades the installed unpinned one with a
    second ``install`` command (non-idempotent by design)."""
    host = SimulatedHost(name="h", apt_packages={"git": ["1.0"]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apt.packages(ctx, packages=["git", "another"], latest=True)
    ]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to install; 1 package(s) to upgrade"
    assert commands == [
        AptInstallCommand(packages=("another",)),
        AptInstallCommand(packages=("git",)),
    ]
    assert commands[0].shell() == f"{APT_GET_PREFIX}install another"
    assert commands[1].shell() == f"{APT_GET_PREFIX}install git"


async def test_apt_packages_latest_skips_pins_and_carries_no_extra_args():
    """Pinned packages are never ``latest`` candidates, and the upgrade
    command omits ``extra_install_args`` (pyinfra builds it before appending
    them) while keeping the install flags."""
    host = SimulatedHost(
        name="h", apt_packages={"git": ["1.0"], "something": ["1.0"]}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in apt.packages(
            ctx,
            packages=["git", "something=1.0"],
            latest=True,
            no_recommends=True,
            extra_install_args="--extra-install-arg",
        )
    ]
    assert commands == [
        AptInstallCommand(packages=("git",), no_recommends=True)
    ]
    assert commands[0].shell() == (
        f"{APT_GET_PREFIX}install --no-install-recommends git"
    )


async def test_apt_packages_update_is_stateless():
    """pyinfra's ``update_nocache.json``: ``update=True`` always yields the
    bare ``apt-get update`` -- no non-interactive wrapper, no stamp file."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.packages(ctx, update=True)]
    assert ctx.changed
    assert ctx.reason == "apt update"
    assert commands == [AptUpdateCommand()]
    assert commands[0].shell() == "apt-get update"


async def test_apt_packages_update_wait_for_lock():
    """The lock timeout is inserted between ``apt-get`` and ``update``, the
    form pyinfra's ``apt.update`` renders."""
    assert AptUpdateCommand(wait_for_lock=True).shell() == (
        "apt-get -o DPkg::Lock::Timeout=60 update"
    )
    assert AptUpdateCommand(wait_for_lock=30).shell() == (
        "apt-get -o DPkg::Lock::Timeout=30 update"
    )


async def test_apt_packages_upgrade_runs_when_changes_proposed():
    """Nonzero ``upgrade`` simulation counts yield the upgrade with the
    proposed counts in the reason (the dist_upgrade gating, inlined)."""
    host = SimulatedHost(name="h", apt_simulations={"upgrade": DIST_UPGRADE_CHANGES})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.packages(ctx, upgrade=True)]
    assert ctx.changed
    assert ctx.reason == (
        "upgrade will change packages (10 upgraded, 3 newly installed, "
        "2 to remove, 3 not upgraded)"
    )
    assert commands == [AptUpgradeCommand()]
    assert commands[0].shell() == f"{APT_GET_PREFIX}upgrade"


async def test_apt_packages_upgrade_noop_when_no_changes_proposed():
    """All-zero simulation counts skip the upgrade entirely."""
    host = SimulatedHost(name="h", apt_simulations={"upgrade": SIM_ZERO_FACT})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.packages(ctx, upgrade=True)]
    assert not ctx.changed
    assert ctx.reason == "upgrade skipped, no changes would be performed"
    assert commands == []


async def test_apt_packages_upgrade_runs_anyway_on_failed_simulation():
    """A failed simulation (None -- no seam entry) runs the real upgrade
    anyway, pyinfra's ``not changes`` fallback."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.packages(ctx, upgrade=True)]
    assert ctx.changed
    assert ctx.reason == "upgrade simulation failed, will run anyway"
    assert commands == [AptUpgradeCommand()]


async def test_apt_packages_update_then_upgrade_order():
    """pyinfra's ``update_upgrade.json``: the update runs before the
    upgrade, and the reasons join with a semicolon and space."""
    host = SimulatedHost(name="h", apt_simulations={"upgrade": SIM_ZERO_FACT})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.packages(ctx, update=True, upgrade=True)]
    assert ctx.changed  # the stateless update alone reports a change
    assert ctx.reason == "apt update; upgrade skipped, no changes would be performed"
    assert commands == [AptUpdateCommand()]


async def test_apt_packages_wait_for_lock():
    """pyinfra's ``add_packages_wait_for_lock.yaml``: the 60-second lock
    timeout lands before the dpkg options in the install command."""
    host = SimulatedHost(
        name="h", apt_packages={"something": ["0.9"], "another": ["1.1"]}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in apt.packages(
            ctx,
            packages=["git", "python-pip", "something=1.0", "another=1.1"],
            wait_for_lock=True,
        )
    ]
    assert commands[0].shell() == (
        "DEBIAN_FRONTEND=noninteractive apt-get -y -o DPkg::Lock::Timeout=60 "
        '-o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" '
        "install git python-pip something=1.0"
    )


async def test_apt_packages_cache_time_is_dropped():
    """The developer chose to drop pyinfra's ``cache_time``: the keyword is
    absorbed with no effect -- update stays stateless and no stamp-file
    touch is ever yielded."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.packages(ctx, update=True, cache_time=3600)]
    assert ctx.changed
    assert commands == [AptUpdateCommand()]
    assert host.existing_files == set()


async def test_apt_packages_force_is_dropped():
    """pyinfra's ``add_package_force.json`` renders ``--force-yes``; reemote5
    drops the deprecated flag (the apt.deb decision), absorbing the keyword."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.packages(ctx, packages=["git"], force=True)]
    assert commands == [AptInstallCommand(packages=("git",))]
    assert "--force-yes" not in commands[0].shell()


async def test_apt_packages_none_and_empty_request_nothing():
    """None or an empty list requests nothing: no fact read effect, no
    commands, no change."""
    host = SimulatedHost(name="h")
    for requested in (None, []):
        ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
        commands = [c async for c in apt.packages(ctx, packages=requested)]
        assert not ctx.changed
        assert ctx.reason == "no packages requested"
        assert commands == []


async def test_apt_packages_accepts_a_bare_string():
    """pyinfra accepts a single package name as a bare string."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.packages(ctx, packages="git")]
    assert commands == [AptInstallCommand(packages=("git",))]


async def test_apt_packages_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors pyinfra's global-arguments slot; op() already
    strips the ``name``/``sudo`` meta-parameters, the rest is ignored."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.packages(ctx, packages=["git"], future_flag=True)]
    assert commands == [AptInstallCommand(packages=("git",))]


def test_apt_packages_command_shells_and_elevation():
    """All four descriptors elevate as a whole under sudo/su, and package
    names needing it are shell-quoted."""
    install = AptInstallCommand(packages=("git",))
    assert command_shell(install, sudo=True) == f"sudo {APT_GET_PREFIX}install git"
    assert command_shell(install, su=True) == (
        f"su -c {shlex.quote(f'{APT_GET_PREFIX}install git')}"
    )
    assert AptInstallCommand(packages=("my pkg",)).shell().endswith(
        "install 'my pkg'"
    )
    assert command_shell(AptUpdateCommand(), sudo=True) == "sudo apt-get update"
    upgrade = AptUpgradeCommand()
    assert command_shell(upgrade, sudo=True) == f"sudo {APT_GET_PREFIX}upgrade"
    remove = AptRemoveCommand(packages=("git",))
    assert command_shell(remove, sudo=True) == f"sudo {APT_GET_PREFIX}remove git"


async def test_apt_packages_simulated_seam_install_and_remove():
    """``send_command`` records installs into ``apt_packages`` (pins at their
    exact version, unpinned names with the fixtures' empty-string version
    placeholder)
    and removes them again; a pinned removal drops only that version."""
    host = SimulatedHost(name="h")
    await send_command(host, AptInstallCommand(packages=("git", "something=1.0")))
    assert host.apt_packages == {"git": [""], "something": ["1.0"]}
    await send_command(host, AptInstallCommand(packages=("something=2.0",)))
    assert host.apt_packages["something"] == ["2.0"]
    await send_command(host, AptRemoveCommand(packages=("something=2.0",)))
    assert "something" not in host.apt_packages
    await send_command(host, AptRemoveCommand(packages=("git",)))
    assert host.apt_packages == {}


async def test_apt_packages_simulated_seam_upgrade_and_update():
    """The upgrade arm applies pending candidates and zeroes the ``upgrade``
    simulation; the update arm has no simulated effect."""
    host = SimulatedHost(
        name="h",
        apt_simulations={"upgrade": DIST_UPGRADE_CHANGES},
        apt_packages={"vim": ["2:9.1-1"]},
        apt_upgradable={"vim": {"installed": "2:9.1-1", "candidate": "2:9.1-2"}},
    )
    await send_command(host, AptUpdateCommand())
    assert host.apt_simulations["upgrade"] == DIST_UPGRADE_CHANGES
    await send_command(host, AptUpgradeCommand())
    assert host.apt_packages == {"vim": ["2:9.1-2"]}
    assert host.apt_upgradable == {}
    assert host.apt_simulations["upgrade"] == SIM_ZERO_FACT


async def test_apt_packages_end_to_end_install_then_noop():
    """Through run(): the first apply installs into the simulated state; a
    second run sees the packages present and is a no-op."""

    def packages_deploy(host):
        yield op(
            apt.packages,
            name="Install Asterisk and Vim",
            packages=("asterisk", "vim"),
            update=True,
            sudo=True,
        )

    host = SimulatedHost(name="h")

    def factory(entry):
        return host  # reuse ONE host so the second run sees the first's install

    _plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], packages_deploy, host_factory=factory
    )
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (
        AptUpdateCommand(),
        AptInstallCommand(packages=("asterisk", "vim")),
    )
    assert host.apt_packages == {"asterisk": [""], "vim": [""]}
    log = format_log(report)
    assert "sudo apt-get update" in log
    assert f"sudo {APT_GET_PREFIX}install asterisk vim" in log

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], packages_deploy, host_factory=factory
    )
    (entry2,) = report2.log
    # update stays stateless (always changed); the install half converges.
    assert entry2.changed
    assert entry2.reason == "apt update; all packages present"
    assert entry2.commands == (AptUpdateCommand(),)


async def test_apt_packages_pinned_end_to_end_converges():
    """A pinned install records exactly that version, so a repeat run with
    no stateless options is a full no-op."""

    def pin_deploy(host):
        yield op(apt.packages, name="Pin something", packages=("something=1.0",), sudo=True)

    host = SimulatedHost(name="h")

    def factory(entry):
        return host

    _plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], pin_deploy, host_factory=factory
    )
    (entry,) = report.log
    assert entry.changed
    assert host.apt_packages == {"something": ["1.0"]}

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], pin_deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert not entry2.changed
    assert entry2.reason == "all packages present"
    assert entry2.commands == ()


async def test_apt_packages_upgrade_end_to_end_then_noop():
    """Through run(): the first apply upgrades the simulated state and zeroes
    the ``upgrade`` simulation; a second run is a no-op."""

    def upgrade_deploy(host):
        yield op(apt.packages, name="Upgrade all packages", upgrade=True, sudo=True)

    host = SimulatedHost(
        name="h",
        apt_simulations={"upgrade": DIST_UPGRADE_CHANGES},
        apt_upgradable={"vim": {"installed": "2:9.1-1", "candidate": "2:9.1-2"}},
    )

    def factory(entry):
        return host

    _plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], upgrade_deploy, host_factory=factory
    )
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (AptUpgradeCommand(),)
    assert host.apt_packages == {"vim": ["2:9.1-2"]}
    log = format_log(report)
    assert f"sudo {APT_GET_PREFIX}upgrade" in log

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], upgrade_deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert not entry2.changed
    assert entry2.reason == "upgrade skipped, no changes would be performed"


async def test_apt_packages_dry_run_sends_nothing():
    """dry=True materializes the plan (changed, one install) but mutates no
    simulated state."""

    def packages_deploy(host):
        yield op(apt.packages, name="Install git", packages=("git",), sudo=True)

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        packages_deploy,
        dry=True,
        host_factory=lambda entry: host,
    )
    assert report is None
    assert plans["h"][0].changed
    assert plans["h"][0].commands == (AptInstallCommand(packages=("git",)),)
    assert host.apt_packages == {}


async def test_apt_packages_remove_after_install_in_one_deploy():
    """The apply phase's fresh per-step fact cache sees the install, so a
    following present=False removes it -- while the prepare plan (which
    never sends) still shows the removal as a no-op."""

    def packages_deploy(host):
        yield op(apt.packages, name="install", packages=("git",))
        yield op(apt.packages, name="remove", packages=("git",), present=False)

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], packages_deploy)
    assert plans["h"][0].changed  # install: absent during prepare
    assert not plans["h"][1].changed  # remove: still absent during prepare
    report = await run_apply(runtimes, plans=plans)
    install, remove = report.log
    assert install.changed
    assert remove.changed
    assert remove.reason == "1 package(s) to remove"
    assert host.apt_packages == {}


# --- apt.update / apt.upgrade operations (pyinfra parity) ----------------------

# The ``commands`` field of pyinfra's ``apt.upgrade/upgrade_wait_for_lock.yaml``
# fixture: the 60-second lock timeout inserted before the dpkg options.
UPGRADE_LOCK_SHELL = (
    "DEBIAN_FRONTEND=noninteractive apt-get -y -o DPkg::Lock::Timeout=60 "
    '-o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" '
    "upgrade"
)


async def test_apt_update_always_yields_bare_update():
    """Non-idempotent like pyinfra's ``apt.update``: always changed, always
    the bare ``apt-get update`` -- no non-interactive wrapper, no fact read."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.update(ctx)]
    assert ctx.changed
    assert ctx.reason == "apt update"
    assert commands == [AptUpdateCommand()]
    assert commands[0].shell() == "apt-get update"


async def test_apt_update_wait_for_lock():
    """pyinfra's ``apt.update/update_wait_for_lock.yaml``: an int sets that
    many seconds; ``True`` maps to the 60-second default."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.update(ctx, wait_for_lock=120)]
    assert commands == [AptUpdateCommand(wait_for_lock=120)]
    assert commands[0].shell() == "apt-get -o DPkg::Lock::Timeout=120 update"
    ctx60 = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands60 = [c async for c in apt.update(ctx60, wait_for_lock=True)]
    assert commands60[0].shell() == "apt-get -o DPkg::Lock::Timeout=60 update"


async def test_apt_update_cache_time_is_dropped():
    """The ``apt.packages`` decision applies to the standalone form too:
    ``cache_time`` is absorbed with no effect -- the update stays stateless
    and no stamp-file touch is ever yielded."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.update(ctx, cache_time=3600)]
    assert ctx.changed
    assert commands == [AptUpdateCommand()]
    assert host.existing_files == set()


async def test_apt_update_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors pyinfra's global-arguments slot; op() already
    strips the ``name``/``sudo`` meta-parameters, the rest is ignored."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.update(ctx, future_flag=True)]
    assert commands == [AptUpdateCommand()]


def test_apt_update_command_shell_and_elevation():
    """The shared descriptor elevates as a whole under sudo/su."""
    assert command_shell(AptUpdateCommand(), sudo=True) == "sudo apt-get update"
    assert command_shell(AptUpdateCommand(), su=True) == (
        f"su -c {shlex.quote('apt-get update')}"
    )


async def test_apt_update_simulated_seam_is_a_noop():
    """The update arm is recognised but tracks no package-list metadata:
    sending it mutates no simulated state."""
    host = SimulatedHost(name="h", apt_simulations={"upgrade": SIM_UPGRADE_FACT})
    await send_command(host, AptUpdateCommand())
    assert host.apt_simulations["upgrade"] == SIM_UPGRADE_FACT
    assert host.apt_packages == {}


async def test_apt_update_end_to_end_stays_non_idempotent():
    """Through run(): the elevated update lands in the log, and a second
    run still reports a change -- there is no state to converge on."""

    def update_deploy(host):
        yield op(apt.update, name="Update apt repositories", sudo=True)

    host = SimulatedHost(name="h")

    def factory(entry):
        return host  # reuse ONE host across both runs

    _plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], update_deploy, host_factory=factory
    )
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (AptUpdateCommand(),)
    assert "$ sudo apt-get update" in format_log(report)

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], update_deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert entry2.changed
    assert entry2.reason == "apt update"


async def test_apt_update_dry_run_sends_nothing():
    """dry=True materializes the plan (changed, one command) but sends
    nothing -- report is None."""

    def update_deploy(host):
        yield op(apt.update, name="Update apt repositories", sudo=True)

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        update_deploy,
        dry=True,
        host_factory=lambda entry: host,
    )
    assert report is None
    assert plans["h"][0].changed
    assert plans["h"][0].commands == (AptUpdateCommand(),)


async def test_apt_upgrade_runs_when_changes_proposed():
    """pyinfra's ``apt.upgrade/upgrade.json``: fifteen proposed upgrades
    yield the non-interactive upgrade verbatim, with the counts in the
    reason."""
    host = SimulatedHost(name="h", apt_simulations={"upgrade": SIM_UPGRADE_FACT})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.upgrade(ctx)]
    assert ctx.changed
    assert ctx.reason == (
        "upgrade will change packages (15 upgraded, 0 newly installed, "
        "0 to remove, 0 not upgraded)"
    )
    assert commands == [AptUpgradeCommand()]
    assert commands[0].shell() == f"{APT_GET_PREFIX}upgrade"


async def test_apt_upgrade_noop_when_no_changes_proposed():
    """All-zero counts are pyinfra's noop: nothing yields and the reason
    mirrors its noop_description verbatim."""
    host = SimulatedHost(name="h", apt_simulations={"upgrade": SIM_ZERO_FACT})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.upgrade(ctx)]
    assert not ctx.changed
    assert ctx.reason == "upgrade skipped, no changes would be performed"
    assert commands == []


async def test_apt_upgrade_runs_anyway_on_failed_simulation():
    """A failed simulation (None -- no seam entry, no summary line) runs
    the real command anyway, pyinfra's ``not changes`` fallback."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.upgrade(ctx)]
    assert ctx.changed
    assert ctx.reason == "upgrade simulation failed, will run anyway"
    assert commands == [AptUpgradeCommand()]


async def test_apt_upgrade_counts_not_upgraded_too():
    """Only ``not_upgraded`` nonzero still counts as a proposed change --
    pyinfra's condition checks all four counts."""
    simulation = {**SIM_ZERO_FACT, "not_upgraded": 3}
    host = SimulatedHost(name="h", apt_simulations={"upgrade": simulation})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.upgrade(ctx)]
    assert ctx.changed
    assert commands == [AptUpgradeCommand()]


async def test_apt_upgrade_wait_for_lock():
    """wait_for_lock=True threads the 60-second timeout (pyinfra's
    ``upgrade_wait_for_lock.yaml``); an int sets that many seconds."""
    host = SimulatedHost(name="h", apt_simulations={"upgrade": SIM_UPGRADE_FACT})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.upgrade(ctx, wait_for_lock=True)]
    assert commands == [AptUpgradeCommand(wait_for_lock=True)]
    assert commands[0].shell() == UPGRADE_LOCK_SHELL
    ctx30 = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands30 = [c async for c in apt.upgrade(ctx30, wait_for_lock=30)]
    assert "-o DPkg::Lock::Timeout=30" in commands30[0].shell()


async def test_apt_upgrade_auto_remove_is_dropped():
    """The developer chose to drop pyinfra's ``auto_remove`` in favour of
    the standalone ``apt.auto_remove`` (the dist_upgrade split): the
    keyword is absorbed and ``--autoremove`` never renders."""
    host = SimulatedHost(name="h", apt_simulations={"upgrade": SIM_UPGRADE_FACT})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.upgrade(ctx, auto_remove=True)]
    assert commands == [AptUpgradeCommand()]
    assert "--autoremove" not in commands[0].shell()


async def test_apt_upgrade_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors pyinfra's global-arguments slot; op() already
    strips the ``name``/``sudo`` meta-parameters, the rest is ignored."""
    host = SimulatedHost(name="h", apt_simulations={"upgrade": SIM_UPGRADE_FACT})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.upgrade(ctx, future_flag=True)]
    assert commands == [AptUpgradeCommand()]


def test_apt_upgrade_command_shell_and_elevation():
    """The shared descriptor renders pyinfra's upgrade.json command and
    elevates as a whole under sudo/su."""
    upgrade = AptUpgradeCommand()
    assert upgrade.shell() == f"{APT_GET_PREFIX}upgrade"
    assert command_shell(upgrade, sudo=True) == f"sudo {APT_GET_PREFIX}upgrade"
    assert command_shell(upgrade, su=True) == (
        f"su -c {shlex.quote(f'{APT_GET_PREFIX}upgrade')}"
    )


async def test_apt_upgrade_simulated_seam_mutates_state():
    """``send_command`` applies the pending upgrades into ``apt_packages``,
    clears ``apt_upgradable`` and zeroes the upgrade simulation -- the same
    arm ``apt.packages(upgrade=True)`` uses."""
    host = SimulatedHost(
        name="h",
        apt_simulations={"upgrade": SIM_UPGRADE_FACT},
        apt_packages={"vim": ["2:9.1-1"]},
        apt_upgradable={"vim": {"installed": "2:9.1-1", "candidate": "2:9.1-2"}},
    )
    await send_command(host, AptUpgradeCommand())
    assert host.apt_packages == {"vim": ["2:9.1-2"]}
    assert host.apt_upgradable == {}
    assert host.apt_simulations["upgrade"] == SIM_ZERO_FACT


async def test_apt_upgrade_end_to_end_upgrade_then_noop():
    """Through run(): the first apply upgrades the simulated state; a
    second run sees the zeroed simulation and is a no-op."""

    def upgrade_deploy(host):
        yield op(apt.upgrade, name="Upgrade apt packages", sudo=True)

    host = SimulatedHost(
        name="h",
        apt_simulations={"upgrade": SIM_UPGRADE_FACT},
        apt_upgradable={"vim": {"installed": "2:9.1-1", "candidate": "2:9.1-2"}},
    )

    def factory(entry):
        return host  # reuse ONE host so the second run sees the first's upgrade

    _plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], upgrade_deploy, host_factory=factory
    )
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (AptUpgradeCommand(),)
    assert host.apt_packages == {"vim": ["2:9.1-2"]}
    assert f"$ sudo {APT_GET_PREFIX}upgrade" in format_log(report)

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], upgrade_deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert not entry2.changed
    assert entry2.reason == "upgrade skipped, no changes would be performed"


async def test_apt_update_then_upgrade_in_one_deploy():
    """The classic patch pair composes in lockstep: the update lands first,
    then the upgrade, each elevated as its own request."""

    def patch_deploy(host):
        yield op(apt.update, name="Update apt repositories", sudo=True)
        yield op(apt.upgrade, name="Upgrade apt packages", sudo=True)

    host = SimulatedHost(
        name="h",
        apt_simulations={"upgrade": SIM_UPGRADE_FACT},
        apt_upgradable={"vim": {"installed": "2:9.1-1", "candidate": "2:9.1-2"}},
    )
    _plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        patch_deploy,
        host_factory=lambda entry: host,
    )
    update_entry, upgrade_entry = report.log
    assert update_entry.commands == (AptUpdateCommand(),)
    assert upgrade_entry.commands == (AptUpgradeCommand(),)
    assert host.apt_packages == {"vim": ["2:9.1-2"]}
    log = format_log(report)
    assert log.index("$ sudo apt-get update") < log.index(
        f"$ sudo {APT_GET_PREFIX}upgrade"
    )


async def test_apt_upgrade_dry_run_sends_nothing():
    """dry=True materializes the plan (changed, one command) but mutates no
    simulated state -- the upgrade is only planned, never sent."""

    def upgrade_deploy(host):
        yield op(apt.upgrade, name="Upgrade apt packages", sudo=True)

    host = SimulatedHost(name="h", apt_simulations={"upgrade": SIM_UPGRADE_FACT})
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        upgrade_deploy,
        dry=True,
        host_factory=lambda entry: host,
    )
    assert report is None
    assert plans["h"][0].changed
    assert plans["h"][0].commands == (AptUpgradeCommand(),)
    assert host.apt_simulations["upgrade"] == SIM_UPGRADE_FACT


# --- apt.ppa operation (pyinfra parity) ---------------------------------------

# pyinfra's apt.ppa/add.json renders ``apt-add-repository -y ppa:some-repo``;
# the Bitcoin PPA is pyinfra's documented example. ``shlex.quote`` leaves the
# safe ``ppa:bitcoin/bitcoin`` token unquoted.
PPA_SRC = "ppa:bitcoin/bitcoin"
PPA_URL = "http://ppa.launchpad.net/bitcoin/bitcoin/ubuntu"
PPA_ADD_SHELL = "apt-add-repository -y ppa:bitcoin/bitcoin"
PPA_REMOVE_SHELL = "apt-add-repository -y --remove ppa:bitcoin/bitcoin"
# The AptSource the simulated add appends (codename from the lsb_release seam).
PPA_SOURCE = {
    "type": "deb",
    "url": PPA_URL,
    "distribution": "noble",
    "components": ["main"],
    "options": {},
}


def test_apt_ppa_command_shell_matches_pyinfra():
    """The descriptor renders pyinfra's add/remove commands and shell-quotes the
    src (pyinfra's add_quotes_injection.json), elevating as a whole."""
    assert AptPpaCommand(src=PPA_SRC).shell() == PPA_ADD_SHELL
    assert AptPpaCommand(src=PPA_SRC, present=False).shell() == PPA_REMOVE_SHELL
    # pyinfra's add_quotes_injection.json: 'x"; reboot #' stays one quoted token.
    assert AptPpaCommand(src='x"; reboot #').shell() == (
        "apt-add-repository -y 'x\"; reboot #'"
    )
    assert command_shell(AptPpaCommand(src=PPA_SRC), sudo=True) == (
        f"sudo {PPA_ADD_SHELL}"
    )
    assert command_shell(AptPpaCommand(src=PPA_SRC), su=True) == (
        f"su -c '{PPA_ADD_SHELL}'"
    )


async def test_apt_ppa_add_always_yields():
    """present=True always reports a change and yields one add command."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.ppa(ctx, src=PPA_SRC)]
    assert ctx.changed
    assert ctx.reason == f"ppa {PPA_SRC} will be added"
    assert commands == [AptPpaCommand(src=PPA_SRC)]


async def test_apt_ppa_remove_always_yields():
    """present=False always reports a change and yields one --remove command
    (pyinfra's remove.json)."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.ppa(ctx, src=PPA_SRC, present=False)]
    assert ctx.changed
    assert ctx.reason == f"ppa {PPA_SRC} will be removed"
    assert commands == [AptPpaCommand(src=PPA_SRC, present=False)]


async def test_apt_ppa_is_non_idempotent():
    """Even with the PPA already configured, the operation reads no fact and
    still yields -- pyinfra marks apt.ppa non-idempotent."""
    host = SimulatedHost(name="h", apt_sources=[dict(PPA_SOURCE)])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.ppa(ctx, src=PPA_SRC)]
    assert ctx.changed
    assert commands == [AptPpaCommand(src=PPA_SRC)]


async def test_apt_ppa_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors pyinfra's global-arguments slot; op() already strips
    the ``name``/``sudo`` meta-parameters, the rest is ignored."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.ppa(ctx, src=PPA_SRC, future_flag=True)]
    assert commands == [AptPpaCommand(src=PPA_SRC)]


async def test_apt_ppa_simulated_seam_add_and_remove():
    """``send_command`` appends the PPA's Launchpad source to ``apt_sources``
    (codename from lsb_release), a repeat add is a no-op, and the removal drops
    it -- tolerating an absent PPA."""
    host = SimulatedHost(name="h", lsb_release={"Codename": "noble"})
    await send_command(host, AptPpaCommand(src=PPA_SRC))
    assert host.apt_sources == [PPA_SOURCE]
    # A repeat add of the same URL adds nothing.
    await send_command(host, AptPpaCommand(src=PPA_SRC))
    assert host.apt_sources == [PPA_SOURCE]
    await send_command(host, AptPpaCommand(src=PPA_SRC, present=False))
    assert host.apt_sources == []
    # Removing an absent PPA is tolerated (the real command would exit non-zero).
    await send_command(host, AptPpaCommand(src=PPA_SRC, present=False))
    assert host.apt_sources == []


async def test_apt_ppa_seam_default_codename():
    """With no lsb_release codename seeded, the simulated source's distribution
    is the empty string (the simulation models no release detection)."""
    host = SimulatedHost(name="h")
    await send_command(host, AptPpaCommand(src=PPA_SRC))
    assert host.apt_sources == [{**PPA_SOURCE, "distribution": ""}]


async def test_apt_ppa_end_to_end_over_run():
    """Through run(): the add elevates under sudo in the log and mutates the
    simulated sources; being non-idempotent, a second run still changes."""

    def ppa_deploy(host):
        yield op(apt.ppa, name="Add the Bitcoin ppa", src=PPA_SRC, sudo=True)

    host = SimulatedHost(name="h", lsb_release={"Codename": "noble"})

    def factory(entry):
        return host

    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], ppa_deploy, host_factory=factory
    )
    assert plans["h"][0].changed
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (AptPpaCommand(src=PPA_SRC),)
    assert host.apt_sources == [PPA_SOURCE]
    log = format_log(report)
    assert f"$ sudo {PPA_ADD_SHELL}" in log

    # Non-idempotent: a second run reports a change again (no fact convergence).
    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], ppa_deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert entry2.changed


async def test_apt_ppa_dry_run_sends_nothing():
    """dry=True materializes the plan (changed, one command) but mutates no
    simulated state."""

    def ppa_deploy(host):
        yield op(apt.ppa, name="Add the Bitcoin ppa", src=PPA_SRC, sudo=True)

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        ppa_deploy,
        dry=True,
        host_factory=lambda entry: host,
    )
    assert report is None
    assert plans["h"][0].changed
    assert plans["h"][0].commands == (AptPpaCommand(src=PPA_SRC),)
    assert host.apt_sources == []


async def test_apt_ppa_add_then_remove_in_one_deploy():
    """Both directions are non-idempotent, so the prepare plan and the apply log
    show both as changes; the apply leaves the simulated sources empty."""

    def ppa_deploy(host):
        yield op(apt.ppa, name="add", src=PPA_SRC, sudo=True)
        yield op(apt.ppa, name="remove", src=PPA_SRC, present=False, sudo=True)

    host = SimulatedHost(name="h", lsb_release={"Codename": "noble"})
    runtimes, plans = await run_prepare([host], ppa_deploy)
    assert plans["h"][0].changed
    assert plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    add, remove = report.log
    assert add.changed
    assert remove.changed
    assert remove.reason == f"ppa {PPA_SRC} will be removed"
    assert host.apt_sources == []


# --- apt.repo operation (pyinfra parity) ---------------------------------------

# pyinfra's apt.repo fixtures all use the Canonical partner repo line; the
# AptSource below is their ``apt.AptSources`` fact entry in reemote5's shape
# (options always list-valued -- here empty).
REPO_SRC = "deb http://archive.canonical.com/ubuntu hardy partner"
REPO_SOURCE = {
    "type": "deb",
    "url": "http://archive.canonical.com/ubuntu",
    "distribution": "hardy",
    "components": ["partner"],
    "options": {},
}
SOURCES_LIST = "/etc/apt/sources.list"
# The add.json fixture's command -- the inner script of the sh -c wrapper.
REPO_ADD_INNER = f"echo '{REPO_SRC}' >> {SOURCES_LIST}"
# The remove.json fixture's sed script, verbatim (escaped dots and slashes).
REPO_REMOVE_SCRIPT = r"/^.*deb http:\/\/archive\.canonical\.com\/ubuntu hardy partner.*$/d"


def test_apt_repo_command_shells_match_pyinfra():
    """The append's inner script and the removal's sed script are pyinfra's
    fixture commands; the deviations are the sh -c wrapper (elevation) and
    the bare -i (no timestamped backup dance)."""
    add = AptRepoAddCommand(path=SOURCES_LIST, line=REPO_SRC)
    # The outer shlex quoting escapes inner quotes, so assert on the
    # recovered inner script, never a raw substring.
    assert shlex.split(add.shell())[2] == REPO_ADD_INNER
    assert add.shell() == f"sh -c {shlex.quote(REPO_ADD_INNER)}"
    remove = AptRepoRemoveCommand(path=SOURCES_LIST, line=REPO_SRC)
    assert remove.shell() == f"sed -i '{REPO_REMOVE_SCRIPT}' {SOURCES_LIST}"
    # Elevation applies to the whole descriptor.
    assert command_shell(add, sudo=True) == f"sudo {add.shell()}"
    assert command_shell(remove, sudo=True) == f"sudo {remove.shell()}"
    assert command_shell(add, su=True) == f"su -c {shlex.quote(add.shell())}"


async def test_apt_repo_add_when_absent():
    """pyinfra's add.json: an unconfigured repo yields one append to
    /etc/apt/sources.list."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.repo(ctx, src=REPO_SRC)]
    assert ctx.changed
    assert ctx.reason == f'apt repo "{REPO_SRC}" missing, will be added to {SOURCES_LIST}'
    assert commands == [AptRepoAddCommand(path=SOURCES_LIST, line=REPO_SRC)]


async def test_apt_repo_add_with_filename():
    """pyinfra's add_filename.json: filename targets
    /etc/apt/sources.list.d/<filename>.list."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.repo(ctx, src=REPO_SRC, filename="somefile")]
    assert ctx.changed
    assert commands == [
        AptRepoAddCommand(path="/etc/apt/sources.list.d/somefile.list", line=REPO_SRC)
    ]


async def test_apt_repo_add_noop_when_present():
    """pyinfra's add_existing.json: a configured repo is a no-op with
    pyinfra's noop_description verbatim."""
    host = SimulatedHost(name="h", apt_sources=[dict(REPO_SOURCE)])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.repo(ctx, src=REPO_SRC)]
    assert not ctx.changed
    assert ctx.reason == f'apt repo "{REPO_SRC}" exists'
    assert commands == []


async def test_apt_repo_remove_when_present():
    """pyinfra's remove.json: a configured repo with present=False yields
    one sed deletion."""
    host = SimulatedHost(name="h", apt_sources=[dict(REPO_SOURCE)])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.repo(ctx, src=REPO_SRC, present=False)]
    assert ctx.changed
    assert ctx.reason == f'apt repo "{REPO_SRC}" present, will be removed from {SOURCES_LIST}'
    assert commands == [AptRepoRemoveCommand(path=SOURCES_LIST, line=REPO_SRC)]


async def test_apt_repo_remove_noop_when_absent():
    """present=False for an unconfigured repo is a no-op with pyinfra's
    noop_description verbatim."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.repo(ctx, src=REPO_SRC, present=False)]
    assert not ctx.changed
    assert ctx.reason == f'apt repo "{REPO_SRC}" does not exist'
    assert commands == []


async def test_apt_repo_options_match_with_list_valued_shape():
    """A src with bracketed options matches the fact's list-valued options
    (the AptSource shape deviation): equal -> noop, different -> add."""
    src = "deb [arch=amd64 signed-by=/etc/apt/keyrings/vendor.gpg] https://example.com/apt noble main"
    entry = {
        "type": "deb",
        "url": "https://example.com/apt",
        "distribution": "noble",
        "components": ["main"],
        "options": {"arch": ["amd64"], "signed-by": ["/etc/apt/keyrings/vendor.gpg"]},
    }
    host = SimulatedHost(name="h", apt_sources=[entry])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.repo(ctx, src=src)]
    assert not ctx.changed
    assert commands == []
    # A different arch is a different repository -> the line is appended.
    other = src.replace("arch=amd64", "arch=arm64")
    ctx2 = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands2 = [c async for c in apt.repo(ctx2, src=other)]
    assert ctx2.changed
    assert commands2 == [AptRepoAddCommand(path=SOURCES_LIST, line=other)]


async def test_apt_repo_unparseable_src_is_tolerated():
    """pyinfra's tolerance: a src its regex cannot parse counts as absent,
    so present=True still appends it and present=False is a no-op."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.repo(ctx, src="not-a-repo-line")]
    assert ctx.changed
    assert commands == [AptRepoAddCommand(path=SOURCES_LIST, line="not-a-repo-line")]
    ctx2 = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands2 = [c async for c in apt.repo(ctx2, src="not-a-repo-line", present=False)]
    assert not ctx2.changed
    assert commands2 == []


async def test_apt_repo_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors pyinfra's global-arguments slot; op() already
    strips the ``name``/``sudo`` meta-parameters, the rest is ignored."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.repo(ctx, src=REPO_SRC, future_flag=True)]
    assert commands == [AptRepoAddCommand(path=SOURCES_LIST, line=REPO_SRC)]


async def test_apt_repo_simulated_seam_add():
    """``send_command`` appends the line to remote_files (plus existence
    and stats) and the parsed entry to apt_sources; a repeat append adds
    a second file line (echo always appends) but no duplicate entry."""
    host = SimulatedHost(name="h")
    await send_command(host, AptRepoAddCommand(path=SOURCES_LIST, line=REPO_SRC))
    assert host.remote_files[SOURCES_LIST] == f"{REPO_SRC}\n".encode()
    assert SOURCES_LIST in host.existing_files
    assert host.file_stats[SOURCES_LIST]["size"] == len(f"{REPO_SRC}\n")
    assert host.apt_sources == [REPO_SOURCE]
    await send_command(host, AptRepoAddCommand(path=SOURCES_LIST, line=REPO_SRC))
    assert host.remote_files[SOURCES_LIST] == f"{REPO_SRC}\n{REPO_SRC}\n".encode()
    assert host.apt_sources == [REPO_SOURCE]


async def test_apt_repo_simulated_seam_remove():
    """The removal deletes the matching line (surrounding text included,
    the anchored ^.*line.*$ pattern), keeps other lines, drops the
    apt_sources entry and tolerates an absent path."""
    other = "deb http://archive.ubuntu.com/ubuntu noble main"
    host = SimulatedHost(
        name="h",
        apt_sources=[dict(REPO_SOURCE)],
        remote_files={SOURCES_LIST: f"{other}\n{REPO_SRC} # partner\n".encode()},
    )
    await send_command(host, AptRepoRemoveCommand(path=SOURCES_LIST, line=REPO_SRC))
    assert host.remote_files[SOURCES_LIST] == f"{other}\n".encode()
    assert host.apt_sources == []
    # A second removal changes nothing (sed exits 0 deleting nothing).
    await send_command(host, AptRepoRemoveCommand(path=SOURCES_LIST, line=REPO_SRC))
    assert host.remote_files[SOURCES_LIST] == f"{other}\n".encode()
    # An absent path is tolerated too.
    await send_command(
        host, AptRepoRemoveCommand(path="/etc/apt/sources.list.d/gone.list", line=REPO_SRC)
    )


async def test_apt_repo_end_to_end_add_then_noop():
    """Through run(): the append elevates under sudo in the log and
    mutates the simulated sources; a second run is a no-op."""

    def repo_deploy(host):
        yield op(apt.repo, name="Add the partner repo", src=REPO_SRC, sudo=True)

    host = SimulatedHost(name="h")

    def factory(entry):
        return host

    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], repo_deploy, host_factory=factory
    )
    assert plans["h"][0].changed
    (entry,) = report.log
    assert entry.changed
    add = AptRepoAddCommand(path=SOURCES_LIST, line=REPO_SRC)
    assert entry.commands == (add,)
    assert host.apt_sources == [REPO_SOURCE]
    assert f"$ sudo {add.shell()}" in format_log(report)

    # Idempotent: the fresh fact read sees the appended entry -> no change.
    plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], repo_deploy, host_factory=factory
    )
    assert not plans2["h"][0].changed
    (entry2,) = report2.log
    assert not entry2.changed
    assert entry2.reason == f'apt repo "{REPO_SRC}" exists'


async def test_apt_repo_end_to_end_filename_then_remove():
    """A filename-scoped add writes the .list file; a later present=False
    run empties it and drops the sources entry."""
    path = "/etc/apt/sources.list.d/somefile.list"

    def add_deploy(host):
        yield op(apt.repo, name="add", src=REPO_SRC, filename="somefile", sudo=True)

    def remove_deploy(host):
        yield op(
            apt.repo,
            name="remove",
            src=REPO_SRC,
            filename="somefile",
            present=False,
            sudo=True,
        )

    host = SimulatedHost(name="h")

    def factory(entry):
        return host

    _plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], add_deploy, host_factory=factory
    )
    assert report.log[0].changed
    assert host.remote_files[path] == f"{REPO_SRC}\n".encode()

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], remove_deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert entry2.changed
    remove = AptRepoRemoveCommand(path=path, line=REPO_SRC)
    assert entry2.commands == (remove,)
    assert f"$ sudo {remove.shell()}" in format_log(report2)
    assert host.remote_files[path] == b""
    assert host.apt_sources == []


async def test_apt_repo_dry_run_sends_nothing():
    """dry=True materializes the plan (changed, one command) but mutates no
    simulated state."""

    def repo_deploy(host):
        yield op(apt.repo, name="Add the partner repo", src=REPO_SRC, sudo=True)

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        repo_deploy,
        dry=True,
        host_factory=lambda entry: host,
    )
    assert report is None
    assert plans["h"][0].changed
    assert plans["h"][0].commands == (AptRepoAddCommand(path=SOURCES_LIST, line=REPO_SRC),)
    assert host.apt_sources == []
    assert host.remote_files == {}


async def test_apt_repo_repeat_add_in_one_deploy_apply_noop():
    """Two identical adds: the prepare plan shows both changed (its fact
    cache persists), but the apply log shows the second as a no-op -- the
    per-step fresh cache sees the first append."""

    def repo_deploy(host):
        yield op(apt.repo, name="add once", src=REPO_SRC, sudo=True)
        yield op(apt.repo, name="add twice", src=REPO_SRC, sudo=True)

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], repo_deploy)
    assert plans["h"][0].changed
    assert plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    first, second = report.log
    assert first.changed
    assert not second.changed
    assert second.reason == f'apt repo "{REPO_SRC}" exists'
    # Only the first append ran, so the file holds exactly one line.
    assert host.remote_files[SOURCES_LIST] == f"{REPO_SRC}\n".encode()
    assert host.apt_sources == [REPO_SOURCE]


async def test_apt_repo_add_then_remove_in_one_deploy():
    """Add then remove in one deploy: the apply's fresh facts see the
    appended entry, so the removal fires and leaves the file empty."""

    def repo_deploy(host):
        yield op(apt.repo, name="add", src=REPO_SRC, sudo=True)
        yield op(apt.repo, name="remove", src=REPO_SRC, present=False, sudo=True)

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], repo_deploy)
    assert plans["h"][0].changed
    # Prepare never sends, so the removal sees the pre-deploy absence.
    assert not plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    add, remove = report.log
    assert add.changed
    assert remove.changed
    assert remove.reason == f'apt repo "{REPO_SRC}" present, will be removed from {SOURCES_LIST}'
    assert host.remote_files[SOURCES_LIST] == b""
    assert host.apt_sources == []


# --- apt.sources_file operation (pyinfra parity) -------------------------------

# pyinfra's create.json kwargs: the Docker deb822 repository from its docstring
# example. The content below is the fixture's upload payload verbatim and the
# source entry its apt.AptSources fact holds in reemote5's list-valued shape.
DOCKER_KWARGS = {
    "filename": "docker",
    "types": ["deb"],
    "uris": ["https://download.docker.com/linux/debian"],
    "suites": ["bookworm"],
    "components": ["stable"],
    "architectures": ["amd64"],
    "signed_by": "/etc/apt/keyrings/docker.gpg",
}
DOCKER_DEST = "/etc/apt/sources.list.d/docker.sources"
DOCKER_CONTENT = (
    "Types: deb\n"
    "URIs: https://download.docker.com/linux/debian\n"
    "Suites: bookworm\n"
    "Components: stable\n"
    "Architectures: amd64\n"
    "Signed-By: /etc/apt/keyrings/docker.gpg\n"
)
DOCKER_SOURCE = {
    "type": "deb",
    "url": "https://download.docker.com/linux/debian",
    "distribution": "bookworm",
    "components": ["stable"],
    "options": {"arch": ["amd64"], "signed-by": ["/etc/apt/keyrings/docker.gpg"]},
}


async def test_apt_sources_file_create_matches_pyinfra():
    """pyinfra's create.json: an unconfigured repository yields the spool
    transfer plus the elevated install, and the generated deb822 content is
    the fixture's upload payload verbatim."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.sources_file(ctx, **DOCKER_KWARGS)]
    assert ctx.changed
    assert ctx.reason == f'apt sources file "{DOCKER_DEST}" missing or outdated, will be written'
    assert commands == [
        AptSourcesFileUploadCommand(content=DOCKER_CONTENT, dest=_SOURCES_SPOOL_PATH),
        AptSourcesFileInstallCommand(spool=_SOURCES_SPOOL_PATH, dest=DOCKER_DEST),
    ]


async def test_apt_sources_file_create_idempotent():
    """pyinfra's create_idempotent.json: every expanded repository already
    configured -> no-op with pyinfra's noop_description verbatim. The fact
    entry carries list-valued options (the AptSource shape)."""
    host = SimulatedHost(name="h", apt_sources=[dict(DOCKER_SOURCE)])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.sources_file(ctx, **DOCKER_KWARGS)]
    assert not ctx.changed
    assert ctx.reason == f'apt sources file "{DOCKER_DEST}" is already configured'
    assert commands == []


async def test_apt_sources_file_create_minimal():
    """pyinfra's create_minimal.json: only uris and suites given -> types
    defaults to deb and the Components/Architectures/Signed-By lines are
    omitted from the content."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in apt.sources_file(
            ctx,
            filename="debian",
            uris=["http://deb.debian.org/debian"],
            suites=["bookworm"],
        )
    ]
    assert ctx.changed
    content = "Types: deb\nURIs: http://deb.debian.org/debian\nSuites: bookworm\n"
    assert commands == [
        AptSourcesFileUploadCommand(content=content, dest=_SOURCES_SPOOL_PATH),
        AptSourcesFileInstallCommand(
            spool=_SOURCES_SPOOL_PATH, dest="/etc/apt/sources.list.d/debian.sources"
        ),
    ]


async def test_apt_sources_file_string_args():
    """pyinfra's create_string_args.json: space-separated strings normalize
    to lists, so multi-valued Types and Suites land on one line each."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in apt.sources_file(
            ctx,
            filename="debian",
            types="deb deb-src",
            uris="http://deb.debian.org/debian",
            suites="bookworm bookworm-updates",
        )
    ]
    assert ctx.changed
    content = (
        "Types: deb deb-src\n"
        "URIs: http://deb.debian.org/debian\n"
        "Suites: bookworm bookworm-updates\n"
    )
    assert commands[0] == AptSourcesFileUploadCommand(
        content=content, dest=_SOURCES_SPOOL_PATH
    )


async def test_apt_sources_file_create_update():
    """pyinfra's create_update.json: the file exists with different content
    (here an outdated suite), so the full rewrite is planned even though a
    sources file is already present."""
    outdated = {**DOCKER_SOURCE, "distribution": "bullseye"}
    host = SimulatedHost(
        name="h",
        apt_sources=[outdated],
        remote_files={DOCKER_DEST: DOCKER_CONTENT.replace("bookworm", "bullseye").encode()},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.sources_file(ctx, **DOCKER_KWARGS)]
    assert ctx.changed
    assert commands == [
        AptSourcesFileUploadCommand(content=DOCKER_CONTENT, dest=_SOURCES_SPOOL_PATH),
        AptSourcesFileInstallCommand(spool=_SOURCES_SPOOL_PATH, dest=DOCKER_DEST),
    ]


async def test_apt_sources_file_requires_uris():
    """pyinfra's error_missing_uris.json: suites without any URI raises
    OperationError with pyinfra's message verbatim."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(
        OperationError, match="apt.sources_file requires at least one URI and one suite"
    ):
        _ = [
            c
            async for c in apt.sources_file(ctx, filename="docker", suites=["bookworm"])
        ]


async def test_apt_sources_file_requires_suites():
    """pyinfra's error_missing_suites.json: uris without any suite raises the
    same error, and the check runs before any fact read."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(
        OperationError, match="apt.sources_file requires at least one URI and one suite"
    ):
        _ = [
            c
            async for c in apt.sources_file(
                ctx, filename="docker", uris=["https://download.docker.com/linux/debian"]
            )
        ]


async def test_apt_sources_file_remove_always_yields():
    """pyinfra's remove.json command (rm -f) with the documented deviation:
    the removal is non-idempotent, so both fixture cases -- file present and
    file absent (remove_missing.json, a no-op upstream) -- yield the same
    command and report a change."""
    for seeded in (True, False):
        host = SimulatedHost(name="h")
        if seeded:
            host.remote_files[DOCKER_DEST] = DOCKER_CONTENT.encode()
            host.existing_files.add(DOCKER_DEST)
        ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
        commands = [c async for c in apt.sources_file(ctx, filename="docker", present=False)]
        assert ctx.changed
        assert ctx.reason == f'apt sources file "{DOCKER_DEST}" will be removed'
        assert commands == [AptSourcesFileRemoveCommand(path=DOCKER_DEST)]


async def test_apt_sources_file_multi_value_expansion_idempotence():
    """The idempotence check is all() over the type/URI/suite expansion: a
    partially configured combination set still writes, a complete one is a
    no-op."""
    kwargs = {
        "filename": "debian",
        "types": ["deb", "deb-src"],
        "uris": ["http://deb.debian.org/debian"],
        "suites": ["bookworm", "bookworm-updates"],
    }
    combinations = [
        {
            "type": repo_type,
            "url": "http://deb.debian.org/debian",
            "distribution": suite,
            "components": [],
            "options": {},
        }
        for repo_type in ("deb", "deb-src")
        for suite in ("bookworm", "bookworm-updates")
    ]
    host = SimulatedHost(name="h", apt_sources=combinations[:-1])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.sources_file(ctx, **kwargs)]
    assert ctx.changed
    assert len(commands) == 2
    host_all = SimulatedHost(name="h", apt_sources=combinations)
    ctx_all = OpContext(host=host_all, facts=FactCache(host_all, phase="prepare"))
    assert [c async for c in apt.sources_file(ctx_all, **kwargs)] == []
    assert not ctx_all.changed


async def test_apt_sources_file_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors pyinfra's global-arguments slot; op() already
    strips the ``name``/``sudo`` meta-parameters, the rest is ignored."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.sources_file(ctx, **DOCKER_KWARGS, future_flag=True)]
    assert commands == [
        AptSourcesFileUploadCommand(content=DOCKER_CONTENT, dest=_SOURCES_SPOOL_PATH),
        AptSourcesFileInstallCommand(spool=_SOURCES_SPOOL_PATH, dest=DOCKER_DEST),
    ]


def test_apt_sources_file_upload_command_renders_as_an_unprivileged_scp():
    """The spool transfer has no shell(); it renders via __str__ and -- being
    a TransferCommand -- is never elevated, even though the request is
    sudo=True for the install half."""
    cmd = AptSourcesFileUploadCommand(content=DOCKER_CONTENT, dest=_SOURCES_SPOOL_PATH)
    assert not hasattr(cmd, "shell")
    assert str(cmd) == f"scp <deb822 sources> -> {_SOURCES_SPOOL_PATH}"
    assert command_shell(cmd) == f"scp <deb822 sources> -> {_SOURCES_SPOOL_PATH}"
    assert command_shell(cmd, sudo=True) == f"scp <deb822 sources> -> {_SOURCES_SPOOL_PATH}"


def test_apt_sources_file_install_command_shell_and_elevation():
    """The install wraps mkdir, cp, chmod 0644 (pyinfra's fixed mode) and the
    spool cleanup in one sh -c so every step runs under the elevated shell.
    The paths are safe tokens, so shlex.quote leaves them unquoted."""
    cmd = AptSourcesFileInstallCommand(spool=_SOURCES_SPOOL_PATH, dest=DOCKER_DEST)
    shell = cmd.shell()
    assert shell.startswith("sh -c ")
    inner = shlex.split(shell)[2]
    assert inner == (
        "mkdir -p /etc/apt/sources.list.d && "
        f"cp {_SOURCES_SPOOL_PATH} {DOCKER_DEST} && "
        f"chmod 0644 {DOCKER_DEST} && rm -f {_SOURCES_SPOOL_PATH}"
    )
    assert command_shell(cmd, sudo=True) == f"sudo {shell}"
    assert command_shell(cmd, su=True) == f"su -c {shlex.quote(shell)}"


def test_apt_sources_file_install_command_quotes_unsafe_paths():
    """A dest carrying shell metacharacters is quoted everywhere it appears:
    the cp destination, the chmod operand and the mkdir parent."""
    dest = "/etc/apt/sources.list.d/my repo;rm.sources"
    cmd = AptSourcesFileInstallCommand(spool=_SOURCES_SPOOL_PATH, dest=dest)
    inner = shlex.split(cmd.shell())[2]
    quoted = shlex.quote(dest)
    assert f"cp {_SOURCES_SPOOL_PATH} {quoted}" in inner
    assert f"chmod 0644 {quoted}" in inner
    assert "mkdir -p /etc/apt/sources.list.d" in inner


def test_apt_sources_file_remove_command_shell_matches_pyinfra():
    """pyinfra's remove.json command verbatim (the path is a safe token),
    elevated as a whole; an unsafe filename is shell-quoted."""
    cmd = AptSourcesFileRemoveCommand(path=DOCKER_DEST)
    assert cmd.shell() == f"rm -f {DOCKER_DEST}"
    assert command_shell(cmd, sudo=True) == f"sudo rm -f {DOCKER_DEST}"
    unsafe = AptSourcesFileRemoveCommand(path="/etc/apt/sources.list.d/my repo.sources")
    assert unsafe.shell() == f"rm -f {shlex.quote('/etc/apt/sources.list.d/my repo.sources')}"


# The same repository with tuple containers -- what a deploy passes, since
# OperationRequest prefers immutable kwargs (lists remain accepted by the
# operation itself, as pyinfra's fixtures use them).
DOCKER_DEPLOY_KWARGS = {
    "filename": "docker",
    "types": ("deb",),
    "uris": ("https://download.docker.com/linux/debian",),
    "suites": ("bookworm",),
    "components": ("stable",),
    "architectures": ("amd64",),
    "signed_by": "/etc/apt/keyrings/docker.gpg",
}


async def test_apt_sources_file_simulated_seam_spools_then_installs():
    """send_command maps the transfer onto remote_files (via put_file) and the
    install onto the sources path while clearing the spool file. The file also
    lands in existing_files and file_stats (root, mode 644), and its parsed
    entry lands in apt_sources so the AptSources fact sees it."""
    host = SimulatedHost(name="h")
    await send_command(
        host, AptSourcesFileUploadCommand(content=DOCKER_CONTENT, dest=_SOURCES_SPOOL_PATH)
    )
    assert host.remote_files[_SOURCES_SPOOL_PATH] == DOCKER_CONTENT.encode()
    await send_command(
        host, AptSourcesFileInstallCommand(spool=_SOURCES_SPOOL_PATH, dest=DOCKER_DEST)
    )
    assert host.remote_files[DOCKER_DEST] == DOCKER_CONTENT.encode()
    assert _SOURCES_SPOOL_PATH not in host.remote_files
    assert DOCKER_DEST in host.existing_files
    assert host.file_stats[DOCKER_DEST] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(DOCKER_CONTENT.encode()),
    }
    assert host.apt_sources == [DOCKER_SOURCE]


async def test_apt_sources_file_simulated_seam_rewrite_replaces_entries():
    """A rewrite drops the entries the previous content parsed into and adds
    the new ones, so apt_sources never accumulates stale repositories."""
    host = SimulatedHost(name="h")
    await send_command(
        host, AptSourcesFileUploadCommand(content=DOCKER_CONTENT, dest=_SOURCES_SPOOL_PATH)
    )
    await send_command(
        host, AptSourcesFileInstallCommand(spool=_SOURCES_SPOOL_PATH, dest=DOCKER_DEST)
    )
    updated = DOCKER_CONTENT.replace("bookworm", "bookworm-updates")
    await send_command(
        host, AptSourcesFileUploadCommand(content=updated, dest=_SOURCES_SPOOL_PATH)
    )
    await send_command(
        host, AptSourcesFileInstallCommand(spool=_SOURCES_SPOOL_PATH, dest=DOCKER_DEST)
    )
    assert host.remote_files[DOCKER_DEST] == updated.encode()
    assert host.apt_sources == [{**DOCKER_SOURCE, "distribution": "bookworm-updates"}]


async def test_apt_sources_file_simulated_seam_remove():
    """The removal drops the content, existence marker, stats row and parsed
    entries together, and tolerates an absent path (rm -f exits 0)."""
    host = SimulatedHost(name="h")
    await send_command(
        host, AptSourcesFileUploadCommand(content=DOCKER_CONTENT, dest=_SOURCES_SPOOL_PATH)
    )
    await send_command(
        host, AptSourcesFileInstallCommand(spool=_SOURCES_SPOOL_PATH, dest=DOCKER_DEST)
    )
    await send_command(host, AptSourcesFileRemoveCommand(path=DOCKER_DEST))
    assert DOCKER_DEST not in host.remote_files
    assert DOCKER_DEST not in host.existing_files
    assert DOCKER_DEST not in host.file_stats
    assert host.apt_sources == []
    # A second removal of the now-absent file is tolerated.
    await send_command(host, AptSourcesFileRemoveCommand(path=DOCKER_DEST))
    assert host.apt_sources == []


async def test_apt_sources_file_end_to_end_write_then_noop():
    """Through run(): the apply phase spools the content by scp then installs
    it elevated; the log shows the transfer unprivileged and only the install
    with sudo. A second run is a no-op with pyinfra's noop_description."""

    def sources_deploy(host):
        yield op(
            apt.sources_file,
            name="Add Docker apt repository (deb822)",
            sudo=True,
            **DOCKER_DEPLOY_KWARGS,
        )

    host = SimulatedHost(name="h")

    def factory(entry):
        return host

    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], sources_deploy, host_factory=factory
    )
    assert plans["h"][0].changed
    assert "[changed: " in format_plan(plans)
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (
        AptSourcesFileUploadCommand(content=DOCKER_CONTENT, dest=_SOURCES_SPOOL_PATH),
        AptSourcesFileInstallCommand(spool=_SOURCES_SPOOL_PATH, dest=DOCKER_DEST),
    )
    assert host.remote_files[DOCKER_DEST] == DOCKER_CONTENT.encode()
    assert _SOURCES_SPOOL_PATH not in host.remote_files
    assert host.apt_sources == [DOCKER_SOURCE]
    log = format_log(report)
    assert f"    $ scp <deb822 sources> -> {_SOURCES_SPOOL_PATH}" in log
    assert "    $ sudo sh -c " in log

    # Idempotent: the fresh fact read sees the installed entries -> no change.
    plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], sources_deploy, host_factory=factory
    )
    assert not plans2["h"][0].changed
    (entry2,) = report2.log
    assert not entry2.changed
    assert entry2.reason == f'apt sources file "{DOCKER_DEST}" is already configured'


async def test_apt_sources_file_end_to_end_update_replaces():
    """A second run with a changed suite rewrites the file and the sources
    seam holds only the new repository -- the outdated entry is gone."""

    def deploy_for(suite):
        def sources_deploy(host):
            yield op(
                apt.sources_file,
                name="Add Docker apt repository (deb822)",
                sudo=True,
                **{**DOCKER_DEPLOY_KWARGS, "suites": (suite,)},
            )

        return sources_deploy

    host = SimulatedHost(name="h")

    def factory(entry):
        return host

    _plans, _report, _hosts = await run(
        [{"host": "h", "connection": {}}], deploy_for("bookworm"), host_factory=factory
    )
    plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}],
        deploy_for("bookworm-updates"),
        host_factory=factory,
    )
    assert plans2["h"][0].changed
    (entry2,) = report2.log
    assert entry2.changed
    assert host.apt_sources == [{**DOCKER_SOURCE, "distribution": "bookworm-updates"}]
    assert b"Suites: bookworm-updates\n" in host.remote_files[DOCKER_DEST]


async def test_apt_sources_file_dry_run_sends_nothing():
    """dry=True materializes the plan (changed, two commands) but mutates no
    simulated state -- no spool file, no sources file, no entries."""

    def sources_deploy(host):
        yield op(
            apt.sources_file,
            name="Add Docker apt repository (deb822)",
            sudo=True,
            **DOCKER_DEPLOY_KWARGS,
        )

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        sources_deploy,
        dry=True,
        host_factory=lambda entry: host,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert len(planned.commands) == 2
    assert host.remote_files == {}
    assert host.apt_sources == []


async def test_apt_sources_file_repeat_write_in_one_deploy_apply_noop():
    """Two identical writes: the prepare plan shows both changed (its fact
    cache persists), but the apply log shows the second as a no-op -- the
    per-step fresh cache sees the installed entries."""

    def sources_deploy(host):
        yield op(apt.sources_file, name="write once", sudo=True, **DOCKER_DEPLOY_KWARGS)
        yield op(apt.sources_file, name="write twice", sudo=True, **DOCKER_DEPLOY_KWARGS)

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], sources_deploy)
    assert plans["h"][0].changed
    assert plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    first, second = report.log
    assert first.changed
    assert not second.changed
    assert second.reason == f'apt sources file "{DOCKER_DEST}" is already configured'
    assert host.apt_sources == [DOCKER_SOURCE]
    assert _SOURCES_SPOOL_PATH not in host.remote_files


async def test_apt_sources_file_write_then_remove_in_one_deploy():
    """Write then remove in one deploy: the removal is non-idempotent, so it
    is planned changed even in prepare (unlike apt.repo's fact-gated remove)
    and the apply leaves every store empty."""

    def sources_deploy(host):
        yield op(apt.sources_file, name="write", sudo=True, **DOCKER_DEPLOY_KWARGS)
        yield op(
            apt.sources_file, name="remove", filename="docker", present=False, sudo=True
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], sources_deploy)
    assert plans["h"][0].changed
    assert plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    write, remove = report.log
    assert write.changed
    assert remove.changed
    assert remove.commands == (AptSourcesFileRemoveCommand(path=DOCKER_DEST),)
    assert f"$ sudo rm -f {DOCKER_DEST}" in format_log(report)
    assert host.remote_files == {}
    assert host.existing_files == set()
    assert host.apt_sources == []


async def test_apt_sources_file_fresh_containers_per_phase_stay_deterministic():
    """Unlike apt.key's identity-compared stream, the content is generated
    inside the operation from value-compared kwargs, so a deploy building
    fresh lists on every invocation still passes the determinism guard."""

    def sources_deploy(host):
        yield op(
            apt.sources_file,
            name="Add Docker apt repository (deb822)",
            filename="docker",
            types=["deb"],
            uris=["https://download.docker.com/linux/debian"],
            suites=["bookworm"],
            components=["stable"],
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], sources_deploy)
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.changed
    assert DOCKER_DEST in host.remote_files