# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the files module's hash fact family: ``Sha256File``,
``Sha384File`` and ``Md5File``.

Covers ``reemote.modules.files.files.Sha256File`` / ``Sha384File`` /
``Md5File``: the baseline's own twelve hash fixtures over the real-transport
path (four per fact -- the GNU stdin ``<hash>  -`` form, the bare BSD
digest form, a backslash-bearing path and a metacharacter path), the
exact digit-count parse guard, and the ``remote_files``-derived
``SimulatedHost`` seam (hashlib over the stored bytes, None when absent,
``gather_fact`` argument forwarding). ``RemoteFileSha1`` is regression-
tested to keep its simpler ``sha1sum <path>`` probe: it backs the
``files.put``/``files.get`` idempotence contract, untouched by the family.
"""

import hashlib

import pytest

from reemote.drivers import gather_fact
from reemote.hosts import Host
from reemote.modules.files.files import Md5File, RemoteFileSha1, Sha256File, Sha384File
from tests.simulated_host import SimulatedHost


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.shells: list[str] = []

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        self.shells.append(shell)
        return self._stdout


# The baseline's fixture digests, verbatim.
SHA256 = "3867882e8ccc16bd6a1e3e214a46608f9cf5d21687bbb8c25751da3c47b48033"
SHA384 = (
    "1dd2e448df7c4dc19848a37fdf25c86660b19d06a4cd6987ca2c4071f80e9dd3"
    "061e4b01ddb982dc56ed7a2ed2294699"
)
MD5 = "c10ba97d7c9078a006d26b5db01d8ee7"

# The verbatim tool chains, {q} standing for the quoted path spelling.
SHA256_CMD = (
    "test -e {q} && ( sha256sum < {q} 2> /dev/null || "
    "shasum -a 256 < {q} 2> /dev/null || sha256 < {q} 2> /dev/null ) || true"
)
SHA384_CMD = (
    "test -e {q} && ( sha384sum < {q} 2> /dev/null || "
    "shasum -a 384 < {q} 2> /dev/null || sha384 < {q} 2> /dev/null ) || true"
)
MD5_CMD = (
    "test -e {q} && ( md5sum < {q} 2> /dev/null || md5 < {q} 2> /dev/null ) || true"
)

# The four fixture paths and their shell-quoted spellings.
PLAIN = ("myfile", "myfile")
BACKSLASH = (r"/tmp/mnt-data\x2dbig.mount", r"'/tmp/mnt-data\x2dbig.mount'")
SPECIAL = ("my () file && special_chars.txt", "'my () file && special_chars.txt'")


# --- Real-transport parity: the baseline's own hash-family fixtures ---------------------


@pytest.mark.parametrize(
    ("fact_cls", "template", "digest", "paths", "suffix"),
    [
        (Sha256File, SHA256_CMD, SHA256, PLAIN, "  -"),
        (Sha256File, SHA256_CMD, SHA256, PLAIN, ""),
        (Sha256File, SHA256_CMD, SHA256, BACKSLASH, "  -"),
        (Sha256File, SHA256_CMD, SHA256, SPECIAL, "  -"),
        (Sha384File, SHA384_CMD, SHA384, PLAIN, "  -"),
        (Sha384File, SHA384_CMD, SHA384, PLAIN, ""),
        (Sha384File, SHA384_CMD, SHA384, BACKSLASH, "  -"),
        (Sha384File, SHA384_CMD, SHA384, SPECIAL, "  -"),
        (Md5File, MD5_CMD, MD5, PLAIN, "  -"),
        (Md5File, MD5_CMD, MD5, PLAIN, ""),
        (Md5File, MD5_CMD, MD5, BACKSLASH, "  -"),
        (Md5File, MD5_CMD, MD5, SPECIAL, "  -"),
    ],
    ids=[
        "sha256-file",
        "sha256-bsd",
        "sha256-backslash",
        "sha256-needs-quotes",
        "sha384-file",
        "sha384-bsd",
        "sha384-backslash",
        "sha384-needs-quotes",
        "md5-file",
        "md5-bsd",
        "md5-backslash",
        "md5-needs-quotes",
    ],
)
async def test_hash_family_parses_baseline_fixtures(
    fact_cls, template, digest, paths, suffix
):
    # The baseline's file.json / file_bsd_style.json / backslash.json /
    # file_needs_quotes.json fixtures, verbatim: the stdin form keeps the
    # filename out of the tool's output, so even a backslash-bearing name
    # parses as a bare digest line.
    path, quoted = paths
    host = CannedShellHost(digest + suffix + "\n")
    assert await fact_cls.process(host, path) == digest
    assert host.shells == [template.format(q=quoted)]


# --- Parser edge cases ---------------------------------------------------------------


def test_hash_parse_requires_the_exact_digit_count():
    # A shorter or longer digest is not this fact's answer -- the count
    # is what separates the family members sharing one parse.
    assert Sha256File.parse([MD5 + "  -"]) is None
    assert Sha384File.parse([SHA256]) is None
    assert Md5File.parse([SHA256]) is None
    assert Sha256File.parse([SHA256 + "0"]) is None


def test_hash_parse_rejects_non_hex_and_garbage():
    assert Sha256File.parse(["g" * 64]) is None
    assert Md5File.parse(["not-gonna-match"]) is None
    assert Sha256File.parse([]) is None


def test_hash_parse_accepts_uppercase_hex():
    assert Sha256File.parse([SHA256.upper() + "  -"]) == SHA256.upper()


async def test_hash_is_none_on_empty_output():
    # A missing file (or a host with none of the tool spellings) exits
    # zero through the || true guard with no stdout -> None.
    assert await Sha256File.process(CannedShellHost(""), "/no/such/file") is None
    assert await Md5File.process(CannedShellHost(""), "/no/such/file") is None


# --- Hash family SimulatedHost seam ----------------------------------------------------


async def test_hash_family_derives_from_the_remote_files_store():
    # The shared content store answers at zero wiring cost: the digest is
    # hashlib over the stored bytes, exactly as the real tool reads them.
    host = SimulatedHost(name="h", remote_files={"/f": b"hello"})
    assert await Sha256File.process(host, "/f") == hashlib.sha256(b"hello").hexdigest()
    assert await Sha384File.process(host, "/f") == hashlib.sha384(b"hello").hexdigest()
    assert await Md5File.process(host, "/f") == hashlib.md5(b"hello").hexdigest()
    # An absent path reads None, like the guarded real transport.
    assert await Sha256File.process(host, "/no/such") is None


async def test_remote_file_sha1_keeps_its_own_probe_and_seam():
    # Regression: RemoteFileSha1 backs files.put/files.get idempotence and
    # deliberately keeps the argv form (no stdin chain, no || true guard).
    digest = hashlib.sha1(b"hello").hexdigest()
    host = CannedShellHost(f"{digest}  /f\n")
    assert await RemoteFileSha1.process(host, "/f") == digest
    assert host.shells == ["sha1sum /f"]
    # The same content store answers both facts, each with its algorithm.
    sim = SimulatedHost(name="h", remote_files={"/f": b"hello"})
    assert await RemoteFileSha1.process(sim, "/f") == digest
    assert await Sha256File.process(sim, "/f") == hashlib.sha256(b"hello").hexdigest()


async def test_hash_gather_fact_forwards_the_path():
    hosts = [
        SimulatedHost(name="web1", remote_files={"/f": b"hello"}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, Sha256File, "/f") == {
        "web1": hashlib.sha256(b"hello").hexdigest(),
        "web2": None,
    }