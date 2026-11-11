# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the gpg module: the facts ``GpgKey``, ``GpgKeyDetails``,
``GpgKeyrings``, ``GpgKeys``, ``GpgSecretKeys``, ``GpgTrust`` and
``GpgVerify`` plus the operations ``gpg.key``, ``gpg.dearmor``,
``gpg.import_key``, ``gpg.export_key``, ``gpg.delete_key``, ``gpg.gen_key``,
``gpg.trust`` and ``gpg.refresh_keys``.

Covers the read-only state discovery in ``reemote.modules.gpg.gpg``:
every fact is exercised both over the real-transport path (parsing canned
``gpg --with-colons`` output, reusing the baseline's own fact fixtures
verbatim, with the id-keyed fixture dicts normalized to reemote's typed
entry lists) and through the ``SimulatedHost`` seam, plus the
``gather_fact`` fan-out with argument forwarding. ``GpgKey`` additionally
covers its stream-only validation deviation (empty and URL ``src``
rejected identically on both transports), ``GpgKeys``/``GpgSecretKeys``
cover the shared colon-record parser's edge cases (records before the
first primary key ignored, only the last subkey kept, ``pub`` records
invisible to the secret parser) and ``GpgKeyrings`` covers the baseline's five
keyring fixtures (formats, subkey fingerprints, marker-only unreadable
files) plus its empty-directories validation. The three originals
(``GpgTrust``, ``GpgKeyDetails``, ``GpgVerify``) parse canned output
captured from real local gpg 2.4.7 runs instead of baseline fixtures:
ownertrust exports, a targeted colon listing and ``[GNUPG:]`` status
streams for good, bad and unverifiable signatures.

The operation side covers ``gpg.key`` (the baseline's ``gpg.key`` with
stream-only sources: descriptor shell rendering and elevation, install
idempotence both with and without ``keyid``, the whole-file and selective
removal branches against the baseline's own operation fixtures, the armored
partial-removal rejection, validation and kwargs absorption, the
SimulatedHost mutators and end-to-end ``run``/dry-run/determinism) and
``gpg.dearmor`` (stateless conversion between remote paths). The six
keychain originals cover descriptor rendering and elevation, idempotence
against the keychain/trust facts both directions (import/gen/delete/trust)
or deliberate statelessness (export/refresh), validation and kwargs
absorption, the ``gen_key`` ``real_name`` meta-parameter collision
regression, the SimulatedHost keychain mutators and end-to-end
``run``/``run_prepare``+``run_apply`` with dry-run and determinism guards.

``asyncio_mode = "auto"`` lets the async tests below run without decorators.
"""

import hashlib
import io
import shlex

import pytest

from reemote.core import FactCache, OpContext, op
from reemote.drivers import (
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
from reemote.hosts import Host
from reemote.modules import gpg
from reemote.modules.gpg.gpg import (
    GpgDearmorCommand,
    GpgDeleteKeyCommand,
    GpgExportCommand,
    GpgGenKeyCommand,
    GpgImportCommand,
    GpgImportUploadCommand,
    GpgKey,
    GpgKeyDeleteKeysCommand,
    GpgKeyDetails,
    GpgKeyEmptyCleanupCommand,
    GpgKeyInstallCommand,
    GpgKeyRemoveCommand,
    GpgKeyrings,
    GpgKeys,
    GpgKeyUploadCommand,
    GpgRefreshKeysCommand,
    GpgSecretKeys,
    GpgTrust,
    GpgTrustCommand,
    GpgVerify,
    _clean_keyid,
    _import_spool_path,
    _key_spool_path,
    _KeyringFileExists,
    _matches_keyid,
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


def _key_entries(fact: dict) -> list[dict]:
    """Normalize a baseline id-keyed key-fact dict to reemote entry lists.

    The baseline keys keys (and their subkeys) by id; reemote returns a list of
    entries each carrying its ``id``, with subkeys as a list of their own
    entries (the ``apt.AptKeys`` retyping precedent).
    """
    entries = []
    for key_id, details in fact.items():
        entry = {"id": key_id, **details}
        if "subkeys" in details:
            entry["subkeys"] = [
                {"id": sub_id, **sub} for sub_id, sub in details["subkeys"].items()
            ]
        entries.append(entry)
    return entries


def _keyring_entries(fact: dict) -> list[dict]:
    """Normalize a baseline path-keyed GpgKeyrings fact dict to entry lists."""
    return [
        {"path": path, "format": details["format"], "keys": _key_entries(details["keys"])}
        for path, details in fact.items()
    ]


def _keyrings_command(search_locations: str) -> str:
    # The ``command`` field of the baseline's gpg.GpgKeyrings fixtures verbatim;
    # every fixture differs only in its quoted directory list.
    return (
        f"find {search_locations}"
        " -type f \\( -name '*.gpg' -o -name '*.kbx' \\) 2>/dev/null"
        " -exec sh -c 'echo \"KEYRING:$1\";"
        " gpg --list-keys --with-colons --keyring \"$1\" --no-default-keyring 2>/dev/null || true' _ {} \\; ;"
        f" find {search_locations}"
        " -type f -name '*.asc' 2>/dev/null"
        " -exec sh -c 'echo \"KEYRING:$1\"; gpg --with-colons \"$1\" 2>/dev/null || true' _ {} \\;"
    )


# --- GpgKey (the baseline's gpg.GpgKey/key_from_file.json fixture) ------------------

# The ``output`` field of the baseline's ``gpg.GpgKey/key_from_file.json`` fixture
# (``key_from_url.json`` carries the same records behind the dropped URL
# branch): the Docker release key with one subkey and no fingerprints.
KEY_FILE_OUTPUT = [
    "pub:-:4096:1:8D81803C0EBFCD88:1487788586:::-:",
    "uid:::::::::Docker Release (CE deb) <docker@docker.com>:",
    "sub:-:4096:1:7EA0A9C3F273FCD8:1487788586::::",
]


def test_gpg_key_command_matches_baseline():
    """The command is the baseline's key_from_file fixture command verbatim."""
    assert GpgKey.command("/some/key/file") == "gpg --with-colons /some/key/file"


def test_gpg_key_command_quotes_src():
    """A src with shell metacharacters is shlex-quoted."""
    src = "/tmp/some key;rm -rf.gpg"
    assert GpgKey.command(src) == f"gpg --with-colons {shlex.quote(src)}"


def test_gpg_key_command_rejects_empty_src():
    """An empty src raises ValueError instead of reading a bare gpg stream."""
    with pytest.raises(ValueError, match="non-empty path"):
        GpgKey.command("")


@pytest.mark.parametrize(
    "src",
    [
        "https://download.docker.com/linux/debian/gpg",
        "http://keys.example.com/key.asc",
    ],
)
def test_gpg_key_command_rejects_urls(src):
    """The baseline's wget/curl URL branch is dropped: URL srcs raise ValueError."""
    with pytest.raises(ValueError, match="not a URL"):
        GpgKey.command(src)


async def test_gpg_key_rejects_empty_src_on_simulated_host():
    """The simulated path validates identically: empty src raises."""
    host = SimulatedHost(name="web1")
    with pytest.raises(ValueError, match="non-empty path"):
        await GpgKey.process(host, "")


async def test_gpg_key_rejects_url_on_simulated_host():
    """The simulated path validates identically: URL src raises."""
    host = SimulatedHost(name="web1")
    with pytest.raises(ValueError, match="not a URL"):
        await GpgKey.process(host, "https://download.docker.com/linux/debian/gpg")


async def test_gpg_key_parses_baseline_fixture():
    """The Docker key parses to the baseline's fixture fact, retyped to a list."""
    host = CannedShellHost("\n".join(KEY_FILE_OUTPUT))
    keys = await GpgKey.process(host, "/some/key/file")
    expected = _key_entries(
        {
            "8D81803C0EBFCD88": {
                "validity": "-",
                "length": 4096,
                "subkeys": {"7EA0A9C3F273FCD8": {"validity": "-", "length": 4096}},
                "uid_hash": "",
                "uid": "Docker Release (CE deb) <docker@docker.com>",
            }
        }
    )
    assert keys == expected
    assert host.last_shell == "gpg --with-colons /some/key/file"


async def test_gpg_key_empty_output_is_empty_list():
    """A missing or unreadable file (exit 2, empty stdout) reads as []."""
    assert await GpgKey.process(CannedShellHost(""), "/missing/key.gpg") == []


async def test_gpg_key_seam_is_keyed_by_src():
    """The seam answers per src path; an unseeded path reads as []."""
    entries = _key_entries(
        {"8D81803C0EBFCD88": {"validity": "-", "length": 4096, "subkeys": {}}}
    )
    host = SimulatedHost(name="web1", gpg_key={"/some/key/file": entries})
    assert await GpgKey.process(host, "/some/key/file") == entries
    assert await GpgKey.process(host, "/other/key.gpg") == []


async def test_gpg_key_seam_returns_copies():
    """Mutating a seam answer (entries and subkey dicts) never touches state."""
    entries = _key_entries(
        {
            "8D81803C0EBFCD88": {
                "validity": "-",
                "length": 4096,
                "subkeys": {"7EA0A9C3F273FCD8": {"validity": "-", "length": 4096}},
                "uid": "Docker Release (CE deb) <docker@docker.com>",
            }
        }
    )
    host = SimulatedHost(name="web1", gpg_key={"/some/key/file": entries})
    answer = await GpgKey.process(host, "/some/key/file")
    answer[0]["uid"] = "mutated"
    answer[0]["subkeys"][0]["length"] = 1
    answer[0]["subkeys"].append({"id": "X", "validity": "-", "length": 1})
    answer.clear()
    assert await GpgKey.process(host, "/some/key/file") == entries


async def test_gpg_key_gather_fact_forwards_src():
    """gather_fact fans out over hosts, forwarding the src argument."""
    entries = _key_entries(
        {"8D81803C0EBFCD88": {"validity": "-", "length": 4096, "subkeys": {}}}
    )
    host1 = SimulatedHost(name="web1", gpg_key={"/some/key/file": entries})
    host2 = SimulatedHost(name="web2")
    keys = await gather_fact([host1, host2], GpgKey, "/some/key/file")
    assert keys == {"web1": entries, "web2": []}


# --- GpgKeys (the baseline's gpg.GpgKeys fixtures) ----------------------------------

# The ``output`` field of the baseline's ``gpg.GpgKeys/keys.json`` fixture: four
# public keys with tru noise and a blank line between records.
KEYS_OUTPUT = [
    "tru:t:1:1601454628:0:3:1:5",
    "pub:-:4096:1:3B4FE6ACC0B21F32:1336770936:::-:::scSC::::::23::0:",
    "fpr:::::::::790BC7277767219C42C86F933B4FE6ACC0B21F32:",
    "uid:-::::1336770936::B7A02867A0C1D32B594B36C00E20C8C57E397748::Ubuntu Archive Automatic Signing Key (2012) <ftpmaster@ubuntu.com>::::::::::0:",
    "tru:t:1:1601454628:0:3:1:5:",
    "pub:-:4096:1:D94AA3F0EFE21092:1336774248:::-:::scSC::::::23::0:",
    "fpr:::::::::843938DF228D22F7B3742BC0D94AA3F0EFE21092:",
    "uid:-::::1336774248::77355A0B96082B2694009775B6490C605BD16B6F::Ubuntu CD Image Automatic Signing Key (2012) <cdimage@ubuntu.com>::::::::::0:",
    "pub:-:4096:1:8D81803C0EBFCD88:1487788586:::-:",
    "uid:::::::::Docker Release (CE deb) <docker@docker.com>:",
    "sub:-:4096:1:7EA0A9C3F273FCD8:1487788586::::",
    "",
    "tru:t:1:1601454628:0:3:1:5",
    "pub:-:4096:1:871920D1991BC93C:1537196506:::-:::scSC::::::23::0:",
    "fpr:::::::::F6ECB3762474EDA9D21B7022871920D1991BC93C:",
    "uid:-::::1537196506::BE438F08F546424C0EA810FD722053597EB5127B::Ubuntu Archive Automatic Signing Key (2018) <ftpmaster@ubuntu.com>::::::::::0:",
]

# The ``output`` field of ``keys_keyring.json``: the three Ubuntu keys, no
# Docker key, listed from one explicit keyring file.
KEYS_KEYRING_OUTPUT = [
    "tru:t:1:1601454628:0:3:1:5",
    "pub:-:4096:1:3B4FE6ACC0B21F32:1336770936:::-:::scSC::::::23::0:",
    "fpr:::::::::790BC7277767219C42C86F933B4FE6ACC0B21F32:",
    "uid:-::::1336770936::B7A02867A0C1D32B594B36C00E20C8C57E397748::Ubuntu Archive Automatic Signing Key (2012) <ftpmaster@ubuntu.com>::::::::::0:",
    "tru:t:1:1601454628:0:3:1:5:",
    "pub:-:4096:1:D94AA3F0EFE21092:1336774248:::-:::scSC::::::23::0:",
    "fpr:::::::::843938DF228D22F7B3742BC0D94AA3F0EFE21092:",
    "uid:-::::1336774248::77355A0B96082B2694009775B6490C605BD16B6F::Ubuntu CD Image Automatic Signing Key (2012) <cdimage@ubuntu.com>::::::::::0:",
    "tru:t:1:1601454628:0:3:1:5",
    "pub:-:4096:1:871920D1991BC93C:1537196506:::-:::scSC::::::23::0:",
    "fpr:::::::::F6ECB3762474EDA9D21B7022871920D1991BC93C:",
    "uid:-::::1537196506::BE438F08F546424C0EA810FD722053597EB5127B::Ubuntu Archive Automatic Signing Key (2018) <ftpmaster@ubuntu.com>::::::::::0:",
]

# The ``fact`` fields of the baseline's ``gpg.GpgKeys/keys.json`` (the secret-key
# fixtures carry the identical fact dicts) and ``keys_keyring.json``.
KEYS_FACT = {
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
    "8D81803C0EBFCD88": {
        "validity": "-",
        "length": 4096,
        "subkeys": {"7EA0A9C3F273FCD8": {"validity": "-", "length": 4096}},
        "uid_hash": "",
        "uid": "Docker Release (CE deb) <docker@docker.com>",
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
KEYS_KEYRING_FACT = {
    key_id: KEYS_FACT[key_id]
    for key_id in ("3B4FE6ACC0B21F32", "D94AA3F0EFE21092", "871920D1991BC93C")
}


def test_gpg_keys_command_matches_baseline():
    """The default command is the baseline's keys.json fixture command verbatim."""
    assert GpgKeys.command() == "gpg --list-keys --with-colons"
    assert GpgKeys.command(None) == "gpg --list-keys --with-colons"


def test_gpg_keys_command_keyring_matches_baseline():
    """A keyring path is the baseline's keys_keyring.json command verbatim."""
    assert GpgKeys.command("/some/keyring") == (
        "gpg --list-keys --with-colons --keyring /some/keyring --no-default-keyring"
    )


def test_gpg_keys_command_quotes_keyring():
    """A keyring path with shell metacharacters is shlex-quoted."""
    keyring = "/tmp/some keyring;rm -rf.gpg"
    assert GpgKeys.command(keyring) == (
        f"gpg --list-keys --with-colons --keyring {shlex.quote(keyring)}"
        " --no-default-keyring"
    )


async def test_gpg_keys_parses_baseline_fixture():
    """The four public keys parse to the baseline's keys.json fact, retyped."""
    host = CannedShellHost("\n".join(KEYS_OUTPUT))
    keys = await GpgKeys.process(host)
    assert keys == _key_entries(KEYS_FACT)
    assert host.last_shell == "gpg --list-keys --with-colons"


async def test_gpg_keys_parses_keyring_baseline_fixture():
    """The keyring listing parses to the baseline's keys_keyring.json fact."""
    host = CannedShellHost("\n".join(KEYS_KEYRING_OUTPUT))
    keys = await GpgKeys.process(host, "/some/keyring")
    assert keys == _key_entries(KEYS_KEYRING_FACT)
    assert host.last_shell == (
        "gpg --list-keys --with-colons --keyring /some/keyring --no-default-keyring"
    )


def test_gpg_keys_parse_ignores_records_before_first_pub():
    """tru/fpr/uid/sub records before the first pub attach to nothing."""
    output = [
        "tru:t:1:1601454628:0:3:1:5",
        "fpr:::::::::DEADBeEFDEADBEEFDEADBEEFDEADBEEFDEADBEEF:",
        "uid:::::::::Stray User <stray@example.com>:",
        "sub:-:4096:1:0000000000000000:1487788586::::",
        "pub:-:2048:1:AAAAAAAAAAAAAAAA:1487788586:::-:",
    ]
    assert GpgKeys.parse(output) == [
        {"id": "AAAAAAAAAAAAAAAA", "validity": "-", "length": 2048, "subkeys": []}
    ]


def test_gpg_keys_parse_keeps_only_last_subkey():
    """The baseline keeps only the last sub record before the next primary key."""
    output = [
        "pub:-:4096:1:AAAAAAAAAAAAAAAA:1487788586:::-:",
        "sub:-:4096:1:1111111111111111:1487788586::::",
        "sub:-:2048:1:2222222222222222:1487788586::::",
    ]
    keys = GpgKeys.parse(output)
    assert keys[0]["subkeys"] == [
        {"id": "2222222222222222", "validity": "-", "length": 2048}
    ]


async def test_gpg_keys_empty_output_is_empty_list():
    """An empty keychain prints nothing and reads as []."""
    assert await GpgKeys.process(CannedShellHost("")) == []


async def test_gpg_keys_seam_returns_state():
    """The seam answers from gpg_keys, bypassing the shell entirely."""
    entries = _key_entries(KEYS_FACT)
    host = SimulatedHost(name="web1", gpg_keys=entries)
    assert await GpgKeys.process(host) == entries
    assert await GpgKeys.process(host, "/some/keyring") == entries


async def test_gpg_keys_seam_returns_copies():
    """Mutating a seam answer (entries and subkey dicts) never touches state."""
    entries = _key_entries(KEYS_FACT)
    host = SimulatedHost(name="web1", gpg_keys=entries)
    answer = await GpgKeys.process(host)
    answer[2]["subkeys"][0]["length"] = 1
    answer[0]["uid"] = "mutated"
    answer.pop()
    assert await GpgKeys.process(host) == entries


async def test_gpg_keys_gather_fact_fan_out():
    """gather_fact fans out over hosts, forwarding the keyring argument."""
    entries = _key_entries(KEYS_KEYRING_FACT)
    host1 = SimulatedHost(name="web1", gpg_keys=entries)
    host2 = SimulatedHost(name="web2")
    keys = await gather_fact([host1, host2], GpgKeys, "/some/keyring")
    assert keys == {"web1": entries, "web2": []}


# --- GpgSecretKeys (the baseline's gpg.GpgSecretKeys fixtures) ----------------------

# The ``output`` field of the baseline's ``gpg.GpgSecretKeys/keys.json`` fixture:
# the same keychain as keys.json with sec/ssb records instead of pub/sub.
SECRET_KEYS_OUTPUT = [
    "tru:t:1:1601454628:0:3:1:5",
    "sec:-:4096:1:3B4FE6ACC0B21F32:1336770936:::-:::scSC::::::23::0:",
    "fpr:::::::::790BC7277767219C42C86F933B4FE6ACC0B21F32:",
    "uid:-::::1336770936::B7A02867A0C1D32B594B36C00E20C8C57E397748::Ubuntu Archive Automatic Signing Key (2012) <ftpmaster@ubuntu.com>::::::::::0:",
    "tru:t:1:1601454628:0:3:1:5:",
    "sec:-:4096:1:D94AA3F0EFE21092:1336774248:::-:::scSC::::::23::0:",
    "fpr:::::::::843938DF228D22F7B3742BC0D94AA3F0EFE21092:",
    "uid:-::::1336774248::77355A0B96082B2694009775B6490C605BD16B6F::Ubuntu CD Image Automatic Signing Key (2012) <cdimage@ubuntu.com>::::::::::0:",
    "sec:-:4096:1:8D81803C0EBFCD88:1487788586:::-:",
    "uid:::::::::Docker Release (CE deb) <docker@docker.com>:",
    "ssb:-:4096:1:7EA0A9C3F273FCD8:1487788586::::",
    "",
    "tru:t:1:1601454628:0:3:1:5",
    "sec:-:4096:1:871920D1991BC93C:1537196506:::-:::scSC::::::23::0:",
    "fpr:::::::::F6ECB3762474EDA9D21B7022871920D1991BC93C:",
    "uid:-::::1537196506::BE438F08F546424C0EA810FD722053597EB5127B::Ubuntu Archive Automatic Signing Key (2018) <ftpmaster@ubuntu.com>::::::::::0:",
]

# The ``output`` field of ``keys_keyring.json``: the three Ubuntu sec keys.
SECRET_KEYS_KEYRING_OUTPUT = [
    "tru:t:1:1601454628:0:3:1:5",
    "sec:-:4096:1:3B4FE6ACC0B21F32:1336770936:::-:::scSC::::::23::0:",
    "fpr:::::::::790BC7277767219C42C86F933B4FE6ACC0B21F32:",
    "uid:-::::1336770936::B7A02867A0C1D32B594B36C00E20C8C57E397748::Ubuntu Archive Automatic Signing Key (2012) <ftpmaster@ubuntu.com>::::::::::0:",
    "tru:t:1:1601454628:0:3:1:5:",
    "sec:-:4096:1:D94AA3F0EFE21092:1336774248:::-:::scSC::::::23::0:",
    "fpr:::::::::843938DF228D22F7B3742BC0D94AA3F0EFE21092:",
    "uid:-::::1336774248::77355A0B96082B2694009775B6490C605BD16B6F::Ubuntu CD Image Automatic Signing Key (2012) <cdimage@ubuntu.com>::::::::::0:",
    "tru:t:1:1601454628:0:3:1:5",
    "sec:-:4096:1:871920D1991BC93C:1537196506:::-:::scSC::::::23::0:",
    "fpr:::::::::F6ECB3762474EDA9D21B7022871920D1991BC93C:",
    "uid:-::::1537196506::BE438F08F546424C0EA810FD722053597EB5127B::Ubuntu Archive Automatic Signing Key (2018) <ftpmaster@ubuntu.com>::::::::::0:",
]


def test_gpg_secret_keys_command_matches_baseline():
    """The default command is the baseline's secret keys.json command verbatim."""
    assert GpgSecretKeys.command() == "gpg --list-secret-keys --with-colons"
    assert GpgSecretKeys.command(None) == "gpg --list-secret-keys --with-colons"


def test_gpg_secret_keys_command_keyring_matches_baseline():
    """A keyring path is the baseline's secret keys_keyring.json command verbatim."""
    assert GpgSecretKeys.command("/some/keyring") == (
        "gpg --list-secret-keys --with-colons --keyring /some/keyring --no-default-keyring"
    )


def test_gpg_secret_keys_command_quotes_keyring():
    """A keyring path with shell metacharacters is shlex-quoted."""
    keyring = "/tmp/some keyring;rm -rf.gpg"
    assert GpgSecretKeys.command(keyring) == (
        f"gpg --list-secret-keys --with-colons --keyring {shlex.quote(keyring)}"
        " --no-default-keyring"
    )


async def test_gpg_secret_keys_parses_baseline_fixture():
    """The sec/ssb records parse to the baseline's keys.json fact, retyped."""
    host = CannedShellHost("\n".join(SECRET_KEYS_OUTPUT))
    keys = await GpgSecretKeys.process(host)
    assert keys == _key_entries(KEYS_FACT)
    assert host.last_shell == "gpg --list-secret-keys --with-colons"


async def test_gpg_secret_keys_parses_keyring_baseline_fixture():
    """The keyring listing parses to the baseline's secret keys_keyring.json fact."""
    host = CannedShellHost("\n".join(SECRET_KEYS_KEYRING_OUTPUT))
    keys = await GpgSecretKeys.process(host, "/some/keyring")
    assert keys == _key_entries(KEYS_KEYRING_FACT)
    assert host.last_shell == (
        "gpg --list-secret-keys --with-colons --keyring /some/keyring --no-default-keyring"
    )


def test_gpg_secret_keys_parse_ignores_pub_records():
    """The secret parser only starts keys on sec records, never pub ones."""
    assert GpgSecretKeys.parse(KEYS_OUTPUT) == []


async def test_gpg_secret_keys_seam_returns_state():
    """The seam answers from gpg_secret_keys, bypassing the shell."""
    entries = _key_entries(KEYS_FACT)
    host = SimulatedHost(name="web1", gpg_secret_keys=entries)
    assert await GpgSecretKeys.process(host) == entries


async def test_gpg_secret_keys_seam_returns_copies():
    """Mutating a seam answer never touches the simulated state."""
    entries = _key_entries(KEYS_FACT)
    host = SimulatedHost(name="web1", gpg_secret_keys=entries)
    answer = await GpgSecretKeys.process(host)
    answer[2]["subkeys"][0]["length"] = 1
    answer.clear()
    assert await GpgSecretKeys.process(host) == entries


async def test_gpg_secret_keys_gather_fact_fan_out():
    """gather_fact fans out over hosts, forwarding the keyring argument."""
    entries = _key_entries(KEYS_KEYRING_FACT)
    host1 = SimulatedHost(name="web1", gpg_secret_keys=entries)
    host2 = SimulatedHost(name="web2")
    keys = await gather_fact([host1, host2], GpgSecretKeys, "/some/keyring")
    assert keys == {"web1": entries, "web2": []}


# --- GpgKeyrings (the baseline's gpg.GpgKeyrings fixtures) --------------------------

# The ``output`` field of the baseline's ``gpg.GpgKeyrings/default.json`` fixture:
# one binary docker.gpg and one armored ubuntu-keyring.asc.
KEYRINGS_DEFAULT_OUTPUT = [
    "KEYRING:/etc/apt/keyrings/docker.gpg",
    "tru:t:1:1601454628:0:3:1:5",
    "pub:-:4096:1:ABCD1234EFGH5678:1336770936:::-:::scSC::::::23::0:",
    "fpr:::::::::ABCD1234EFGH5678IJKL9012MNOP3456QRST7890:",
    "uid:-::::1336770936::ABC123DEF456::Docker Release (CE deb) <docker@docker.com>::::::::::0:",
    "KEYRING:/etc/apt/trusted.gpg.d/ubuntu-keyring.asc",
    "pub:-:4096:1:1234ABCD5678EFGH:1336774248:::-:::scSC::::::23::0:",
    "uid:-::::1336774248::::Ubuntu Archive Signing Key <ubuntu-archive@lists.ubuntu.com>::::::::::0:",
]

# The ``fact`` field of ``default.json``.
KEYRINGS_DEFAULT_FACT = {
    "/etc/apt/keyrings/docker.gpg": {
        "format": "gpg",
        "keys": {
            "ABCD1234EFGH5678": {
                "validity": "-",
                "length": 4096,
                "subkeys": {},
                "fingerprint": "ABCD1234EFGH5678IJKL9012MNOP3456QRST7890",
                "uid_hash": "ABC123DEF456",
                "uid": "Docker Release (CE deb) <docker@docker.com>",
            }
        },
    },
    "/etc/apt/trusted.gpg.d/ubuntu-keyring.asc": {
        "format": "asc",
        "keys": {
            "1234ABCD5678EFGH": {
                "validity": "-",
                "length": 4096,
                "subkeys": {},
                "uid_hash": "",
                "uid": "Ubuntu Archive Signing Key <ubuntu-archive@lists.ubuntu.com>",
            }
        },
    },
}

# The ``output`` field of ``asc_only.json``: two armored Debian keyrings
# whose subkeys carry fingerprints of their own.
KEYRINGS_ASC_ONLY_OUTPUT = [
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

# The ``fact`` field of ``asc_only.json``.
KEYRINGS_ASC_ONLY_FACT = {
    "/etc/apt/trusted.gpg.d/debian-archive-bookworm-automatic.asc": {
        "format": "asc",
        "keys": {
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
            }
        },
    },
    "/etc/apt/trusted.gpg.d/debian-archive-bookworm-stable.asc": {
        "format": "asc",
        "keys": {
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
            }
        },
    },
}

# The ``output`` field of ``mixed_formats.json``: one keyring per format.
KEYRINGS_MIXED_OUTPUT = [
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

# The ``fact`` field of ``mixed_formats.json``.
KEYRINGS_MIXED_FACT = {
    "/etc/apt/trusted.gpg.d/debian-archive.gpg": {
        "format": "gpg",
        "keys": {
            "73A4F27B8DD47936": {
                "validity": "-",
                "length": 4096,
                "subkeys": {},
                "fingerprint": "4D64390375060AA4D3E84D8F73A4F27B8DD47936",
                "uid_hash": "7B1EFED0F7198D8488DD53F0E95BB93FC4D0A10C",
                "uid": "Debian Archive Automatic Signing Key (11/bullseye) <ftpmaster@debian.org>",
            }
        },
    },
    "/etc/apt/trusted.gpg.d/ubuntu-keyring.asc": {
        "format": "asc",
        "keys": {
            "1E9377A2BA9EF27F": {
                "validity": "-",
                "length": 4096,
                "subkeys": {},
                "fingerprint": "790BC7277767219C42C86F931E9377A2BA9EF27F",
                "uid_hash": "",
                "uid": "Ubuntu Archive Signing Key <ubuntu-archive@lists.ubuntu.com>",
            }
        },
    },
    "/etc/apt/trusted.gpg.d/docker.kbx": {
        "format": "kbx",
        "keys": {
            "9DC858229FC7DD38": {
                "validity": "-",
                "length": 4096,
                "subkeys": {},
                "fingerprint": "9DC858229FC7DD38B3088BD89DC858229FC7DD38",
                "uid_hash": "",
                "uid": "Docker Release (CE deb) <docker@docker.com>",
            }
        },
    },
}

# The ``output`` and ``fact`` fields of ``custom_directory.json``.
KEYRINGS_CUSTOM_OUTPUT = [
    "KEYRING:/custom/keyring/path/custom.asc",
    "pub:-:4096:1:9A3B3C4D5E6F7890:1622505600:::-:::scSC::::::23::0:",
    "fpr:::::::::1234567890ABCDEF1234567890ABCDEF12345678:",
    "uid:-::::1622505600::::Custom Key <custom@example.com>::::::::::0:",
]
KEYRINGS_CUSTOM_FACT = {
    "/custom/keyring/path/custom.asc": {
        "format": "asc",
        "keys": {
            "9A3B3C4D5E6F7890": {
                "validity": "-",
                "length": 4096,
                "subkeys": {},
                "fingerprint": "1234567890ABCDEF1234567890ABCDEF12345678",
                "uid_hash": "",
                "uid": "Custom Key <custom@example.com>",
            }
        },
    },
}

APT_DIRECTORIES = ["/etc/apt/trusted.gpg.d", "/etc/apt/keyrings", "/usr/share/keyrings"]


def test_gpg_keyrings_command_matches_baseline():
    """The multi-directory command is the baseline's default.json command verbatim."""
    assert GpgKeyrings.command(APT_DIRECTORIES) == _keyrings_command(
        '"/etc/apt/trusted.gpg.d" "/etc/apt/keyrings" "/usr/share/keyrings"'
    )


def test_gpg_keyrings_command_single_directory():
    """A single string is one directory; a one-element list renders the same."""
    expected = _keyrings_command('"/custom/keyring/path"')
    assert GpgKeyrings.command("/custom/keyring/path") == expected
    assert GpgKeyrings.command(["/custom/keyring/path"]) == expected


def test_gpg_keyrings_command_accepts_tuples():
    """A tuple renders like the list -- the hashable form gpg.key passes
    through FactCache.get."""
    assert GpgKeyrings.command(tuple(APT_DIRECTORIES)) == GpgKeyrings.command(
        APT_DIRECTORIES
    )
    assert GpgKeyrings.command(("/custom/keyring/path",)) == _keyrings_command(
        '"/custom/keyring/path"'
    )


@pytest.mark.parametrize("directories", [[], "", ["/ok", ""]])
def test_gpg_keyrings_command_rejects_empty_directories(directories):
    """Empty directory arguments raise ValueError, never search the cwd."""
    with pytest.raises(ValueError, match="at least one non-empty path"):
        GpgKeyrings.command(directories)


@pytest.mark.parametrize("directories", [[], ""])
async def test_gpg_keyrings_rejects_empty_directories_on_simulated_host(directories):
    """The simulated path validates identically: empty directories raise."""
    host = SimulatedHost(name="web1")
    with pytest.raises(ValueError, match="at least one non-empty path"):
        await GpgKeyrings.process(host, directories)


async def test_gpg_keyrings_parses_default_baseline_fixture():
    """The two keyrings parse to the baseline's default.json fact, retyped."""
    host = CannedShellHost("\n".join(KEYRINGS_DEFAULT_OUTPUT))
    keyrings = await GpgKeyrings.process(host, APT_DIRECTORIES)
    assert keyrings == _keyring_entries(KEYRINGS_DEFAULT_FACT)
    assert host.last_shell == _keyrings_command(
        '"/etc/apt/trusted.gpg.d" "/etc/apt/keyrings" "/usr/share/keyrings"'
    )


async def test_gpg_keyrings_parses_asc_only_baseline_fixture():
    """Armored keyrings keep their subkey fingerprints (asc_only.json)."""
    host = CannedShellHost("\n".join(KEYRINGS_ASC_ONLY_OUTPUT))
    keyrings = await GpgKeyrings.process(host, "/etc/apt/trusted.gpg.d")
    assert keyrings == _keyring_entries(KEYRINGS_ASC_ONLY_FACT)


async def test_gpg_keyrings_parses_mixed_formats_baseline_fixture():
    """gpg, asc and kbx extensions become the format labels (mixed_formats.json)."""
    host = CannedShellHost("\n".join(KEYRINGS_MIXED_OUTPUT))
    keyrings = await GpgKeyrings.process(host, "/etc/apt/trusted.gpg.d")
    assert keyrings == _keyring_entries(KEYRINGS_MIXED_FACT)
    assert [keyring["format"] for keyring in keyrings] == ["gpg", "asc", "kbx"]


async def test_gpg_keyrings_parses_custom_directory_baseline_fixture():
    """A custom directory sweep parses to the baseline's custom_directory.json fact."""
    host = CannedShellHost("\n".join(KEYRINGS_CUSTOM_OUTPUT))
    keyrings = await GpgKeyrings.process(host, "/custom/keyring/path")
    assert keyrings == _keyring_entries(KEYRINGS_CUSTOM_FACT)
    assert host.last_shell == _keyrings_command('"/custom/keyring/path"')


async def test_gpg_keyrings_empty_directory_baseline_fixture():
    """A directory with no keyrings prints nothing and reads as []."""
    host = CannedShellHost("")
    assert await GpgKeyrings.process(host, "/empty/directory") == []
    assert host.last_shell == _keyrings_command('"/empty/directory"')


def test_gpg_keyrings_parse_skips_marker_only_keyrings():
    """An unreadable file contributes its marker only, which is no entry;
    lines before the first marker are ignored."""
    output = [
        "tru:t:1:1601454628:0:3:1:5",
        "KEYRING:/keyrings/unreadable.gpg",
        *KEYRINGS_CUSTOM_OUTPUT,
    ]
    assert GpgKeyrings.parse(output) == _keyring_entries(KEYRINGS_CUSTOM_FACT)


async def test_gpg_keyrings_seam_returns_copies():
    """Mutating a seam answer (keyrings, keys, subkeys) never touches state."""
    entries = _keyring_entries(KEYRINGS_ASC_ONLY_FACT)
    host = SimulatedHost(name="web1", gpg_keyrings=entries)
    answer = await GpgKeyrings.process(host, APT_DIRECTORIES)
    answer[0]["format"] = "mutated"
    answer[0]["keys"][0]["subkeys"][0]["length"] = 1
    answer[0]["keys"].clear()
    answer.pop()
    assert await GpgKeyrings.process(host, APT_DIRECTORIES) == entries


async def test_gpg_keyrings_gather_fact_forwards_directories():
    """gather_fact fans out over hosts, forwarding the directories argument."""
    entries = _keyring_entries(KEYRINGS_CUSTOM_FACT)
    host1 = SimulatedHost(name="web1", gpg_keyrings=entries)
    host2 = SimulatedHost(name="web2")
    keyrings = await gather_fact([host1, host2], GpgKeyrings, ["/custom/keyring/path"])
    assert keyrings == {"web1": entries, "web2": []}


# --- GpgTrust (original; canned output from a local gpg 2.4.7 run) ------------

# ``gpg --batch --export-ownertrust`` in a throwaway GNUPGHOME holding a
# freshly generated own key (which exports :6:) and a second imported key
# whose ownertrust was set to full.
TRUST_OUTPUT = [
    "# List of assigned trustvalues, created Sat 07 Nov 2026 16:06:29 GMT",
    '# (Use "gpg --import-ownertrust" to restore them)',
    "F8FCE1E7C282D5025B12B05E3B2CE2047E4DBF5B:6:",
    "90BF793E5302AE071A0BA68703BBACD22001B8B0:4:",
]
TRUST_FACT = [
    {
        "fingerprint": "F8FCE1E7C282D5025B12B05E3B2CE2047E4DBF5B",
        "ownertrust": 6,
        "level": "ultimate",
    },
    {
        "fingerprint": "90BF793E5302AE071A0BA68703BBACD22001B8B0",
        "ownertrust": 4,
        "level": "full",
    },
]


def test_gpg_trust_command():
    """The command exports ownertrust behind the || true guard."""
    assert GpgTrust.COMMAND == "gpg --batch --export-ownertrust 2>/dev/null || true"


async def test_gpg_trust_parses_export_output():
    """The comment headers are skipped and FPR:N: lines become entries."""
    host = CannedShellHost("\n".join(TRUST_OUTPUT))
    assert await GpgTrust.process(host) == TRUST_FACT
    assert host.last_shell == GpgTrust.COMMAND


@pytest.mark.parametrize(
    ("number", "level"),
    [
        (1, "unknown"),
        (2, "never"),
        (3, "marginal"),
        (4, "full"),
        (5, "ultimate"),
        (6, "ultimate"),
    ],
)
def test_gpg_trust_parse_maps_levels(number, level):
    """The numeric mapping verified against gpg 2.4.7 (5 and 6 both ultimate)."""
    fpr = "F8FCE1E7C282D5025B12B05E3B2CE2047E4DBF5B"
    assert GpgTrust.parse([f"{fpr}:{number}:"]) == [
        {"fingerprint": fpr, "ownertrust": number, "level": level}
    ]


def test_gpg_trust_parse_unrecognized_number_keeps_raw_value():
    """An out-of-range number maps to level unknown but keeps ownertrust."""
    fpr = "F8FCE1E7C282D5025B12B05E3B2CE2047E4DBF5B"
    assert GpgTrust.parse([f"{fpr}:9:"]) == [
        {"fingerprint": fpr, "ownertrust": 9, "level": "unknown"}
    ]


def test_gpg_trust_parse_skips_noise_lines():
    """Blanks, comments, non-integer trust fields and short lines are skipped."""
    output = [
        "",
        "   ",
        "# a comment",
        "F8FCE1E7C282D5025B12B05E3B2CE2047E4DBF5B:not-a-number:",
        "F8FCE1E7C282D5025B12B05E3B2CE2047E4DBF5B",
        ":4:",
        *TRUST_OUTPUT,
    ]
    assert GpgTrust.parse(output) == TRUST_FACT


async def test_gpg_trust_empty_output_is_empty_list():
    """An empty trust database (or a gpg failure masked by || true) reads as []."""
    assert await GpgTrust.process(CannedShellHost("")) == []


async def test_gpg_trust_seam():
    """The seam answers directly, bypassing the shell."""
    host = SimulatedHost(name="web1", gpg_trust=TRUST_FACT)
    assert await GpgTrust.process(host) == TRUST_FACT


async def test_gpg_trust_seam_returns_copies():
    """Mutating a seam answer never touches the simulated state."""
    host = SimulatedHost(name="web1", gpg_trust=TRUST_FACT)
    answer = await GpgTrust.process(host)
    answer[0]["level"] = "mutated"
    answer.clear()
    assert await GpgTrust.process(host) == TRUST_FACT


async def test_gpg_trust_gather_fact_fans_out():
    """gather_fact fans out over hosts; an unseeded host reads as []."""
    host1 = SimulatedHost(name="web1", gpg_trust=TRUST_FACT)
    host2 = SimulatedHost(name="web2")
    assert await gather_fact([host1, host2], GpgTrust) == {
        "web1": TRUST_FACT,
        "web2": [],
    }


# --- GpgKeyDetails (original; canned output from a local gpg 2.4.7 run) -------

# ``gpg --list-keys --with-colons -- 3B2CE2047E4DBF5B`` for the freshly
# generated "Trust Probe" key: ultimate validity, no expiration, no subkeys.
DETAILS_KEYID = "3B2CE2047E4DBF5B"
DETAILS_OUTPUT = [
    "tru:o:1:1794067700:1:3:1:5",
    "pub:u:2048:1:3B2CE2047E4DBF5B:1794067588:::u:::escarESCA::::::23::0:",
    "fpr:::::::::F8FCE1E7C282D5025B12B05E3B2CE2047E4DBF5B:",
    "uid:u::::1794067588::183CA80A66A0635E594A2FC1872FF5A6D76F3CCE::Trust Probe <probe@example.com>::::::::::0:",
]
DETAILS_FACT = {
    "id": "3B2CE2047E4DBF5B",
    "fingerprint": "F8FCE1E7C282D5025B12B05E3B2CE2047E4DBF5B",
    "validity": "u",
    "length": 2048,
    "algo": "1",
    "created": "1794067588",
    "capabilities": "escarESCA",
    "uids": ["Trust Probe <probe@example.com>"],
    "subkeys": [],
}


def test_gpg_key_details_command():
    """The command targets one keyid behind -- and the || true guard."""
    assert GpgKeyDetails.command(DETAILS_KEYID) == (
        "gpg --list-keys --with-colons -- 3B2CE2047E4DBF5B 2>/dev/null || true"
    )


def test_gpg_key_details_command_quotes_keyid():
    """A keyid with shell metacharacters is shlex-quoted."""
    keyid = "some id;rm -rf"
    assert GpgKeyDetails.command(keyid) == (
        f"gpg --list-keys --with-colons -- {shlex.quote(keyid)} 2>/dev/null || true"
    )


def test_gpg_key_details_command_rejects_empty_keyid():
    """An empty keyid raises ValueError instead of listing the whole keychain."""
    with pytest.raises(ValueError, match="non-empty key id"):
        GpgKeyDetails.command("")


async def test_gpg_key_details_rejects_empty_keyid_on_simulated_host():
    """The simulated path validates identically: empty keyid raises."""
    host = SimulatedHost(name="web1")
    with pytest.raises(ValueError, match="non-empty key id"):
        await GpgKeyDetails.process(host, "")


async def test_gpg_key_details_parses_probe_output():
    """The Trust Probe listing parses to the full metadata dict, no expires."""
    host = CannedShellHost("\n".join(DETAILS_OUTPUT))
    assert await GpgKeyDetails.process(host, DETAILS_KEYID) == DETAILS_FACT
    assert host.last_shell == GpgKeyDetails.command(DETAILS_KEYID)


def test_gpg_key_details_parse_keeps_expires_and_subkeys():
    """A non-empty expiration field and sub records surface in the metadata."""
    output = [
        "pub:u:4096:1:8D81803C0EBFCD88:1487788586:1893456000::-:::scESC::::::23::0:",
        "fpr:::::::::9DC858229FC7DD38854AE2D88D81803C0EBFCD88:",
        "uid:u::::1487788586::HASH1::Docker Release (CE deb) <docker@docker.com>::::::::::0:",
        "sub:-:4096:1:7EA0A9C3F273FCD8:1487788586::::",
        "fpr:::::::::6D085F9691E10E5E1F1D24C67EA0A9C3F273FCD8:",
    ]
    assert GpgKeyDetails.parse(output) == {
        "id": "8D81803C0EBFCD88",
        "fingerprint": "9DC858229FC7DD38854AE2D88D81803C0EBFCD88",
        "validity": "u",
        "length": 4096,
        "algo": "1",
        "created": "1487788586",
        "expires": "1893456000",
        "capabilities": "scESC",
        "uids": ["Docker Release (CE deb) <docker@docker.com>"],
        "subkeys": [
            {
                "id": "7EA0A9C3F273FCD8",
                "validity": "-",
                "length": 4096,
                "fingerprint": "6D085F9691E10E5E1F1D24C67EA0A9C3F273FCD8",
            },
        ],
    }


def test_gpg_key_details_parse_collects_all_uids():
    """Every uid record contributes, not just the parser's last one."""
    output = [
        *DETAILS_OUTPUT,
        "uid:u::::1794067588::HASH2::Trust Probe (work) <probe@work.example.com>::::::::::0:",
    ]
    metadata = GpgKeyDetails.parse(output)
    assert metadata is not None
    assert metadata["uids"] == [
        "Trust Probe <probe@example.com>",
        "Trust Probe (work) <probe@work.example.com>",
    ]


def test_gpg_key_details_parse_without_fpr_record():
    """No fpr record means an empty fingerprint, not a missing field."""
    output = [line for line in DETAILS_OUTPUT if not line.startswith("fpr:")]
    metadata = GpgKeyDetails.parse(output)
    assert metadata is not None
    assert metadata["fingerprint"] == ""


@pytest.mark.parametrize("output", [[], ["tru:o:1:1794067700:1:3:1:5"]])
def test_gpg_key_details_parse_absent_key_is_none(output):
    """No pub records (an absent key) parses to None."""
    assert GpgKeyDetails.parse(output) is None


async def test_gpg_key_details_absent_key_over_shell_is_none():
    """Empty stdout (absent key, or gpg missing behind || true) reads as None."""
    host = CannedShellHost("")
    assert await GpgKeyDetails.process(host, "DEADBEEFDEADBEEF") is None
    assert host.last_shell == GpgKeyDetails.command("DEADBEEFDEADBEEF")


async def test_gpg_key_details_seam_is_keyed_by_keyid():
    """The seam answers per keyid; an unseeded keyid reads as None."""
    host = SimulatedHost(name="web1", gpg_key_details={DETAILS_KEYID: DETAILS_FACT})
    assert await GpgKeyDetails.process(host, DETAILS_KEYID) == DETAILS_FACT
    assert await GpgKeyDetails.process(host, "DEADBEEFDEADBEEF") is None


async def test_gpg_key_details_seam_returns_copies():
    """Mutating a seam answer (dict, uids, subkeys) never touches state."""
    entry = {
        **DETAILS_FACT,
        "subkeys": [{"id": "7EA0A9C3F273FCD8", "validity": "-", "length": 4096}],
    }
    host = SimulatedHost(name="web1", gpg_key_details={DETAILS_KEYID: entry})
    answer = await GpgKeyDetails.process(host, DETAILS_KEYID)
    assert answer is not None
    answer["validity"] = "mutated"
    answer["uids"].append("mutated")
    answer["subkeys"][0]["length"] = 1
    answer["subkeys"].clear()
    assert await GpgKeyDetails.process(host, DETAILS_KEYID) == entry


async def test_gpg_key_details_gather_fact_forwards_keyid():
    """gather_fact fans out over hosts, forwarding the keyid argument."""
    host1 = SimulatedHost(name="web1", gpg_key_details={DETAILS_KEYID: DETAILS_FACT})
    host2 = SimulatedHost(name="web2")
    details = await gather_fact([host1, host2], GpgKeyDetails, DETAILS_KEYID)
    assert details == {"web1": DETAILS_FACT, "web2": None}


# --- GpgVerify (original; status streams from a local gpg 2.4.7 run) ----------

# ``gpg --batch --status-fd 1 --verify`` over a detached signature made by
# the Trust Probe key: a good signature, a tampered file (BADSIG) and an
# empty keyring (ERRSIG + NO_PUBKEY, exit 2 masked by the || true guard).
VERIFY_SIGNATURE = "/tmp/report.txt.sig"
VERIFY_FILE = "/tmp/report.txt"
VERIFY_GOODSIG_OUTPUT = [
    "[GNUPG:] NEWSIG probe@example.com",
    "[GNUPG:] KEY_CONSIDERED F8FCE1E7C282D5025B12B05E3B2CE2047E4DBF5B 0",
    "[GNUPG:] SIG_ID Rpi45LYaU15TQjjN024SqVt6Os0 2026-11-07 1794067733",
    "[GNUPG:] GOODSIG 3B2CE2047E4DBF5B Trust Probe <probe@example.com>",
    "[GNUPG:] VALIDSIG F8FCE1E7C282D5025B12B05E3B2CE2047E4DBF5B 2026-11-07 1794067733 0 4 0 1 10 00 F8FCE1E7C282D5025B12B05E3B2CE2047E4DBF5B",
    "[GNUPG:] TRUST_ULTIMATE 0 pgp probe@example.com",
]
VERIFY_BADSIG_OUTPUT = [
    "[GNUPG:] NEWSIG probe@example.com",
    "[GNUPG:] KEY_CONSIDERED F8FCE1E7C282D5025B12B05E3B2CE2047E4DBF5B 0",
    "[GNUPG:] BADSIG 3B2CE2047E4DBF5B Trust Probe <probe@example.com>",
    "[GNUPG:] FAILURE gpg-exit 33554433",
]
VERIFY_NO_PUBKEY_OUTPUT = [
    "[GNUPG:] NEWSIG probe@example.com",
    "[GNUPG:] ERRSIG 3B2CE2047E4DBF5B 1 10 00 1794067733 9 F8FCE1E7C282D5025B12B05E3B2CE2047E4DBF5B",
    "[GNUPG:] NO_PUBKEY 3B2CE2047E4DBF5B",
    "[GNUPG:] FAILURE gpg-exit 33554433",
]
VERIFY_GOODSIG_FACT = {
    "valid": True,
    "status": "goodsig",
    "keyid": "3B2CE2047E4DBF5B",
    "signer": "Trust Probe <probe@example.com>",
}
VERIFY_ERROR_FACT = {"valid": False, "status": "error"}


def test_gpg_verify_command_attached():
    """Without a file the signature is verified attached or cleartext."""
    assert GpgVerify.command(VERIFY_SIGNATURE) == (
        "gpg --batch --status-fd 1 --verify /tmp/report.txt.sig 2>/dev/null || true"
    )


def test_gpg_verify_command_detached():
    """A file argument makes the signature detached over that file."""
    assert GpgVerify.command(VERIFY_SIGNATURE, VERIFY_FILE) == (
        "gpg --batch --status-fd 1 --verify /tmp/report.txt.sig /tmp/report.txt"
        " 2>/dev/null || true"
    )


def test_gpg_verify_command_quotes_both_paths():
    """Signature and file paths with shell metacharacters are shlex-quoted."""
    signature = "/tmp/some sig;rm -rf.asc"
    file = "/tmp/some file.txt"
    assert GpgVerify.command(signature, file) == (
        f"gpg --batch --status-fd 1 --verify {shlex.quote(signature)}"
        f" {shlex.quote(file)} 2>/dev/null || true"
    )


def test_gpg_verify_command_rejects_empty_signature():
    """An empty signature raises ValueError."""
    with pytest.raises(ValueError, match="non-empty path"):
        GpgVerify.command("")


async def test_gpg_verify_rejects_empty_signature_on_simulated_host():
    """The simulated path validates identically: empty signature raises."""
    host = SimulatedHost(name="web1")
    with pytest.raises(ValueError, match="non-empty path"):
        await GpgVerify.process(host, "")


async def test_gpg_verify_parses_goodsig_stream():
    """GOODSIG wins over the VALIDSIG/TRUST lines: valid with keyid and signer."""
    host = CannedShellHost("\n".join(VERIFY_GOODSIG_OUTPUT))
    assert await GpgVerify.process(host, VERIFY_SIGNATURE, VERIFY_FILE) == (
        VERIFY_GOODSIG_FACT
    )
    assert host.last_shell == GpgVerify.command(VERIFY_SIGNATURE, VERIFY_FILE)


async def test_gpg_verify_parses_badsig_stream():
    """BADSIG reads invalid but still names the keyid and signer."""
    host = CannedShellHost("\n".join(VERIFY_BADSIG_OUTPUT))
    assert await GpgVerify.process(host, VERIFY_SIGNATURE, VERIFY_FILE) == {
        "valid": False,
        "status": "badsig",
        "keyid": "3B2CE2047E4DBF5B",
        "signer": "Trust Probe <probe@example.com>",
    }


async def test_gpg_verify_parses_no_pubkey_stream():
    """NO_PUBKEY beats the accompanying ERRSIG: the missing key is the diagnosis."""
    host = CannedShellHost("\n".join(VERIFY_NO_PUBKEY_OUTPUT))
    assert await GpgVerify.process(host, VERIFY_SIGNATURE, VERIFY_FILE) == {
        "valid": False,
        "status": "no_pubkey",
        "keyid": "3B2CE2047E4DBF5B",
    }


def test_gpg_verify_parse_errsig_alone():
    """An ERRSIG without NO_PUBKEY still names the signing keyid, no signer."""
    assert GpgVerify.parse(VERIFY_NO_PUBKEY_OUTPUT[:2]) == {
        "valid": False,
        "status": "errsig",
        "keyid": "3B2CE2047E4DBF5B",
    }


def test_gpg_verify_parse_goodsig_has_top_precedence():
    """GOODSIG decides even when every other recognized status also appears."""
    output = [
        *VERIFY_NO_PUBKEY_OUTPUT,
        *VERIFY_BADSIG_OUTPUT,
        *VERIFY_GOODSIG_OUTPUT,
    ]
    assert GpgVerify.parse(output) == VERIFY_GOODSIG_FACT


def test_gpg_verify_parse_badsig_beats_no_pubkey():
    """Without GOODSIG, BADSIG decides over NO_PUBKEY and ERRSIG."""
    output = [*VERIFY_NO_PUBKEY_OUTPUT, *VERIFY_BADSIG_OUTPUT]
    assert GpgVerify.parse(output) == {
        "valid": False,
        "status": "badsig",
        "keyid": "3B2CE2047E4DBF5B",
        "signer": "Trust Probe <probe@example.com>",
    }


@pytest.mark.parametrize(
    "output",
    [
        [],
        ["gpg: no valid OpenPGP data found."],
        ["[GNUPG:] NEWSIG probe@example.com"],
        ["[GNUPG:] FAILURE gpg-exit 33554433"],
    ],
)
def test_gpg_verify_parse_no_status_lines_is_error(output):
    """No recognized status line reads as the error answer."""
    assert GpgVerify.parse(output) == VERIFY_ERROR_FACT


async def test_gpg_verify_empty_output_is_error():
    """Empty stdout (a gpg failure masked by || true) reads as the error answer."""
    host = CannedShellHost("")
    assert await GpgVerify.process(host, VERIFY_SIGNATURE) == VERIFY_ERROR_FACT
    assert host.last_shell == GpgVerify.command(VERIFY_SIGNATURE)


async def test_gpg_verify_seam_is_keyed_by_signature():
    """The seam answers per signature path; file does not select between entries."""
    host = SimulatedHost(
        name="web1", gpg_verify={VERIFY_SIGNATURE: VERIFY_GOODSIG_FACT}
    )
    assert await GpgVerify.process(host, VERIFY_SIGNATURE) == VERIFY_GOODSIG_FACT
    assert await GpgVerify.process(host, VERIFY_SIGNATURE, VERIFY_FILE) == (
        VERIFY_GOODSIG_FACT
    )
    assert await GpgVerify.process(host, "/other.sig") == VERIFY_ERROR_FACT


async def test_gpg_verify_seam_returns_copies():
    """Mutating a seam answer never touches the simulated state."""
    host = SimulatedHost(
        name="web1", gpg_verify={VERIFY_SIGNATURE: VERIFY_GOODSIG_FACT}
    )
    answer = await GpgVerify.process(host, VERIFY_SIGNATURE)
    answer["signer"] = "mutated"
    answer["valid"] = False
    assert await GpgVerify.process(host, VERIFY_SIGNATURE) == VERIFY_GOODSIG_FACT


async def test_gpg_verify_gather_fact_forwards_signature_and_file():
    """gather_fact fans out over hosts, forwarding signature and file."""
    host1 = SimulatedHost(
        name="web1", gpg_verify={VERIFY_SIGNATURE: VERIFY_GOODSIG_FACT}
    )
    host2 = SimulatedHost(name="web2")
    results = await gather_fact(
        [host1, host2], GpgVerify, VERIFY_SIGNATURE, VERIFY_FILE
    )
    assert results == {"web1": VERIFY_GOODSIG_FACT, "web2": VERIFY_ERROR_FACT}


# --- gpg.key operation (the baseline gpg.key, stream-only sources) ------------------

# A stand-in ASCII-armored GPG public key. gpg.key installs the *content* of an
# IO stream (never a path), so the tests only need opaque, stable bytes; the
# simulated host stores them verbatim at the keyring path (it models no gpg
# --dearmor conversion).
GPG_KEY_BYTES = (
    b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
    b"mQIN reemote test key material\n"
    b"-----END PGP PUBLIC KEY BLOCK-----\n"
)

# The keyring paths of the baseline's gpg.key operation fixtures.
KEYRING_DEST = "/etc/apt/keyrings/test.gpg"
VENDOR_DEST = "/etc/apt/keyrings/vendor.gpg"

# The spool paths gpg.key uploads to, derived from the destination keyring --
# the tests spell the same call the operation makes. The spool file now lives
# inside a sha1-derived /tmp directory; the install rm -rf's the directory.
KEY_SPOOL = _key_spool_path(KEYRING_DEST)
KEY_SPOOL_DIR = KEY_SPOOL.rsplit("/", 1)[0]
VENDOR_SPOOL = _key_spool_path(VENDOR_DEST)
# gpg.import_key has no dest, so its spool comes from a fixed sha1 constant.
IMPORT_SPOOL = _import_spool_path()
IMPORT_SPOOL_DIR = IMPORT_SPOOL.rsplit("/", 1)[0]

# The vendor key of the baseline's remove_by_id fixture: key id ABCDEF1234567890,
# full fingerprint ABCDEF1234567890FEDCBA0987654321ABCDEF12, matched by the
# short id "0xABCDEF12" (a prefix of the existing id).
VENDOR_KEY = {
    "id": "ABCDEF1234567890",
    "validity": "-",
    "length": 4096,
    "subkeys": [],
    "fingerprint": "ABCDEF1234567890FEDCBA0987654321ABCDEF12",
    "uid_hash": "ABC123DEF456",
    "uid": "Vendor Key <vendor@example.com>",
}
VENDOR_KEYRING = {"path": VENDOR_DEST, "format": "gpg", "keys": [VENDOR_KEY]}


def test_gpg_key_upload_command_renders_as_an_unprivileged_scp():
    """The spool transfer has no shell(); it renders via __str__ and -- being a
    TransferCommand -- is never elevated, even though gpg.key's request is
    sudo=True for the install half: the log shows a plain ``scp ...``, never a
    misleading ``sudo scp ...``."""
    stream = io.BytesIO(GPG_KEY_BYTES)
    cmd = GpgKeyUploadCommand(src=stream, dest=KEY_SPOOL)
    assert not hasattr(cmd, "shell")
    assert str(cmd) == f"scp <stream> -> {KEY_SPOOL}"
    assert command_shell(cmd) == f"scp <stream> -> {KEY_SPOOL}"
    assert command_shell(cmd, sudo=True) == f"scp <stream> -> {KEY_SPOOL}"


def test_gpg_key_install_command_shell_and_elevation():
    """The install wraps mkdir, the dearmor-or-copy sniff (the baseline's
    local_file fixture command, plus ``--yes`` so an existing output is
    overwritten instead of refused), chmod and the spool cleanup in one
    ``sh -c`` so every step runs under the elevated shell. The paths are
    safe tokens, so shlex.quote leaves them unquoted; the armor marker
    carries spaces and is quoted."""
    cmd = GpgKeyInstallCommand(spool=KEY_SPOOL, dest=KEYRING_DEST)
    shell = cmd.shell()
    assert shell.startswith("sh -c ")
    inner = shlex.split(shell)[2]
    assert inner == (
        "mkdir -p /etc/apt/keyrings && "
        f"if grep -q 'BEGIN PGP PUBLIC KEY BLOCK' {KEY_SPOOL}; then "
        f"gpg --batch --yes --dearmor -o {KEYRING_DEST} {KEY_SPOOL}; "
        f"else cp {KEY_SPOOL} {KEYRING_DEST}; fi && "
        f"chmod 0644 {KEYRING_DEST} && rm -rf {KEY_SPOOL_DIR}"
    )
    assert command_shell(cmd, sudo=True).startswith("sudo sh -c ")
    assert command_shell(cmd, su=True) == f"su -c {shlex.quote(shell)}"


def test_gpg_key_install_command_no_dearmor_copies_verbatim():
    """dearmor=False drops the armor sniff entirely (the baseline's no_dearmor
    fixture: a plain cp), keeping the mkdir/chmod/cleanup chain."""
    cmd = GpgKeyInstallCommand(spool=KEY_SPOOL, dest=KEYRING_DEST, dearmor=False)
    inner = shlex.split(cmd.shell())[2]
    assert inner == (
        f"mkdir -p /etc/apt/keyrings && cp {KEY_SPOOL} {KEYRING_DEST} && "
        f"chmod 0644 {KEYRING_DEST} && rm -rf {KEY_SPOOL_DIR}"
    )
    assert "grep" not in inner


def test_gpg_key_install_command_quotes_unsafe_paths():
    """A dest carrying shell metacharacters is quoted everywhere it appears --
    the -o target, the cp destination, the chmod operand and the mkdir
    parent."""
    dest = "/etc/apt/keyrings/my key;rm.gpg"
    cmd = GpgKeyInstallCommand(spool=KEY_SPOOL, dest=dest)
    inner = shlex.split(cmd.shell())[2]
    quoted = shlex.quote(dest)
    assert f"gpg --batch --yes --dearmor -o {quoted} {KEY_SPOOL}" in inner
    assert f"cp {KEY_SPOOL} {quoted}" in inner
    assert f"chmod 0644 {quoted}" in inner
    assert "mkdir -p /etc/apt/keyrings" in inner


def test_gpg_key_remove_command_shell_and_elevation():
    """The whole-file removal is the baseline's remove_file fixture command (rm -f
    with the path); a single command elevates as a whole under sudo/su."""
    cmd = GpgKeyRemoveCommand(path=KEYRING_DEST)
    assert cmd.shell() == f"rm -f {KEYRING_DEST}"
    assert command_shell(cmd, sudo=True) == f"sudo rm -f {KEYRING_DEST}"
    assert command_shell(cmd, su=True) == f"su -c {shlex.quote(cmd.shell())}"
    unsafe = "/etc/apt/keyrings/my key.gpg"
    assert GpgKeyRemoveCommand(path=unsafe).shell() == f"rm -f {shlex.quote(unsafe)}"


def test_gpg_key_delete_keys_command_shell_and_elevation():
    """The selective delete is the baseline's remove_by_id fixture command: gpg
    scoped to the one keyring with --batch --yes, deleting by full
    fingerprint. Safe tokens stay unquoted (shlex.quote is the identity on
    them, so the rendering is shell-equivalent to upstream's double-quoted
    form); a single gpg invocation elevates as a whole."""
    cmd = GpgKeyDeleteKeysCommand(
        keyring=VENDOR_DEST, fingerprints=(VENDOR_KEY["fingerprint"],)
    )
    shell = cmd.shell()
    assert shell == (
        f"gpg --batch --yes --no-default-keyring --keyring {VENDOR_DEST}"
        f" --delete-keys {VENDOR_KEY['fingerprint']}"
    )
    assert command_shell(cmd, sudo=True) == f"sudo {shell}"
    assert command_shell(cmd, su=True) == f"su -c {shlex.quote(shell)}"


def test_gpg_key_delete_keys_command_joins_and_quotes():
    """Several fingerprints join with spaces in request order, and an unsafe
    keyring path is shell-quoted."""
    cmd = GpgKeyDeleteKeysCommand(
        keyring="/etc/apt/my keyring.gpg", fingerprints=("AAAA1111", "BBBB2222")
    )
    assert cmd.shell() == (
        "gpg --batch --yes --no-default-keyring"
        f" --keyring {shlex.quote('/etc/apt/my keyring.gpg')}"
        " --delete-keys AAAA1111 BBBB2222"
    )


def test_gpg_key_empty_cleanup_command_shell_and_elevation():
    """The pruning step is the baseline's list-or-remove pipeline wrapped in
    ``sh -c`` so the ``|| rm -f`` runs at the same privilege as the listing
    (a bare sudo prefix would elevate only the gpg at the head of the
    pipe)."""
    cmd = GpgKeyEmptyCleanupCommand(keyring=VENDOR_DEST)
    shell = cmd.shell()
    assert shell.startswith("sh -c ")
    inner = shlex.split(shell)[2]
    assert inner == (
        f"gpg --batch --no-default-keyring --keyring {VENDOR_DEST}"
        f" --list-keys 2>/dev/null | grep -q '^pub' || rm -f {VENDOR_DEST}"
    )
    assert command_shell(cmd, sudo=True).startswith("sudo sh -c ")


@pytest.mark.parametrize(
    ("existing", "clean", "expected"),
    [
        ("8D81803C0EBFCD88", "8D81803C0EBFCD88", True),  # long id exact
        ("8D81803C0EBFCD88", "0EBFCD88", True),  # short id suffix
        ("ABCDEF1234567890", "ABCDEF12", True),  # prefix (upstream form)
        (
            "8D81803C0EBFCD88",
            "9DC858229FC7DD38854AE2D88D81803C0EBFCD88",
            True,
        ),  # full fingerprint ends with the id
        ("8D81803C0EBFCD88", "DEADBEEF00000000", False),  # no relation
        ("8d81803c0ebfcd88", "8D81803C0EBFCD88", True),  # case-insensitive
    ],
)
def test_matches_keyid_forms(existing, clean, expected):
    """The baseline's _matches_keyid: exact, suffix, prefix and fingerprint forms."""
    assert _matches_keyid(existing, clean) is expected


def test_clean_keyid_strips_prefix_and_uppercases():
    """The baseline's cleaning: 0x/0X prefixes dropped, the rest uppercased."""
    assert _clean_keyid("0xabcdef12") == "ABCDEF12"
    assert _clean_keyid("0Xabcdef12") == "ABCDEF12"
    assert _clean_keyid("abcdef12") == "ABCDEF12"


async def test_keyring_file_exists_seam_reads_existing_files():
    """The private probe shares the files.FileExists seam: membership in
    existing_files, no shell."""
    host = SimulatedHost(name="h", existing_files={KEYRING_DEST})
    assert await _KeyringFileExists.process(host, KEYRING_DEST) is True
    assert await _KeyringFileExists.process(host, VENDOR_DEST) is False


async def test_keyring_file_exists_real_transport_probe():
    """Over the real transport the probe is ``test -e <path> && echo yes``;
    a missing file's non-zero exit maps to an empty stdout and False."""
    present = CannedShellHost("yes\n")
    assert await _KeyringFileExists.process(present, KEYRING_DEST) is True
    assert present.last_shell == f"test -e {KEYRING_DEST} && echo yes"
    assert await _KeyringFileExists.process(CannedShellHost(""), KEYRING_DEST) is False


async def test_gpg_key_op_yields_upload_then_install():
    """An absent dest installs: the spool transfer followed by the elevated
    install, with the change reason naming the stream and destination."""
    host = SimulatedHost(name="h")
    stream = io.BytesIO(GPG_KEY_BYTES)
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gpg.key(ctx, src=stream, dest=KEYRING_DEST)]
    assert ctx.changed
    assert ctx.reason == f"install GPG key from <stream> to {KEYRING_DEST}"
    assert commands == [
        GpgKeyUploadCommand(src=stream, dest=KEY_SPOOL),
        GpgKeyInstallCommand(spool=KEY_SPOOL, dest=KEYRING_DEST),
    ]


async def test_gpg_key_op_dearmor_false_forwarded():
    """dearmor=False rides into the install descriptor (the cp-only branch)."""
    host = SimulatedHost(name="h")
    stream = io.BytesIO(GPG_KEY_BYTES)
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in gpg.key(ctx, src=stream, dest=KEYRING_DEST, dearmor=False)
    ]
    assert commands[1] == GpgKeyInstallCommand(
        spool=KEY_SPOOL, dest=KEYRING_DEST, dearmor=False
    )


async def test_gpg_key_op_rejects_a_str_src():
    """The baseline's gpg.key takes a filename/URL the target host reads; reemote
    takes an IO stream, so a str src raises rather than being treated as a
    controller-side path."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="does not support a filename/URL src"):
        _ = [
            c
            async for c in gpg.key(
                ctx, src="https://download.docker.com/linux/debian/gpg", dest=KEYRING_DEST
            )
        ]


async def test_gpg_key_op_requires_src_for_install():
    """No src (and no keyserver -- the fetch is dropped) raises the baseline's
    installation validation, retyped to ValueError."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="`src` must be provided for installation"):
        _ = [c async for c in gpg.key(ctx, dest=KEYRING_DEST)]


@pytest.mark.parametrize("dest", [None, ""])
async def test_gpg_key_op_requires_dest_for_install(dest):
    """The baseline's no_dest fixture: `dest` must be provided for installation."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="`dest` must be provided for installation"):
        _ = [c async for c in gpg.key(ctx, src=io.BytesIO(GPG_KEY_BYTES), dest=dest)]


async def test_gpg_key_op_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors the baseline's global-arguments slot; op() already strips
    the name/sudo meta-parameters, the rest is ignored."""
    host = SimulatedHost(name="h")
    stream = io.BytesIO(GPG_KEY_BYTES)
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in gpg.key(ctx, src=stream, dest=KEYRING_DEST, future_flag=True)
    ]
    assert commands == [
        GpgKeyUploadCommand(src=stream, dest=KEY_SPOOL),
        GpgKeyInstallCommand(spool=KEY_SPOOL, dest=KEYRING_DEST),
    ]


async def test_gpg_key_op_existing_dest_is_unchanged():
    """Without keyid an existing dest file is left alone (upstream's no-keyid
    idempotence; the mode-repair chmod is dropped with the mode parameter)."""
    host = SimulatedHost(name="h", existing_files={KEYRING_DEST})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in gpg.key(ctx, src=io.BytesIO(GPG_KEY_BYTES), dest=KEYRING_DEST)
    ]
    assert not ctx.changed
    assert ctx.reason == f"GPG keyring {KEYRING_DEST} already exists"
    assert commands == []


async def test_gpg_key_op_keyid_idempotent_fingerprint_vs_long_id():
    """The baseline's local_file_idempotent_full_fingerprint_vs_long_id fixture: a
    requested full fingerprint matches the keyring's long id (the fingerprint
    ends with it), so the install is a no-op with upstream's noop wording."""
    keyring = {
        "path": VENDOR_DEST,
        "format": "gpg",
        "keys": [
            {
                "id": "8D81803C0EBFCD88",
                "validity": "-",
                "length": 4096,
                "uid": "Test Key <test@example.com>",
            }
        ],
    }
    host = SimulatedHost(name="h", gpg_keyrings=[keyring])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    keyid = ["9DC858229FC7DD38854AE2D88D81803C0EBFCD88"]
    commands = [
        c
        async for c in gpg.key(
            ctx, src=io.BytesIO(GPG_KEY_BYTES), dest=VENDOR_DEST, keyid=keyid
        )
    ]
    assert not ctx.changed
    assert ctx.reason == f"GPG keys {keyid} already exist in {VENDOR_DEST}"
    assert commands == []


async def test_gpg_key_op_keyid_partial_match_installs():
    """When only some requested keyids are present the install proceeds
    (upstream checks ALL requested ids)."""
    host = SimulatedHost(name="h", gpg_keyrings=[VENDOR_KEYRING])
    stream = io.BytesIO(GPG_KEY_BYTES)
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in gpg.key(
            ctx, src=stream, dest=VENDOR_DEST, keyid=["0xABCDEF12", "0xDEADBEEF"]
        )
    ]
    assert ctx.changed
    assert commands == [
        GpgKeyUploadCommand(src=stream, dest=VENDOR_SPOOL),
        GpgKeyInstallCommand(spool=VENDOR_SPOOL, dest=VENDOR_DEST),
    ]


async def test_gpg_key_op_keyid_absent_keyring_installs():
    """A keyid with no keyring entry for dest falls through to the install
    (upstream's fact-not-available path). A single str keyid is accepted."""
    host = SimulatedHost(name="h")
    stream = io.BytesIO(GPG_KEY_BYTES)
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in gpg.key(
            ctx, src=stream, dest=VENDOR_DEST, keyid="0xABCDEF12"
        )
    ]
    assert ctx.changed
    assert len(commands) == 2


async def test_gpg_key_op_removal_requires_dest_or_keyid_and_dirs():
    """The baseline's remove_no_dest_no_keyid fixture: for removal, either dest or
    both keyid and working_dirs must be provided."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="For removal, either `dest` or both"):
        _ = [c async for c in gpg.key(ctx, present=False)]
    with pytest.raises(ValueError, match="For removal, either `dest` or both"):
        _ = [c async for c in gpg.key(ctx, keyid="0xABCDEF12", present=False)]


async def test_gpg_key_op_removes_whole_file():
    """The baseline's remove_file fixture: dest without keyid removes the whole
    keyring file, idempotent on the existence probe."""
    host = SimulatedHost(name="h", existing_files={KEYRING_DEST})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gpg.key(ctx, dest=KEYRING_DEST, present=False)]
    assert ctx.changed
    assert ctx.reason == f"remove GPG keyring file {KEYRING_DEST}"
    assert commands == [GpgKeyRemoveCommand(path=KEYRING_DEST)]


async def test_gpg_key_op_remove_absent_file_is_unchanged():
    """An already-absent keyring file reports no change (rm -f would succeed
    silently, but the plan shows the no-op)."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gpg.key(ctx, dest=KEYRING_DEST, present=False)]
    assert not ctx.changed
    assert ctx.reason == f"GPG keyring file {KEYRING_DEST} already absent"
    assert commands == []


async def test_gpg_key_op_remove_by_id():
    """The baseline's remove_by_id fixture: the short id 0xABCDEF12 resolves to the
    key's full fingerprint, deleted from the one keyring then pruned if
    empty."""
    host = SimulatedHost(name="h", gpg_keyrings=[VENDOR_KEYRING])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in gpg.key(ctx, dest=VENDOR_DEST, keyid="0xABCDEF12", present=False)
    ]
    assert ctx.changed
    assert ctx.reason == f"remove GPG keys ['0xABCDEF12'] from {VENDOR_DEST}"
    assert commands == [
        GpgKeyDeleteKeysCommand(
            keyring=VENDOR_DEST, fingerprints=(VENDOR_KEY["fingerprint"],)
        ),
        GpgKeyEmptyCleanupCommand(keyring=VENDOR_DEST),
    ]


async def test_gpg_key_op_remove_by_full_fingerprint():
    """The baseline's remove_by_full_fingerprint fixture: a full-fingerprint keyid
    matches the key whose id is its last 16 hex digits."""
    keyring = {
        "path": VENDOR_DEST,
        "format": "gpg",
        "keys": [
            {
                "id": "112233440011CAFE",
                "validity": "-",
                "length": 4096,
                "subkeys": [],
                "fingerprint": "AABBCCDD11223344AABBCCDD112233440011CAFE",
                "uid_hash": "ABC123DEF456",
                "uid": "Vendor Key <vendor@example.com>",
            }
        ],
    }
    host = SimulatedHost(name="h", gpg_keyrings=[keyring])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in gpg.key(
            ctx,
            dest=VENDOR_DEST,
            keyid="0xAABBCCDD11223344AABBCCDD112233440011CAFE",
            present=False,
        )
    ]
    assert ctx.changed
    assert commands == [
        GpgKeyDeleteKeysCommand(
            keyring=VENDOR_DEST,
            fingerprints=("AABBCCDD11223344AABBCCDD112233440011CAFE",),
        ),
        GpgKeyEmptyCleanupCommand(keyring=VENDOR_DEST),
    ]


async def test_gpg_key_op_asc_partial_removal_raises():
    """The baseline's remove_by_id_asc_partial fixture: an armored keyring holding
    other keys cannot be edited in-place -- ValueError (upstream's
    OperationError) with upstream's message."""
    keyring = {
        "path": "/etc/apt/trusted.gpg.d/multi-key.asc",
        "format": "asc",
        "keys": [
            VENDOR_KEY,
            {
                "id": "111111111111AAAA",
                "validity": "-",
                "length": 4096,
                "subkeys": [],
                "fingerprint": "111111111111AAAA222222222222BBBB33333333",
                "uid_hash": "DEF456GHI789",
                "uid": "Key Two <two@example.com>",
            },
        ],
    }
    host = SimulatedHost(name="h", gpg_keyrings=[keyring])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="Cannot remove individual keys from armored"):
        _ = [
            c
            async for c in gpg.key(
                ctx, dest=keyring["path"], keyid="0xABCDEF12", present=False
            )
        ]


async def test_gpg_key_op_asc_all_match_removes_whole_file():
    """An armored keyring whose keys ALL match is removed whole (upstream's
    files.file present=False path -- no in-place edit needed)."""
    keyring = {"path": "/etc/apt/trusted.gpg.d/vendor.asc", "format": "asc", "keys": [VENDOR_KEY]}
    host = SimulatedHost(name="h", gpg_keyrings=[keyring])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in gpg.key(
            ctx, dest=keyring["path"], keyid="0xABCDEF12", present=False
        )
    ]
    assert ctx.changed
    assert commands == [GpgKeyRemoveCommand(path=keyring["path"])]


async def test_gpg_key_op_remove_no_match_is_unchanged():
    """A keyid matching nothing in dest's keyring (or with dest absent from
    the fact) reports no change."""
    host = SimulatedHost(name="h", gpg_keyrings=[VENDOR_KEYRING])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in gpg.key(ctx, dest=VENDOR_DEST, keyid="0xDEADBEEF", present=False)
    ]
    assert not ctx.changed
    assert ctx.reason == f"GPG keys ['0xDEADBEEF'] already absent from {VENDOR_DEST}"
    assert commands == []

    empty = SimulatedHost(name="h")
    ctx = OpContext(host=empty, facts=FactCache(empty, phase="prepare"))
    commands = [
        c
        async for c in gpg.key(ctx, dest=VENDOR_DEST, keyid="0xABCDEF12", present=False)
    ]
    assert not ctx.changed
    assert commands == []


async def test_gpg_key_op_remove_from_all_keyrings():
    """The baseline's remove_from_all_keyrings fixture: keyid + working_dirs scans
    every keyring under the directories and acts only on matching ones."""
    compromised = {
        "id": "COMPROMISED123567890",
        "validity": "-",
        "length": 4096,
        "subkeys": [],
        "fingerprint": "COMPROMISED123567890FEDCBA0987654321COMPROMISED123",
        "uid_hash": "ABC123DEF456",
        "uid": "Compromised Key <compromised@example.com>",
    }
    path = "/etc/apt/trusted.gpg.d/compromised.gpg"
    host = SimulatedHost(
        name="h",
        gpg_keyrings=[
            {"path": path, "format": "gpg", "keys": [compromised]},
            VENDOR_KEYRING,
        ],
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in gpg.key(
            ctx,
            keyid="0xCOMPROMISED123",
            present=False,
            working_dirs=["/etc/apt/trusted.gpg.d", "/etc/apt/keyrings", "/usr/share/keyrings"],
        )
    ]
    assert ctx.changed
    assert ctx.reason == f"remove GPG keys ['0xCOMPROMISED123'] from {path}"
    assert commands == [
        GpgKeyDeleteKeysCommand(
            keyring=path, fingerprints=(compromised["fingerprint"],)
        ),
        GpgKeyEmptyCleanupCommand(keyring=path),
    ]


async def test_gpg_key_op_scan_multiple_keyrings_joins_reasons():
    """A scan matching several keyrings yields each one's commands in fact
    order and joins the per-keyring reasons with '; '."""
    second = {
        "path": "/usr/share/keyrings/vendor-copy.gpg",
        "format": "gpg",
        "keys": [VENDOR_KEY],
    }
    host = SimulatedHost(name="h", gpg_keyrings=[VENDOR_KEYRING, second])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in gpg.key(
            ctx, keyid="0xABCDEF12", present=False, working_dirs=["/etc/apt/keyrings", "/usr/share/keyrings"]
        )
    ]
    assert ctx.changed
    assert ctx.reason == (
        f"remove GPG keys ['0xABCDEF12'] from {VENDOR_DEST}; "
        f"remove GPG keys ['0xABCDEF12'] from {second['path']}"
    )
    assert len(commands) == 4
    assert commands[0].keyring == VENDOR_DEST
    assert commands[2].keyring == second["path"]


async def test_gpg_key_op_remove_fingerprint_fallback_and_dedup():
    """A key with no fingerprint falls back to its id (upstream's .get
    default), and two keyids matching the same key dedupe to one
    fingerprint."""
    bare = {"id": "ABCDEF1234567890", "validity": "-", "length": 4096}
    keyring = {"path": VENDOR_DEST, "format": "gpg", "keys": [bare]}
    host = SimulatedHost(name="h", gpg_keyrings=[keyring])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in gpg.key(
            ctx, dest=VENDOR_DEST, keyid=["0xABCDEF12", "ABCDEF1234567890"], present=False
        )
    ]
    assert commands[0] == GpgKeyDeleteKeysCommand(
        keyring=VENDOR_DEST, fingerprints=("ABCDEF1234567890",)
    )


async def test_gpg_key_simulated_seam_spools_then_installs():
    """send_command maps the transfer onto remote_files (via put_file) and the
    install onto the keyring path while clearing the spool file. The keyring
    also lands in existing_files, file_stats (root, mode 644) and gpg_keyrings
    (an entry with empty keys -- key material is not modeled), so every
    consulted fact stays consistent."""
    host = SimulatedHost(name="h")
    stream = io.BytesIO(GPG_KEY_BYTES)
    await send_command(host, GpgKeyUploadCommand(src=stream, dest=KEY_SPOOL))
    assert host.remote_files[KEY_SPOOL] == GPG_KEY_BYTES
    await send_command(
        host, GpgKeyInstallCommand(spool=KEY_SPOOL, dest=KEYRING_DEST)
    )
    assert host.remote_files[KEYRING_DEST] == GPG_KEY_BYTES
    assert KEY_SPOOL not in host.remote_files
    assert KEYRING_DEST in host.existing_files
    assert host.file_stats[KEYRING_DEST] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(GPG_KEY_BYTES),
    }
    assert host.gpg_keyrings == [
        {"path": KEYRING_DEST, "format": "gpg", "keys": []}
    ]


async def test_gpg_key_simulated_seam_remove_clears_every_store():
    """The whole-file removal mutator drops the file from remote_files,
    existing_files, file_stats and gpg_keyrings together."""
    host = SimulatedHost(
        name="h",
        remote_files={VENDOR_DEST: GPG_KEY_BYTES},
        existing_files={VENDOR_DEST},
        file_stats={VENDOR_DEST: {"user": "root", "group": "root", "mode": 644, "size": 1}},
        gpg_keyrings=[VENDOR_KEYRING],
    )
    await send_command(host, GpgKeyRemoveCommand(path=VENDOR_DEST))
    assert VENDOR_DEST not in host.remote_files
    assert VENDOR_DEST not in host.existing_files
    assert VENDOR_DEST not in host.file_stats
    assert host.gpg_keyrings == []


async def test_gpg_key_simulated_seam_selective_delete_then_cleanup():
    """A selective delete drops only the named keys; the cleanup removes the
    file only once the last key is gone (a surviving key keeps the file)."""
    survivor = {
        "id": "111111111111AAAA",
        "validity": "-",
        "length": 4096,
        "fingerprint": "111111111111AAAA222222222222BBBB33333333",
    }
    keyring = {"path": VENDOR_DEST, "format": "gpg", "keys": [VENDOR_KEY, survivor]}
    host = SimulatedHost(
        name="h",
        remote_files={VENDOR_DEST: GPG_KEY_BYTES},
        existing_files={VENDOR_DEST},
        gpg_keyrings=[keyring],
    )
    await send_command(
        host,
        GpgKeyDeleteKeysCommand(
            keyring=VENDOR_DEST, fingerprints=(VENDOR_KEY["fingerprint"],)
        ),
    )
    await send_command(host, GpgKeyEmptyCleanupCommand(keyring=VENDOR_DEST))
    assert host.gpg_keyrings == [
        {"path": VENDOR_DEST, "format": "gpg", "keys": [survivor]}
    ]
    assert VENDOR_DEST in host.existing_files  # surviving key keeps the file

    await send_command(
        host,
        GpgKeyDeleteKeysCommand(
            keyring=VENDOR_DEST, fingerprints=(survivor["fingerprint"],)
        ),
    )
    await send_command(host, GpgKeyEmptyCleanupCommand(keyring=VENDOR_DEST))
    assert host.gpg_keyrings == []
    assert VENDOR_DEST not in host.existing_files
    assert VENDOR_DEST not in host.remote_files


async def test_gpg_key_end_to_end_installs_over_run():
    """Through run(): the apply phase spools the stream by scp then installs it,
    so the simulated host holds the keyring file and the spool file is gone.
    The stream is one object captured by the deploy closure, so both phases
    pass the identical object and the determinism guard holds. The log shows
    the transfer unprivileged and only the install elevated."""
    stream = io.BytesIO(GPG_KEY_BYTES)

    def key_deploy(host):
        yield op(gpg.key, name="Add vendor GPG key", src=stream, dest=KEYRING_DEST, sudo=True)

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], key_deploy, host_factory=lambda entry: host
    )
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (
        GpgKeyUploadCommand(src=stream, dest=KEY_SPOOL),
        GpgKeyInstallCommand(spool=KEY_SPOOL, dest=KEYRING_DEST),
    )
    assert host.remote_files[KEYRING_DEST] == GPG_KEY_BYTES
    assert KEY_SPOOL not in host.remote_files
    assert KEYRING_DEST in host.existing_files
    log = format_log(report)
    assert f"    $ scp <stream> -> {KEY_SPOOL}" in log
    assert "    $ sudo sh -c " in log
    plan = format_plan(plans)
    assert f"install GPG key from <stream> to {KEYRING_DEST}" in plan


async def test_gpg_key_end_to_end_second_install_is_noop_in_apply():
    """Two identical installs in one deploy: the prepare plan shows both
    changed (the shared prepare cache never sees the first install), but the
    apply log's fresh per-step cache sees the installed file and reports the
    second as a no-op -- the existence idempotence observed end to end."""
    stream = io.BytesIO(GPG_KEY_BYTES)

    def key_deploy(host):
        yield op(gpg.key, name="Install once", src=stream, dest=KEYRING_DEST, sudo=True)
        yield op(gpg.key, name="Install twice", src=stream, dest=KEYRING_DEST, sudo=True)

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], key_deploy)
    assert plans["h"][0].changed
    assert plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    first, second = report.log
    assert first.changed
    assert not second.changed
    assert second.commands == ()
    assert second.reason == f"GPG keyring {KEYRING_DEST} already exists"


async def test_gpg_key_end_to_end_removal_over_run():
    """A seeded keyring file is removed through run(), and a second identical
    removal in the same deploy is a no-op in the apply log (the probe sees the
    file gone)."""

    def remove_deploy(host):
        yield op(gpg.key, name="Remove keyring", dest=KEYRING_DEST, present=False, sudo=True)
        yield op(gpg.key, name="Remove again", dest=KEYRING_DEST, present=False, sudo=True)

    host = SimulatedHost(
        name="h",
        remote_files={KEYRING_DEST: GPG_KEY_BYTES},
        existing_files={KEYRING_DEST},
    )
    runtimes, plans = await run_prepare([host], remove_deploy)
    report = await run_apply(runtimes, plans=plans)
    first, second = report.log
    assert first.changed
    assert first.commands == (GpgKeyRemoveCommand(path=KEYRING_DEST),)
    assert not second.changed
    assert KEYRING_DEST not in host.existing_files
    assert KEYRING_DEST not in host.remote_files
    log = format_log(report)
    assert f"    $ sudo rm -f {KEYRING_DEST}" in log


async def test_gpg_key_dry_run_plans_without_installing():
    """dry=True materializes the plan (changed, two commands) but sends
    nothing, so no keyring file and no spool file is written."""
    stream = io.BytesIO(GPG_KEY_BYTES)

    def key_deploy(host):
        yield op(gpg.key, name="Add vendor GPG key", src=stream, dest=KEYRING_DEST, sudo=True)

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


async def test_gpg_key_fresh_stream_per_phase_trips_determinism():
    """The stream is compared by identity, so a deploy that builds a new one per
    invocation diverges between the prepare and apply phases and the guard
    raises -- the reason the example hoists its stream to a module constant."""

    def key_deploy(host):
        yield op(
            gpg.key,
            name="Add vendor GPG key",
            src=io.BytesIO(GPG_KEY_BYTES),
            dest=KEYRING_DEST,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], key_deploy)
    with pytest.raises(DeterminismError):
        await run_apply(runtimes, plans=plans)


# --- gpg.dearmor operation (the baseline gpg.dearmor) -------------------------------

DEARMOR_SRC = "/tmp/test.asc"
DEARMOR_DEST = "/tmp/test.gpg"


def test_gpg_dearmor_command_shell_and_elevation():
    """The conversion wraps mkdir, gpg --batch --yes --dearmor (the baseline's
    basic fixture command, plus ``--yes`` so the stateless conversion
    overwrites an existing output instead of failing on it) and chmod in
    one ``sh -c`` so every step elevates together."""
    cmd = GpgDearmorCommand(src=DEARMOR_SRC, dest=DEARMOR_DEST)
    shell = cmd.shell()
    assert shell.startswith("sh -c ")
    inner = shlex.split(shell)[2]
    assert inner == (
        f"mkdir -p /tmp && gpg --batch --yes --dearmor -o {DEARMOR_DEST} {DEARMOR_SRC}"
        f" && chmod 0644 {DEARMOR_DEST}"
    )
    assert command_shell(cmd, sudo=True).startswith("sudo sh -c ")
    assert command_shell(cmd, su=True) == f"su -c {shlex.quote(shell)}"


def test_gpg_dearmor_command_quotes_unsafe_paths():
    """Both remote paths and the mkdir parent are shell-quoted."""
    cmd = GpgDearmorCommand(src="/tmp/my key.asc", dest="/tmp/out dir/key.gpg")
    inner = shlex.split(cmd.shell())[2]
    assert f"mkdir -p {shlex.quote('/tmp/out dir')}" in inner
    assert f"-o {shlex.quote('/tmp/out dir/key.gpg')} {shlex.quote('/tmp/my key.asc')}" in inner
    assert f"chmod 0644 {shlex.quote('/tmp/out dir/key.gpg')}" in inner


async def test_gpg_dearmor_op_is_stateless():
    """dearmor always reports a change and yields its one command, reading no
    fact (the baseline marks it non-idempotent)."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gpg.dearmor(ctx, src=DEARMOR_SRC, dest=DEARMOR_DEST)]
    assert ctx.changed
    assert ctx.reason == f"dearmor {DEARMOR_SRC} to {DEARMOR_DEST}"
    assert commands == [GpgDearmorCommand(src=DEARMOR_SRC, dest=DEARMOR_DEST)]


@pytest.mark.parametrize(
    ("src", "dest", "message"),
    [
        ("", DEARMOR_DEST, "non-empty src"),
        (DEARMOR_SRC, "", "non-empty dest"),
    ],
)
async def test_gpg_dearmor_op_validates_paths(src, dest, message):
    """Empty remote paths raise ValueError before any command is yielded."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match=message):
        _ = [c async for c in gpg.dearmor(ctx, src=src, dest=dest)]


async def test_gpg_dearmor_op_absorbs_unknown_kwargs():
    """``**kwargs`` absorbs the baseline's global arguments (op() strips the
    name/sudo meta-parameters)."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in gpg.dearmor(
            ctx, src=DEARMOR_SRC, dest=DEARMOR_DEST, future_flag=True
        )
    ]
    assert commands == [GpgDearmorCommand(src=DEARMOR_SRC, dest=DEARMOR_DEST)]


async def test_gpg_dearmor_simulated_seam_copies_remote_content():
    """send_command maps the conversion onto a remote-to-remote copy: the src
    stays, the dest gains the bytes plus existing_files/file_stats/gpg_keyrings
    entries (the simulation models no actual dearmoring)."""
    host = SimulatedHost(name="h", remote_files={DEARMOR_SRC: GPG_KEY_BYTES})
    await send_command(host, GpgDearmorCommand(src=DEARMOR_SRC, dest=DEARMOR_DEST))
    assert host.remote_files[DEARMOR_SRC] == GPG_KEY_BYTES
    assert host.remote_files[DEARMOR_DEST] == GPG_KEY_BYTES
    assert DEARMOR_DEST in host.existing_files
    assert host.file_stats[DEARMOR_DEST]["mode"] == 644
    assert host.gpg_keyrings == [{"path": DEARMOR_DEST, "format": "gpg", "keys": []}]


async def test_gpg_dearmor_end_to_end_over_run():
    """Through run(): the stateless conversion always applies, writing the
    dest from the seeded src; the log shows the elevated sh -c chain."""

    def dearmor_deploy(host):
        yield op(
            gpg.dearmor, name="Convert key to binary", src=DEARMOR_SRC, dest=DEARMOR_DEST
        )

    host = SimulatedHost(name="h", remote_files={DEARMOR_SRC: GPG_KEY_BYTES})
    _plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], dearmor_deploy, host_factory=lambda entry: host
    )
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (GpgDearmorCommand(src=DEARMOR_SRC, dest=DEARMOR_DEST),)
    assert host.remote_files[DEARMOR_DEST] == GPG_KEY_BYTES
    assert f"    $ {GpgDearmorCommand(src=DEARMOR_SRC, dest=DEARMOR_DEST).shell()}" in (
        format_log(report)
    )


async def test_gpg_dearmor_dry_run_sends_nothing():
    """dry=True plans the conversion but writes no dest file."""

    def dearmor_deploy(host):
        yield op(
            gpg.dearmor, name="Convert key to binary", src=DEARMOR_SRC, dest=DEARMOR_DEST
        )

    host = SimulatedHost(name="h", remote_files={DEARMOR_SRC: GPG_KEY_BYTES})
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        dearmor_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert DEARMOR_DEST not in host.remote_files


# --- gpg.import_key operation (reemote original) -------------------------------


def test_gpg_import_upload_command_renders_as_an_unprivileged_scp():
    """The import spool transfer has no shell(); it renders via __str__ and is
    never elevated, even under a sudo-flagged request."""
    stream = io.BytesIO(GPG_KEY_BYTES)
    cmd = GpgImportUploadCommand(src=stream, dest=IMPORT_SPOOL)
    assert not hasattr(cmd, "shell")
    assert str(cmd) == f"scp <stream> -> {IMPORT_SPOOL}"
    assert command_shell(cmd, sudo=True) == f"scp <stream> -> {IMPORT_SPOOL}"


def test_gpg_import_command_shell_and_elevation():
    """The import wraps gpg --batch --yes --import and the spool cleanup in one
    sh -c so both steps run at the request's privilege."""
    cmd = GpgImportCommand(spool=IMPORT_SPOOL)
    shell = cmd.shell()
    assert shell.startswith("sh -c ")
    assert shlex.split(shell)[2] == (
        f"gpg --batch --yes --import {IMPORT_SPOOL} && rm -rf {IMPORT_SPOOL_DIR}"
    )
    assert command_shell(cmd, sudo=True).startswith("sudo sh -c ")
    assert command_shell(cmd, su=True) == f"su -c {shlex.quote(shell)}"
    unsafe = "/tmp/my spool;rm.gpg"
    assert shlex.quote(unsafe) in GpgImportCommand(spool=unsafe).shell()


async def test_gpg_import_key_op_without_keyid_always_imports():
    """Without keyid the stream's fingerprint is unknown controller-side, so
    the import always runs: upload then import, with the stream-naming
    reason."""
    host = SimulatedHost(name="h")
    stream = io.BytesIO(GPG_KEY_BYTES)
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gpg.import_key(ctx, src=stream)]
    assert ctx.changed
    assert ctx.reason == "import GPG key from <stream>"
    assert commands == [
        GpgImportUploadCommand(src=stream, dest=IMPORT_SPOOL),
        GpgImportCommand(spool=IMPORT_SPOOL),
    ]


async def test_gpg_import_key_op_without_keyid_stays_changed_on_repeat():
    """The documented non-idempotence: even with the key already imported, a
    keyid-less import reports a change again."""
    host = SimulatedHost(name="h", gpg_keys=[VENDOR_KEY])
    stream = io.BytesIO(GPG_KEY_BYTES)
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gpg.import_key(ctx, src=stream)]
    assert ctx.changed
    assert len(commands) == 2


@pytest.mark.parametrize("keyid", ["ABCDEF1234567890", "34567890", "0xABCDEF1234567890"])
async def test_gpg_import_key_op_keyid_present_is_noop(keyid):
    """With keyid the import is idempotent against GpgKeys: a matching long
    id, short-id suffix or 0x-prefixed form all report the key imported."""
    host = SimulatedHost(name="h", gpg_keys=[VENDOR_KEY])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gpg.import_key(ctx, src=io.BytesIO(GPG_KEY_BYTES), keyid=keyid)]
    assert not ctx.changed
    assert ctx.reason == f"GPG key {keyid} already imported"
    assert commands == []


async def test_gpg_import_key_op_keyid_absent_imports():
    """A keyid matching nothing in the keychain imports."""
    host = SimulatedHost(name="h", gpg_keys=[VENDOR_KEY])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in gpg.import_key(ctx, src=io.BytesIO(GPG_KEY_BYTES), keyid="DEADBEEF")
    ]
    assert ctx.changed
    assert len(commands) == 2


async def test_gpg_import_key_op_secret_checks_the_secret_keychain():
    """secret=True consults GpgSecretKeys: a public-only key does not satisfy
    a secret import, a secret one does."""
    stream = io.BytesIO(GPG_KEY_BYTES)
    public_only = SimulatedHost(name="h", gpg_keys=[VENDOR_KEY])
    ctx = OpContext(host=public_only, facts=FactCache(public_only, phase="prepare"))
    commands = [
        c async for c in gpg.import_key(ctx, src=stream, keyid="ABCDEF12", secret=True)
    ]
    assert ctx.changed
    assert len(commands) == 2

    secret_host = SimulatedHost(name="h", gpg_secret_keys=[VENDOR_KEY])
    ctx = OpContext(host=secret_host, facts=FactCache(secret_host, phase="prepare"))
    commands = [
        c async for c in gpg.import_key(ctx, src=stream, keyid="ABCDEF12", secret=True)
    ]
    assert not ctx.changed
    assert commands == []


async def test_gpg_import_key_op_rejects_a_str_src():
    """A str src raises the gpg.key-consistent ValueError."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="does not support a filename/URL src"):
        _ = [c async for c in gpg.import_key(ctx, src="/tmp/key.asc")]


async def test_gpg_import_key_op_absorbs_unknown_kwargs():
    """``**kwargs`` absorbs the baseline's global arguments."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in gpg.import_key(ctx, src=io.BytesIO(GPG_KEY_BYTES), future_flag=True)
    ]
    assert len(commands) == 2


async def test_gpg_import_key_simulated_seam_adds_a_stub_key():
    """send_command maps the pair onto spool-then-import: the spool is gone and
    a content-hash stub key lands in gpg_keys; re-importing the same bytes
    adds no duplicate."""
    host = SimulatedHost(name="h")
    stream = io.BytesIO(GPG_KEY_BYTES)
    await send_command(host, GpgImportUploadCommand(src=stream, dest=IMPORT_SPOOL))
    await send_command(host, GpgImportCommand(spool=IMPORT_SPOOL))
    assert IMPORT_SPOOL not in host.remote_files
    fingerprint = hashlib.sha256(GPG_KEY_BYTES).hexdigest().upper()[:40]
    assert host.gpg_keys == [
        {
            "id": fingerprint[-16:],
            "validity": "u",
            "length": 4096,
            "fingerprint": fingerprint,
            "subkeys": [],
        }
    ]
    await send_command(host, GpgImportUploadCommand(src=stream, dest=IMPORT_SPOOL))
    await send_command(host, GpgImportCommand(spool=IMPORT_SPOOL))
    assert len(host.gpg_keys) == 1


async def test_gpg_import_key_end_to_end_second_import_with_keyid_is_noop():
    """Two keyid-guarded imports of the same stream: the prepare plan shows
    both changed, but the apply log's fresh per-step cache sees the stub key
    the first import added (the keyid names its content-hash fingerprint)."""
    stream = io.BytesIO(GPG_KEY_BYTES)
    fingerprint = hashlib.sha256(GPG_KEY_BYTES).hexdigest().upper()[:40]

    def import_deploy(host):
        yield op(gpg.import_key, name="Import once", src=stream, keyid=fingerprint)
        yield op(gpg.import_key, name="Import twice", src=stream, keyid=fingerprint)

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], import_deploy)
    assert plans["h"][0].changed
    assert plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    first, second = report.log
    assert first.changed
    assert not second.changed
    assert second.reason == f"GPG key {fingerprint} already imported"
    log = format_log(report)
    assert f"    $ scp <stream> -> {IMPORT_SPOOL}" in log


async def test_gpg_import_key_dry_run_sends_nothing():
    """dry=True plans the import but stores no spool file and no key."""
    stream = io.BytesIO(GPG_KEY_BYTES)

    def import_deploy(host):
        yield op(gpg.import_key, name="Import key", src=stream, sudo=True)

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        import_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert len(planned.commands) == 2
    assert host.remote_files == {}
    assert host.gpg_keys == []


async def test_gpg_import_key_fresh_stream_per_phase_trips_determinism():
    """A fresh stream per invocation diverges between the phases and the
    determinism guard raises."""

    def import_deploy(host):
        yield op(gpg.import_key, name="Import key", src=io.BytesIO(GPG_KEY_BYTES))

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], import_deploy)
    with pytest.raises(DeterminismError):
        await run_apply(runtimes, plans=plans)


# --- gpg.export_key operation (reemote original) -------------------------------

EXPORT_DEST = "/tmp/exported.gpg"


@pytest.mark.parametrize(
    ("armor", "secret", "expected"),
    [
        (False, False, "gpg --batch --yes --export ABCDEF12 > /tmp/exported.gpg"),
        (True, False, "gpg --batch --yes --armor --export ABCDEF12 > /tmp/exported.gpg"),
        (
            False,
            True,
            (
                "gpg --batch --yes --pinentry-mode loopback --export-secret-keys"
                " ABCDEF12 > /tmp/exported.gpg"
            ),
        ),
        (
            True,
            True,
            (
                "gpg --batch --yes --armor --pinentry-mode loopback --export-secret-keys"
                " ABCDEF12 > /tmp/exported.gpg"
            ),
        ),
    ],
)
def test_gpg_export_command_shell_variants(armor, secret, expected):
    """The export renders mkdir plus the redirecting gpg in one sh -c; armor
    and secret (with its fail-fast loopback pinentry) toggle their flags."""
    cmd = GpgExportCommand(keyid="ABCDEF12", dest=EXPORT_DEST, armor=armor, secret=secret)
    shell = cmd.shell()
    assert shell.startswith("sh -c ")
    assert shlex.split(shell)[2] == f"mkdir -p /tmp && {expected}"
    assert command_shell(cmd, sudo=True).startswith("sudo sh -c ")
    assert command_shell(cmd, su=True) == f"su -c {shlex.quote(shell)}"


def test_gpg_export_command_quotes_unsafe_values():
    """A keyid or dest with shell metacharacters is quoted everywhere."""
    cmd = GpgExportCommand(keyid="a b;rm", dest="/tmp/my key.gpg")
    inner = shlex.split(cmd.shell())[2]
    assert f"--export {shlex.quote('a b;rm')} > {shlex.quote('/tmp/my key.gpg')}" in inner


async def test_gpg_export_key_op_is_stateless():
    """The export always yields and always reports a change, reading no fact
    -- even when the dest file already exists."""
    host = SimulatedHost(name="h", remote_files={EXPORT_DEST: b"old"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gpg.export_key(ctx, keyid="ABCDEF12", dest=EXPORT_DEST)]
    assert ctx.changed
    assert ctx.reason == f"export GPG key ABCDEF12 to {EXPORT_DEST}"
    assert commands == [GpgExportCommand(keyid="ABCDEF12", dest=EXPORT_DEST)]


@pytest.mark.parametrize(
    ("keyid", "dest", "message"),
    [("", EXPORT_DEST, "non-empty keyid"), ("ABCDEF12", "", "non-empty dest")],
)
async def test_gpg_export_key_op_validates(keyid, dest, message):
    """Empty keyid/dest raise ValueError before any command is yielded."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match=message):
        _ = [c async for c in gpg.export_key(ctx, keyid=keyid, dest=dest)]


async def test_gpg_export_key_op_absorbs_unknown_kwargs():
    """``**kwargs`` absorbs the baseline's global arguments; armor/secret ride into
    the descriptor."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in gpg.export_key(
            ctx, keyid="ABCDEF12", dest=EXPORT_DEST, armor=True, secret=True, future_flag=1
        )
    ]
    assert commands == [
        GpgExportCommand(keyid="ABCDEF12", dest=EXPORT_DEST, armor=True, secret=True)
    ]


async def test_gpg_export_key_simulated_seam_writes_a_placeholder_file():
    """send_command stores a deterministic placeholder export (no key material
    is modeled) visible to the existence/content seams, but not gpg_keyrings."""
    host = SimulatedHost(name="h")
    await send_command(
        host, GpgExportCommand(keyid="ABCDEF12", dest=EXPORT_DEST, armor=True)
    )
    data = host.remote_files[EXPORT_DEST]
    assert b"BEGIN PGP PUBLIC KEY BLOCK" in data
    assert b"simulated export of ABCDEF12" in data
    assert EXPORT_DEST in host.existing_files
    assert host.file_stats[EXPORT_DEST]["size"] == len(data)
    assert host.gpg_keyrings == []
    await send_command(
        host, GpgExportCommand(keyid="ABCDEF12", dest=EXPORT_DEST, armor=True, secret=True)
    )
    assert b"BEGIN PGP PRIVATE KEY BLOCK" in host.remote_files[EXPORT_DEST]


async def test_gpg_export_key_end_to_end_over_run():
    """Through run(): the stateless export always applies; the log shows the
    elevated sh -c chain and a second export in the deploy stays changed."""

    def export_deploy(host):
        yield op(gpg.export_key, name="Export once", keyid="ABCDEF12", dest=EXPORT_DEST, sudo=True)
        yield op(gpg.export_key, name="Export twice", keyid="ABCDEF12", dest=EXPORT_DEST, sudo=True)

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], export_deploy)
    report = await run_apply(runtimes, plans=plans)
    first, second = report.log
    assert first.changed
    assert second.changed  # stateless: no idempotence to observe
    assert EXPORT_DEST in host.remote_files
    assert "    $ sudo sh -c " in format_log(report)


async def test_gpg_export_key_dry_run_sends_nothing():
    """dry=True plans the export but writes no file."""

    def export_deploy(host):
        yield op(gpg.export_key, name="Export key", keyid="ABCDEF12", dest=EXPORT_DEST)

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        export_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert EXPORT_DEST not in host.remote_files


# --- gpg.delete_key operation (reemote original) -------------------------------


@pytest.mark.parametrize(
    ("secret", "action"),
    [(False, "--delete-keys"), (True, "--delete-secret-and-public-keys")],
)
def test_gpg_delete_key_command_shell_and_elevation(secret, action):
    """A single gpg invocation with --batch --yes; secret deletes both halves.
    A bare sudo prefix elevates the whole command."""
    cmd = GpgDeleteKeyCommand(fingerprint=VENDOR_KEY["fingerprint"], secret=secret)
    shell = cmd.shell()
    assert shell == f"gpg --batch --yes {action} {VENDOR_KEY['fingerprint']}"
    assert command_shell(cmd, sudo=True) == f"sudo {shell}"
    assert command_shell(cmd, su=True) == f"su -c {shlex.quote(shell)}"


async def test_gpg_delete_key_op_resolves_short_id_to_fingerprint():
    """A short keyid resolves to the matching entry's full fingerprint before
    deleting (gpg refuses ambiguous short ids under --batch)."""
    host = SimulatedHost(name="h", gpg_keys=[VENDOR_KEY])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gpg.delete_key(ctx, keyid="0x34567890")]
    assert ctx.changed
    assert ctx.reason == "delete GPG key 0x34567890"
    assert commands == [GpgDeleteKeyCommand(fingerprint=VENDOR_KEY["fingerprint"])]


async def test_gpg_delete_key_op_absent_is_noop():
    """A keyid matching nothing reports no change."""
    host = SimulatedHost(name="h", gpg_keys=[VENDOR_KEY])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gpg.delete_key(ctx, keyid="DEADBEEF")]
    assert not ctx.changed
    assert ctx.reason == "GPG key DEADBEEF already absent"
    assert commands == []


async def test_gpg_delete_key_op_falls_back_to_the_key_id():
    """An entry with no fingerprint attached deletes by its id (the gpg.key
    selective-removal fallback)."""
    bare = {"id": "8D81803C0EBFCD88", "validity": "-", "length": 4096}
    host = SimulatedHost(name="h", gpg_keys=[bare])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gpg.delete_key(ctx, keyid="0EBFCD88")]
    assert commands == [GpgDeleteKeyCommand(fingerprint="8D81803C0EBFCD88")]


async def test_gpg_delete_key_op_secret_checks_the_secret_keychain():
    """secret=True consults GpgSecretKeys and yields the both-halves delete."""
    host = SimulatedHost(name="h", gpg_secret_keys=[VENDOR_KEY])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gpg.delete_key(ctx, keyid="ABCDEF12", secret=True)]
    assert ctx.changed
    assert commands == [
        GpgDeleteKeyCommand(fingerprint=VENDOR_KEY["fingerprint"], secret=True)
    ]

    public_only = SimulatedHost(name="h", gpg_keys=[VENDOR_KEY])
    ctx = OpContext(host=public_only, facts=FactCache(public_only, phase="prepare"))
    commands = [c async for c in gpg.delete_key(ctx, keyid="ABCDEF12", secret=True)]
    assert not ctx.changed
    assert commands == []


async def test_gpg_delete_key_op_rejects_empty_keyid():
    """An empty keyid raises ValueError before any fact is read."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="non-empty keyid"):
        _ = [c async for c in gpg.delete_key(ctx, keyid="")]


async def test_gpg_delete_key_op_absorbs_unknown_kwargs():
    """``**kwargs`` absorbs the baseline's global arguments."""
    host = SimulatedHost(name="h", gpg_keys=[VENDOR_KEY])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gpg.delete_key(ctx, keyid="ABCDEF12", future_flag=True)]
    assert len(commands) == 1


async def test_gpg_delete_key_simulated_seam_prunes_keychain_and_trust():
    """A public delete drops the key from gpg_keys and its ownertrust, keeping
    the secret half; a secret delete drops both halves."""
    trust = {"fingerprint": VENDOR_KEY["fingerprint"], "ownertrust": 4, "level": "full"}
    host = SimulatedHost(
        name="h",
        gpg_keys=[VENDOR_KEY],
        gpg_secret_keys=[VENDOR_KEY],
        gpg_trust=[trust],
    )
    await send_command(
        host, GpgDeleteKeyCommand(fingerprint=VENDOR_KEY["fingerprint"])
    )
    assert host.gpg_keys == []
    assert host.gpg_secret_keys == [VENDOR_KEY]  # public-only keeps the secret
    assert host.gpg_trust == []

    host = SimulatedHost(name="h", gpg_keys=[VENDOR_KEY], gpg_secret_keys=[VENDOR_KEY])
    await send_command(
        host, GpgDeleteKeyCommand(fingerprint=VENDOR_KEY["fingerprint"], secret=True)
    )
    assert host.gpg_keys == []
    assert host.gpg_secret_keys == []


async def test_gpg_delete_key_end_to_end_second_delete_is_noop():
    """Two identical deletes: both plan changed, the apply log's fresh cache
    sees the key gone after the first."""

    def delete_deploy(host):
        yield op(gpg.delete_key, name="Delete once", keyid="ABCDEF12", sudo=True)
        yield op(gpg.delete_key, name="Delete twice", keyid="ABCDEF12", sudo=True)

    host = SimulatedHost(name="h", gpg_keys=[VENDOR_KEY])
    runtimes, plans = await run_prepare([host], delete_deploy)
    assert plans["h"][0].changed
    assert plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    first, second = report.log
    assert first.changed
    assert not second.changed
    assert second.reason == "GPG key ABCDEF12 already absent"
    assert host.gpg_keys == []
    assert (
        f"    $ sudo gpg --batch --yes --delete-keys {VENDOR_KEY['fingerprint']}"
        in format_log(report)
    )


async def test_gpg_delete_key_dry_run_sends_nothing():
    """dry=True plans the delete but the key stays."""

    def delete_deploy(host):
        yield op(gpg.delete_key, name="Delete key", keyid="ABCDEF12")

    host = SimulatedHost(name="h", gpg_keys=[VENDOR_KEY])
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        delete_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert host.gpg_keys == [VENDOR_KEY]


# --- gpg.gen_key operation (reemote original) ----------------------------------

GEN_UID = "Kim Jarvis <kim@example.com>"


def test_gpg_gen_key_command_shell_and_elevation():
    """The batch block is printf-fed into gpg --batch --gen-key inside one
    sh -c; every parameter line is quoted and %no-protection closes it."""
    cmd = GpgGenKeyCommand(
        real_name="Kim Jarvis", email="kim@example.com", algo="rsa", length=4096, expire="never"
    )
    shell = cmd.shell()
    assert shell.startswith("sh -c ")
    inner = shlex.split(shell)[2]
    assert inner == (
        "printf '%s\\n' 'Key-Type: rsa' 'Key-Length: 4096' 'Name-Real: Kim Jarvis'"
        " 'Name-Email: kim@example.com' 'Expire-Date: never' %no-protection"
        " | gpg --batch --gen-key"
    )
    assert command_shell(cmd, sudo=True).startswith("sudo sh -c ")
    assert command_shell(cmd, su=True) == f"su -c {shlex.quote(shell)}"


def test_gpg_gen_key_command_quotes_unsafe_parameters():
    """A name/email with shell metacharacters stays one quoted printf operand
    (the round-trip through sh -c preserves it verbatim)."""
    cmd = GpgGenKeyCommand(
        real_name="A B'c", email="e(x)@x.com", algo="rsa", length=2048, expire="1y"
    )
    inner = shlex.split(cmd.shell())[2]
    operands = shlex.split(inner.split(" | ")[0])
    assert "Name-Real: A B'c" in operands
    assert "Name-Email: e(x)@x.com" in operands
    assert "Key-Length: 2048" in operands
    assert "Expire-Date: 1y" in operands


async def test_gpg_gen_key_op_generates_when_uid_absent():
    """An absent uid generates: one descriptor carrying every batch parameter,
    with the uid-naming reason."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in gpg.gen_key(ctx, real_name="Kim Jarvis", email="kim@example.com")
    ]
    assert ctx.changed
    assert ctx.reason == f"generate GPG key for {GEN_UID}"
    assert commands == [
        GpgGenKeyCommand(
            real_name="Kim Jarvis", email="kim@example.com", algo="rsa", length=4096, expire="never"
        )
    ]


async def test_gpg_gen_key_op_existing_uid_is_noop():
    """A key whose uid is exactly name <email> satisfies the request."""
    host = SimulatedHost(name="h", gpg_keys=[VENDOR_KEY])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in gpg.gen_key(
            ctx, real_name="Vendor Key", email="vendor@example.com"
        )
    ]
    assert not ctx.changed
    assert ctx.reason == "GPG key for Vendor Key <vendor@example.com> already exists"
    assert commands == []


async def test_gpg_gen_key_op_uid_match_is_exact():
    """The same email under a different real name is a different uid: the
    generation runs."""
    host = SimulatedHost(name="h", gpg_keys=[VENDOR_KEY])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in gpg.gen_key(ctx, real_name="Other Name", email="vendor@example.com")
    ]
    assert ctx.changed
    assert len(commands) == 1


@pytest.mark.parametrize(
    ("real_name", "email", "message"),
    [("", "e@x.com", "non-empty real_name"), ("Kim", "", "non-empty email")],
)
async def test_gpg_gen_key_op_validates(real_name, email, message):
    """Empty real_name/email raise ValueError before any fact is read."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match=message):
        _ = [c async for c in gpg.gen_key(ctx, real_name=real_name, email=email)]


async def test_gpg_gen_key_op_absorbs_unknown_kwargs():
    """``**kwargs`` absorbs the baseline's global arguments; algo/length/expire ride
    into the descriptor."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in gpg.gen_key(
            ctx,
            real_name="Kim Jarvis",
            email="kim@example.com",
            algo="eddsa",
            length=2048,
            expire="2y",
            future_flag=True,
        )
    ]
    assert commands == [
        GpgGenKeyCommand(
            real_name="Kim Jarvis", email="kim@example.com", algo="eddsa", length=2048, expire="2y"
        )
    ]


async def test_gpg_gen_key_name_meta_parameter_does_not_collide():
    """op() reserves ``name`` as the request label: the operation's parameter
    is ``real_name``, so a labelled request keeps both distinct -- the label
    names the plan entry while real_name reaches the batch block."""

    def gen_deploy(host):
        yield op(
            gpg.gen_key,
            name="Generate the demo key",
            real_name="Kim Jarvis",
            email="kim@example.com",
        )

    host = SimulatedHost(name="h")
    _runtimes, plans = await run_prepare([host], gen_deploy)
    (planned,) = plans["h"]
    assert planned.request.name == "Generate the demo key"
    assert planned.changed
    (cmd,) = planned.commands
    assert cmd == GpgGenKeyCommand(
        real_name="Kim Jarvis", email="kim@example.com", algo="rsa", length=4096, expire="never"
    )


async def test_gpg_gen_key_simulated_seam_creates_both_halves_and_trust():
    """send_command appends the stub key to gpg_keys and gpg_secret_keys plus
    the ultimate ownertrust a fresh own key exports; a repeat adds nothing."""
    host = SimulatedHost(name="h")
    cmd = GpgGenKeyCommand(
        real_name="Kim Jarvis", email="kim@example.com", algo="rsa", length=4096, expire="never"
    )
    await send_command(host, cmd)
    fingerprint = hashlib.sha256(GEN_UID.encode()).hexdigest().upper()[:40]
    (key,) = host.gpg_keys
    assert key["uid"] == GEN_UID
    assert key["fingerprint"] == fingerprint
    assert key["length"] == 4096
    assert host.gpg_secret_keys == [key]
    assert host.gpg_secret_keys[0] is not host.gpg_keys[0]  # separate dicts
    assert host.gpg_trust == [
        {"fingerprint": fingerprint, "ownertrust": 6, "level": "ultimate"}
    ]
    await send_command(host, cmd)
    assert len(host.gpg_keys) == 1
    assert len(host.gpg_trust) == 1


async def test_gpg_gen_key_end_to_end_second_generation_is_noop():
    """Two identical generations: both plan changed, the apply log's fresh
    cache sees the uid the first generation created."""

    def gen_deploy(host):
        yield op(gpg.gen_key, name="Gen once", real_name="Kim Jarvis", email="kim@example.com")
        yield op(gpg.gen_key, name="Gen twice", real_name="Kim Jarvis", email="kim@example.com")

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], gen_deploy)
    assert plans["h"][0].changed
    assert plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    first, second = report.log
    assert first.changed
    assert not second.changed
    assert second.reason == f"GPG key for {GEN_UID} already exists"
    assert len(host.gpg_keys) == 1


async def test_gpg_gen_key_dry_run_sends_nothing():
    """dry=True plans the generation but the keychain stays empty."""

    def gen_deploy(host):
        yield op(gpg.gen_key, name="Gen key", real_name="Kim Jarvis", email="kim@example.com")

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        gen_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert host.gpg_keys == []


# --- gpg.trust operation (reemote original) ------------------------------------

TRUST_FPR = VENDOR_KEY["fingerprint"]


def test_gpg_trust_command_shell_and_elevation():
    """One FPR:N: line printf-fed into gpg --batch --import-ownertrust inside
    one sh -c so the pipeline runs at the request's privilege."""
    cmd = GpgTrustCommand(fingerprint=TRUST_FPR, ownertrust=6)
    shell = cmd.shell()
    assert shell.startswith("sh -c ")
    inner = shlex.split(shell)[2]
    assert inner == (
        f"printf '%s:%d:\\n' {TRUST_FPR} 6 | gpg --batch --import-ownertrust"
    )
    assert command_shell(cmd, sudo=True).startswith("sudo sh -c ")
    assert command_shell(cmd, su=True) == f"su -c {shlex.quote(shell)}"


@pytest.mark.parametrize(
    ("level", "value"),
    [("unknown", 1), ("never", 2), ("marginal", 3), ("full", 4), ("ultimate", 6)],
)
async def test_gpg_trust_op_sets_each_level(level, value):
    """Every accepted level writes its numeric value (ultimate writes 6, what
    a fresh own key exports)."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gpg.trust(ctx, fingerprint=TRUST_FPR, level=level)]
    assert ctx.changed
    assert ctx.reason == f"set GPG trust for {TRUST_FPR} to {level}"
    assert commands == [GpgTrustCommand(fingerprint=TRUST_FPR, ownertrust=value)]


@pytest.mark.parametrize("ownertrust", [5, 6])
async def test_gpg_trust_op_ultimate_satisfied_by_5_or_6(ownertrust):
    """The idempotence check compares level names, so both raw ultimate values
    satisfy an ultimate request without rewriting the trustdb."""
    host = SimulatedHost(
        name="h",
        gpg_trust=[
            {"fingerprint": TRUST_FPR, "ownertrust": ownertrust, "level": "ultimate"}
        ],
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gpg.trust(ctx, fingerprint=TRUST_FPR, level="ultimate")]
    assert not ctx.changed
    assert ctx.reason == f"GPG trust for {TRUST_FPR} already ultimate"
    assert commands == []


async def test_gpg_trust_op_different_level_rewrites():
    """An assignment at another level is a change."""
    host = SimulatedHost(
        name="h",
        gpg_trust=[{"fingerprint": TRUST_FPR, "ownertrust": 4, "level": "full"}],
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gpg.trust(ctx, fingerprint=TRUST_FPR, level="never")]
    assert ctx.changed
    assert commands == [GpgTrustCommand(fingerprint=TRUST_FPR, ownertrust=2)]


async def test_gpg_trust_op_rejects_invalid_level():
    """An unknown level raises ValueError listing the accepted names."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="Invalid trust level 'always'"):
        _ = [c async for c in gpg.trust(ctx, fingerprint=TRUST_FPR, level="always")]


async def test_gpg_trust_op_rejects_empty_fingerprint():
    """An empty fingerprint raises ValueError before any fact is read."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="non-empty fingerprint"):
        _ = [c async for c in gpg.trust(ctx, fingerprint="", level="full")]


async def test_gpg_trust_op_absorbs_unknown_kwargs():
    """``**kwargs`` absorbs the baseline's global arguments."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in gpg.trust(ctx, fingerprint=TRUST_FPR, level="full", future_flag=1)
    ]
    assert len(commands) == 1


async def test_gpg_trust_simulated_seam_upserts_the_assignment():
    """send_command upserts the trust entry: a new fingerprint appends, an
    existing one is rewritten in place with the new value and level name."""
    host = SimulatedHost(name="h")
    await send_command(host, GpgTrustCommand(fingerprint=TRUST_FPR, ownertrust=4))
    assert host.gpg_trust == [
        {"fingerprint": TRUST_FPR, "ownertrust": 4, "level": "full"}
    ]
    await send_command(host, GpgTrustCommand(fingerprint=TRUST_FPR, ownertrust=2))
    assert host.gpg_trust == [
        {"fingerprint": TRUST_FPR, "ownertrust": 2, "level": "never"}
    ]


async def test_gpg_trust_end_to_end_second_set_is_noop():
    """Two identical trust sets: both plan changed, the apply log's fresh
    cache reads the assignment back and reports the second unchanged."""

    def trust_deploy(host):
        yield op(gpg.trust, name="Trust once", fingerprint=TRUST_FPR, level="full")
        yield op(gpg.trust, name="Trust twice", fingerprint=TRUST_FPR, level="full")

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], trust_deploy)
    assert plans["h"][0].changed
    assert plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    first, second = report.log
    assert first.changed
    assert not second.changed
    assert second.reason == f"GPG trust for {TRUST_FPR} already full"
    assert host.gpg_trust == [
        {"fingerprint": TRUST_FPR, "ownertrust": 4, "level": "full"}
    ]


async def test_gpg_trust_dry_run_sends_nothing():
    """dry=True plans the assignment but the trustdb stays empty."""

    def trust_deploy(host):
        yield op(gpg.trust, name="Trust key", fingerprint=TRUST_FPR, level="ultimate")

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        trust_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert host.gpg_trust == []


# --- gpg.refresh_keys operation (reemote original) -----------------------------


def test_gpg_refresh_keys_command_shell_and_elevation():
    """No ids refreshes every key; named ids append in request order, each
    quoted. A single gpg invocation elevates as a whole."""
    cmd = GpgRefreshKeysCommand()
    assert cmd.shell() == "gpg --batch --refresh-keys"
    assert command_shell(cmd, sudo=True) == "sudo gpg --batch --refresh-keys"
    assert command_shell(cmd, su=True) == f"su -c {shlex.quote(cmd.shell())}"
    named = GpgRefreshKeysCommand(keyids=("ABCDEF12", "0x1234ABCD", "a b"))
    assert named.shell() == f"gpg --batch --refresh-keys ABCDEF12 0x1234ABCD {shlex.quote('a b')}"


async def test_gpg_refresh_keys_op_without_ids_refreshes_all():
    """None refreshes the whole keychain: always changed, empty tuple."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gpg.refresh_keys(ctx)]
    assert ctx.changed
    assert ctx.reason == "refresh all GPG keys"
    assert commands == [GpgRefreshKeysCommand(keyids=())]


@pytest.mark.parametrize(
    ("keyids", "expected"),
    [
        ("ABCDEF12", ("ABCDEF12",)),
        (["ABCDEF12", "0x1234ABCD"], ("ABCDEF12", "0x1234ABCD")),
        (("ABCDEF12", "0x1234ABCD"), ("ABCDEF12", "0x1234ABCD")),
    ],
)
async def test_gpg_refresh_keys_op_normalizes_keyids(keyids, expected):
    """A single str, a list or a tuple all ride into the descriptor as a
    tuple; the reason names the requested ids."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gpg.refresh_keys(ctx, keyids=keyids)]
    assert ctx.changed
    assert ctx.reason == f"refresh GPG keys {list(expected)}"
    assert commands == [GpgRefreshKeysCommand(keyids=expected)]


async def test_gpg_refresh_keys_op_is_stateless_and_absorbs_kwargs():
    """The refresh always runs (keyserver updates are unknowable without
    contacting them); ``**kwargs`` absorbs the baseline's global arguments."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    first = [c async for c in gpg.refresh_keys(ctx, future_flag=True)]
    second = [c async for c in gpg.refresh_keys(ctx)]
    assert first == second == [GpgRefreshKeysCommand(keyids=())]
    assert ctx.changed


async def test_gpg_refresh_keys_simulated_seam_is_a_tolerated_noop():
    """send_command tolerates the refresh: the simulation models no key
    material updates, so every seam stays untouched."""
    host = SimulatedHost(name="h", gpg_keys=[VENDOR_KEY])
    await send_command(host, GpgRefreshKeysCommand(keyids=("ABCDEF12",)))
    assert host.gpg_keys == [VENDOR_KEY]
    assert host.remote_files == {}


async def test_gpg_refresh_keys_end_to_end_over_run():
    """Through run(): the stateless refresh always applies -- a second one in
    the deploy stays changed -- and the log shows the elevated gpg."""

    def refresh_deploy(host):
        yield op(gpg.refresh_keys, name="Refresh once", keyids=("ABCDEF12",), sudo=True)
        yield op(gpg.refresh_keys, name="Refresh twice", keyids=("ABCDEF12",), sudo=True)

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], refresh_deploy)
    report = await run_apply(runtimes, plans=plans)
    first, second = report.log
    assert first.changed
    assert second.changed
    assert "    $ sudo gpg --batch --refresh-keys ABCDEF12" in format_log(report)


async def test_gpg_refresh_keys_dry_run_sends_nothing():
    """dry=True plans the refresh but sends no command."""

    def refresh_deploy(host):
        yield op(gpg.refresh_keys, name="Refresh keys")

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        refresh_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert planned.commands == (GpgRefreshKeysCommand(keyids=()),)