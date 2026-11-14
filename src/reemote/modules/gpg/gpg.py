# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The ``gpg`` module: facts and operations in one source.
#
# Fact side: ``GpgKey``, the keys held in one key file on the target host,
# parsed from ``gpg --with-colons`` into plain ``GpgKeyInfo`` TypedDict-shaped
# dicts, ``GpgKeyrings``, every keyring file found under given directories
# with its format and the keys it holds (``GpgKeyring`` TypedDict-shaped
# dicts), ``GpgKeys``, the public keys in the executing user's keychain (or
# one explicit keyring file), and ``GpgSecretKeys``, the secret keys in that
# keychain -- all sharing the ``GpgKeyInfo`` entry shape and the
# ``_parse_key_records`` colon-record parser. Beside those ports live three
# reemote originals: ``GpgTrust``, the
# ownertrust assignments read from ``gpg --export-ownertrust`` into
# ``GpgKeyTrust`` entries, ``GpgKeyDetails``, one key's full metadata read
# from a targeted ``gpg --list-keys`` into a ``GpgKeyMetadata`` dict (None
# when the key is absent), and ``GpgVerify``, a read-only signature check
# read from ``gpg --status-fd 1 --verify`` into a ``GpgVerification`` dict.
# Like every fact they are phase-unaware: their ``process`` coroutines just
# gather the raw value.
#
# Operation side: ``gpg.key`` plus the typed command descriptors it yields
# (``GpgKeyUploadCommand``/``GpgKeyInstallCommand`` for the install half,
# ``GpgKeyRemoveCommand``/``GpgKeyDeleteKeysCommand``/``GpgKeyEmptyCleanupCommand``
# for the removal half) and ``gpg.dearmor`` (``GpgDearmorCommand``).
# ``gpg.key`` installs and removes keyring files from stream-only sources: a GPG
# key stream is spooled over scp and installed as a keyring file (dearmored
# on the target when the content is ASCII-armored), idempotent on the
# destination's existence (or, with ``keyid``, on the in-namespace
# ``GpgKeyrings`` fact); ``present=False`` removes a whole keyring file or
# selectively deletes keys from keyrings, pruning emptied binary keyrings.
# Its ``keyserver`` fetch, URL download and ``mode`` parameter are
# deliberately dropped (0644 is fixed). ``gpg.dearmor`` converts an
# armored key file already on the target host
# to binary form, deliberately stateless (always yields). Operations are
# async generators that receive an ``OpContext``, await facts, set
# ``ctx.changed``/``ctx.reason``, and yield command descriptors; they never
# touch the host directly. Beside those ports live six reemote originals
# covering the key lifecycle in the executing user's default keychain:
# ``gpg.import_key`` (spool a key stream over scp, ``gpg --import`` it),
# ``gpg.export_key`` (``gpg --export``/``--export-secret-keys`` to a file),
# ``gpg.delete_key`` (``gpg --delete-keys``/``--delete-secret-and-public-keys``),
# ``gpg.gen_key`` (batch ``gpg --gen-key`` without a passphrase),
# ``gpg.trust`` (set one key's ownertrust via ``--import-ownertrust``) and
# ``gpg.refresh_keys`` (``gpg --refresh-keys`` from the keyservers).
# """

import hashlib
import shlex
from collections.abc import AsyncGenerator, Iterable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import IO, Any, NotRequired, TypedDict
from urllib.parse import urlparse

from ...commands import Command, TransferCommand
from ...core import OpContext
from ...hosts import Host

# --- Facts -------------------------------------------------------------------


class GpgKeyInfo(TypedDict):
    """
    Attributes:
        id: The key id (the last 16 hex digits of the fingerprint), the
            identity a colon-record listing names the key under.
        validity: GPG's validity letter for the key (``-`` unknown,
            ``f`` fully trusted, ...), exactly as the colon record
            prints it.
        length: The key length in bits (e.g. 4096).
        fingerprint: The full fingerprint, when a ``fpr`` record
            attached one.
        uid_hash: The hash of the user id, when a ``uid`` record
            attached one.
        uid: The user id string (name, comment and email), when a
            ``uid`` record attached one.
        subkeys: The key's subkeys as further ``GpgKeyInfo`` entries
            (only the last ``sub``/``ssb`` record before the next
            primary key is kept); present on primary keys, absent on
            subkey entries.
    """

    # The shared key entry of ``GpgKey``, ``GpgKeys``, ``GpgSecretKeys`` and
    # ``GpgKeyrings`` -- one shape for every gpg key-listing question, the
    # same fields as apt's ``AptKey`` (whose fact is the APT-flavored
    # projection of the same colon records).
    #
    # A TypedDict rather than a class instance: entries are ordinary dicts,
    # so equality against a literal dict works and ``json.dumps`` serializes
    # them directly. The optional fields are NotRequired because a
    # colon-record stream may carry no ``fpr``/``uid`` line for a key, and
    # subkey entries never carry ``subkeys``.

    id: str
    validity: str
    length: int
    fingerprint: NotRequired[str]
    uid_hash: NotRequired[str]
    uid: NotRequired[str]
    subkeys: NotRequired[list["GpgKeyInfo"]]


def _parse_key_records(
    output: Iterable[str],
    key_record_type: str = "pub",
    subkey_record_type: str = "sub",
) -> list[GpgKeyInfo]:
    """Parse ``gpg --with-colons`` records into key entries.

    The colon-record parser behind the key listings (see
    GnuPG's doc/DETAILS for the field values): a primary-key record
    (``pub``, or ``sec`` for a secret listing) starts a new key
    (``validity``, ``length`` as an int, ``id``), a subkey record (``sub`` /
    ``ssb``) starts its subkey, and ``fpr``/``uid`` records attach the
    ``fingerprint``, ``uid_hash`` and ``uid`` to whichever of the two is
    current. Only the last subkey record before the next primary key is
    kept. Blank lines and other record types
    (``tru``, ...) are skipped, and records before the first primary key
    are ignored. Each key keeps its ``id`` and its subkeys stay a list of
    their own ``GpgKeyInfo`` entries.
    """
    keys: list[GpgKeyInfo] = []
    current_key: GpgKeyInfo | None = None
    current_subkey: GpgKeyInfo | None = None

    for line in output:
        if not line:
            continue

        bits = line.split(":")

        if bits[0] in (key_record_type, subkey_record_type):
            key_details: GpgKeyInfo = {
                "validity": bits[1],
                "length": int(bits[2]),
                "id": bits[4],
            }

            if bits[0] == key_record_type:
                key_details["subkeys"] = []

                if current_key:
                    if current_subkey:
                        current_key["subkeys"].append(current_subkey)
                    keys.append(current_key)

                current_key = key_details
                current_subkey = None
            elif current_key:
                current_subkey = key_details

        elif current_subkey or current_key:
            target = current_subkey or current_key
            assert target is not None
            if bits[0] == "fpr":
                target["fingerprint"] = bits[9]  # fingerprint = field 10
            elif bits[0] == "uid":
                target["uid_hash"] = bits[7]
                target["uid"] = bits[9]

    if current_key:
        if current_subkey:
            current_key["subkeys"].append(current_subkey)
        keys.append(current_key)

    return keys


def _copy_key_entries(entries: Iterable[GpgKeyInfo]) -> list[GpgKeyInfo]:
    """Copy key entries (and their subkey dicts) for a seam read.

    Callers must never mutate simulated state, so every seam returning
    ``GpgKeyInfo`` entries duplicates each entry dict and its ``subkeys``
    list of dicts.
    """
    copies: list[GpgKeyInfo] = []
    for key in entries:
        copy: GpgKeyInfo = {**key}
        if "subkeys" in key:
            copy["subkeys"] = [{**sub} for sub in key["subkeys"]]
        copies.append(copy)
    return copies


class GpgKey:
    """The GPG keys held in one key file on the target host.

    **Commands:**

    ```bash
    gpg --with-colons <src>
    ```

    **Returns:**

    ```python
    list[GpgKeyInfo]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/gpg/gpg_gpg_key.py``.
    """

    # ``gpg --with-colons <src>`` reads a
    # key file (ASCII-armored or binary) without importing it, and the
    # colon records parse into ``GpgKeyInfo`` entries, for example::
    #
    #     [
    #         {
    #             "id": "8D81803C0EBFCD88",
    #             "validity": "-",
    #             "length": 4096,
    #             "subkeys": [
    #                 {"id": "7EA0A9C3F273FCD8", "validity": "-", "length": 4096},
    #             ],
    #             "uid_hash": "",
    #             "uid": "Docker Release (CE deb) <docker@docker.com>",
    #         },
    #     ]
    #
    # ``src`` is a path on the TARGET host, shlex-quoted. The answer is
    # the list of typed entries, each carrying its ``id`` and its subkeys
    # as a list (the ``apt.AptKeys`` retyping precedent).
    # A URL branch -- ``(wget -O - <url> || curl -sSLf <url>) | gpg
    # --with-colons`` fetching the key target-side -- is deliberately
    # dropped: a *fact gather* never fetches a URL (a fact is a phase-unaware
    # read, so a mid-gather download would break determinism), so a ``src``
    # with a URL scheme raises ``ValueError`` (stream the key through ``gpg.key``
    # or ``gpg.import_key`` instead, then read the installed file). An
    # empty ``src`` raises ``ValueError`` too. There is no command-existence
    # gate on ``gpg``: a
    # missing ``gpg`` exits 127, which ``run_shell`` reports as
    # ``CommandNotFoundError``; a missing or unreadable file exits 2 with
    # empty stdout, which reads as ``[]``.

    @classmethod
    def command(cls, src: str) -> str:
        """Shell reading the key file at ``src`` with ``gpg --with-colons``.

        The path is shlex-quoted; an empty path or one carrying a URL
        scheme raises ``ValueError`` (the URL fetch branch is dropped).
        """
        if not src:
            raise ValueError("src must be a non-empty path on the target host")
        if urlparse(src).scheme:
            raise ValueError(
                "src must be a path on the target host, not a URL; "
                "a fact gather does not fetch a URL mid-gather"
            )
        return f"gpg --with-colons {shlex.quote(src)}"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[GpgKeyInfo]:
        """Parse ``gpg --with-colons`` records into key entries. Pure and testable."""
        return _parse_key_records(output)

    @classmethod
    async def process(cls, host: Host, src: str) -> list[GpgKeyInfo]:
        # Build the shell first, even on the simulated path, so an empty
        # or URL src is rejected identically on both transports.
        shell = cls.command(src)
        gpg_key = getattr(host, "gpg_key", None)
        if gpg_key is not None:
            # SimulatedHost seam: the state is keyed by the src path. An
            # unseeded path reads as [] (the simulated stand-in for a file
            # gpg cannot read). Copy each entry so callers never mutate
            # the simulated state.
            return _copy_key_entries(gpg_key.get(src, []))
        # Real transport: read the key file over asyncssh.
        stdout = await host.run_shell(shell)
        return cls.parse(stdout.splitlines())


class GpgKeyring(TypedDict):
    """
    Attributes:
        path: The keyring file's path, exactly as the ``KEYRING:``
            marker line names it.
        format: The file's extension lowercased (``gpg``, ``kbx`` or
            ``asc``), the format label.
        keys: The keys the file holds, as ``GpgKeyInfo`` entries in
            listing order; a keyring ``gpg`` could not read contributes
            no entry at all rather than an empty list.
    """

    # One keyring file (``GpgKeyrings``' entry), as a plain dict.
    #
    # The answer is the list of typed entries, each carrying
    # its ``path`` and its keys as a list (the same retyping as the key
    # entries themselves).

    path: str
    format: str
    keys: list[GpgKeyInfo]


class GpgKeyrings:
    r"""The GPG keyring files found under given directories, with their keys.

    **Commands:**

    ```bash
    find "<directories>" -type f \( -name '*.gpg' -o -name '*.kbx' \) 2>/dev/null -exec sh -c 'echo "KEYRING:$1"; gpg --list-keys --with-colons --keyring "$1" --no-default-keyring 2>/dev/null || true' _ {} \; ; find "<directories>" -type f -name '*.asc' 2>/dev/null -exec sh -c 'echo "KEYRING:$1"; gpg --with-colons "$1" 2>/dev/null || true' _ {} \;
    ```

    **Returns:**

    ```python
    list[GpgKeyring]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/gpg/gpg_gpg_keyrings.py``.
    """

    # Two ``find`` sweeps list every
    # ``.gpg``/``.kbx`` binary keyring and every ``.asc`` armored keyring
    # under ``directories``, each is read with ``gpg --with-colons`` behind
    # a ``KEYRING:`` marker line, and each readable file becomes one
    # ``GpgKeyring`` entry, for example::
    #
    #     [
    #         {
    #             "path": "/etc/apt/keyrings/docker.gpg",
    #             "format": "gpg",
    #             "keys": [
    #                 {
    #                     "id": "8D81803C0EBFCD88",
    #                     "validity": "-",
    #                     "length": 4096,
    #                     "subkeys": [],
    #                     "uid": "Docker Release (CE deb) <docker@docker.com>",
    #                 },
    #             ],
    #         },
    #     ]
    #
    # ``directories`` is a sequence of paths (list or tuple -- a tuple keeps
    # the argument hashable for ``FactCache.get``, which ``gpg.key`` relies
    # on) or a single path string; an empty sequence or string raises
    # ``ValueError`` (an empty argument would silently search
    # the working directory). Entries keep the order the markers appeared
    # in -- binary keyrings first, then armored ones -- and a file ``gpg``
    # cannot read contributes its marker only, which parses to no entry
    # (the ``current_output`` guard). The answer is the
    # list of typed entries. This is the general-purpose keyring
    # inspection fact; ``apt.AptKeys`` is the APT-flavored projection that
    # flattens the same sweep into one key list and drops the per-keyring
    # paths and formats. There is no command-existence gate on ``gpg``:
    # a missing ``gpg`` is swallowed by each
    # exec's ``|| true`` and reads as ``[]``, while a missing ``find``
    # exits 127, which ``run_shell`` reports as ``CommandNotFoundError``.

    #: Marker prefixing each keyring's ``gpg`` output, naming the keyring
    #: file (the ``KEYRING:`` echo).
    KEYRING_MARKER = "KEYRING:"

    @classmethod
    def command(cls, directories: str | Sequence[str]) -> str:
        """Shell listing every keyring under ``directories`` with ``gpg``.

        Two separate ``find``+``-exec`` calls avoid bash-specific syntax:
        binary keyrings (``.gpg``, ``.kbx``) are read with ``--keyring`` /
        ``--no-default-keyring``, armored keyrings (``.asc``) are passed
        directly to ``gpg``. Each file's output is prefixed with a
        ``KEYRING:`` marker line; a file ``gpg`` cannot read contributes
        its marker only (the ``|| true`` swallows the failure). A single
        string is treated as one directory; an empty argument raises
        ``ValueError``.
        """
        if isinstance(directories, str):
            directories = [directories]
        if not directories or not all(directories):
            raise ValueError("directories must name at least one non-empty path")

        search_locations = " ".join(f'"{d}"' for d in directories)
        marker = cls.KEYRING_MARKER

        return (
            f"find {search_locations} -type f \\( -name '*.gpg' -o -name '*.kbx' \\) 2>/dev/null"
            f' -exec sh -c \'echo "{marker}$1";'
            f' gpg --list-keys --with-colons --keyring "$1" --no-default-keyring 2>/dev/null || true\' _ {{}} \\; ;'
            f" find {search_locations} -type f -name '*.asc' 2>/dev/null"
            f' -exec sh -c \'echo "{marker}$1"; gpg --with-colons "$1" 2>/dev/null || true\' _ {{}} \\;'
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[GpgKeyring]:
        """Parse marker-grouped ``gpg --with-colons`` output into keyrings.

        Each ``KEYRING:`` line starts a new keyring group whose colon
        records are parsed by ``_parse_key_records``; the group's format
        is the path's extension lowercased. Lines before the first marker
        are ignored, and a keyring with a marker but no records (an
        unreadable file) contributes no entry. Pure and testable.
        """
        keyrings: list[GpgKeyring] = []
        current_keyring: str | None = None
        current_output: list[str] = []

        for raw in output:
            line = raw.strip()
            if not line:
                continue

            if line.startswith(cls.KEYRING_MARKER):
                if current_keyring and current_output:
                    keyrings.append(
                        {
                            "path": current_keyring,
                            "format": current_keyring.split(".")[-1].lower(),
                            "keys": _parse_key_records(current_output),
                        }
                    )
                current_keyring = line[len(cls.KEYRING_MARKER) :]
                current_output = []
            else:
                current_output.append(line)

        if current_keyring and current_output:
            keyrings.append(
                {
                    "path": current_keyring,
                    "format": current_keyring.split(".")[-1].lower(),
                    "keys": _parse_key_records(current_output),
                }
            )

        return keyrings

    @classmethod
    async def process(cls, host: Host, directories: str | Sequence[str]) -> list[GpgKeyring]:
        # Build the shell first, even on the simulated path, so empty
        # directories are rejected identically on both transports.
        shell = cls.command(directories)
        gpg_keyrings = getattr(host, "gpg_keyrings", None)
        if gpg_keyrings is not None:
            # SimulatedHost seam: the state holds the already-gathered
            # answer, so ``directories`` does not filter it. Copy each
            # entry (and its key entries) so callers never mutate the
            # simulated state.
            return [
                {
                    "path": keyring["path"],
                    "format": keyring["format"],
                    "keys": _copy_key_entries(keyring["keys"]),
                }
                for keyring in gpg_keyrings
            ]
        # Real transport: sweep the keyring directories over asyncssh.
        stdout = await host.run_shell(shell)
        return cls.parse(stdout.splitlines())


class GpgKeys:
    """The public GPG keys in the executing user's keychain, or one keyring file.

    **Commands:**

    ```bash
    gpg --list-keys --with-colons
    ```

    **Returns:**

    ```python
    list[GpgKeyInfo]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/gpg/gpg_gpg_keys.py``.
    """

    # ``gpg --list-keys --with-colons``
    # lists the default keychain's public keys, and the colon records parse
    # into ``GpgKeyInfo`` entries, for example::
    #
    #     [
    #         {
    #             "id": "3B4FE6ACC0B21F32",
    #             "validity": "-",
    #             "length": 4096,
    #             "subkeys": [],
    #             "fingerprint": "790BC7277767219C42C86F933B4FE6ACC0B21F32",
    #             "uid_hash": "B7A02867A0C1D32B594B36C00E20C8C57E397748",
    #             "uid": "Ubuntu Archive Automatic Signing Key (2012) <ftpmaster@ubuntu.com>",
    #         },
    #     ]
    #
    # A ``keyring`` path switches the listing to that one file
    # (``--keyring <path> --no-default-keyring``, shlex-quoted); None (the
    # default) lists the executing user's default keychain -- elevate the
    # gather's host connection or point ``keyring`` at a file to inspect
    # another user's keys. The answer is the list of typed
    # entries (the ``apt.AptKeys`` retyping precedent). There is no
    # command-existence gate on ``gpg``: a
    # missing ``gpg`` exits 127, which ``run_shell`` reports as
    # ``CommandNotFoundError``; an empty keychain prints nothing and reads
    # as ``[]``.

    @classmethod
    def command(cls, keyring: str | None = None) -> str:
        """Shell listing the default keychain's keys, or one keyring file's."""
        if not keyring:
            return "gpg --list-keys --with-colons"
        return (
            f"gpg --list-keys --with-colons --keyring {shlex.quote(keyring)}"
            " --no-default-keyring"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[GpgKeyInfo]:
        """Parse ``gpg --list-keys`` colon records into key entries. Pure and testable."""
        return _parse_key_records(output)

    @classmethod
    async def process(cls, host: Host, keyring: str | None = None) -> list[GpgKeyInfo]:
        shell = cls.command(keyring)
        gpg_keys = getattr(host, "gpg_keys", None)
        if gpg_keys is not None:
            # SimulatedHost seam: the state holds the default keychain's
            # answer, so ``keyring`` does not filter it. Copy each entry
            # so callers never mutate the simulated state.
            return _copy_key_entries(gpg_keys)
        # Real transport: list the keys over asyncssh.
        stdout = await host.run_shell(shell)
        return cls.parse(stdout.splitlines())


class GpgSecretKeys:
    """The secret GPG keys in the executing user's keychain, or one keyring file.

    **Commands:**

    ```bash
    gpg --list-secret-keys --with-colons
    ```

    **Returns:**

    ```python
    list[GpgKeyInfo]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/gpg/gpg_gpg_secret_keys.py``.
    """

    # The secret-key twin of
    # ``GpgKeys`` -- ``gpg --list-secret-keys --with-colons`` prints ``sec``
    # primary and ``ssb`` subkey records instead of ``pub``/``sub``, parsed
    # by the same ``_parse_key_records`` with the record types flipped, into
    # the same ``GpgKeyInfo`` entries, for example::
    #
    #     [
    #         {
    #             "id": "8D81803C0EBFCD88",
    #             "validity": "-",
    #             "length": 4096,
    #             "subkeys": [
    #                 {"id": "7EA0A9C3F273FCD8", "validity": "-", "length": 4096},
    #             ],
    #             "uid_hash": "",
    #             "uid": "Docker Release (CE deb) <docker@docker.com>",
    #         },
    #     ]
    #
    # Only key metadata is listed -- no secret key material ever leaves the
    # host. A ``keyring`` path switches the listing to that one file
    # (``--keyring <path> --no-default-keyring``, shlex-quoted). The answer
    # is the list of typed entries. There is no command-existence gate on
    # ``gpg``: a missing ``gpg`` exits 127, which ``run_shell``
    # reports as ``CommandNotFoundError``; a keychain with no secret keys
    # prints nothing and reads as ``[]``.

    @classmethod
    def command(cls, keyring: str | None = None) -> str:
        """Shell listing the default keychain's secret keys, or one file's."""
        if not keyring:
            return "gpg --list-secret-keys --with-colons"
        return (
            f"gpg --list-secret-keys --with-colons --keyring {shlex.quote(keyring)}"
            " --no-default-keyring"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[GpgKeyInfo]:
        """Parse ``sec``/``ssb`` colon records into key entries. Pure and testable."""
        return _parse_key_records(output, key_record_type="sec", subkey_record_type="ssb")

    @classmethod
    async def process(cls, host: Host, keyring: str | None = None) -> list[GpgKeyInfo]:
        shell = cls.command(keyring)
        gpg_secret_keys = getattr(host, "gpg_secret_keys", None)
        if gpg_secret_keys is not None:
            # SimulatedHost seam: the state holds the default keychain's
            # answer, so ``keyring`` does not filter it. Copy each entry
            # so callers never mutate the simulated state.
            return _copy_key_entries(gpg_secret_keys)
        # Real transport: list the secret keys over asyncssh.
        stdout = await host.run_shell(shell)
        return cls.parse(stdout.splitlines())


#: Numeric ownertrust values ``gpg --export-ownertrust`` prints, mapped to
#: their level names (verified empirically against gpg 2.4.7: a freshly
#: generated own key exports ``6``, and both ``5`` and ``6`` mean ultimate
#: trust; every value 1-6 round-trips through ``--import-ownertrust``).
_TRUST_LEVELS = {
    1: "unknown",
    2: "never",
    3: "marginal",
    4: "full",
    5: "ultimate",
    6: "ultimate",
}


class GpgKeyTrust(TypedDict):
    """
    Attributes:
        fingerprint: The full fingerprint of the key the ownertrust
            assignment names.
        ownertrust: The raw numeric trust value exactly as the export
            line prints it (1-6; 5 and 6 both mean ultimate).
        level: The trust level name for ``ownertrust`` (``unknown``,
            ``never``, ``marginal``, ``full`` or ``ultimate``); an
            unrecognized number maps to ``unknown`` while keeping the
            raw value.
    """

    # One ownertrust assignment (``GpgTrust``' entry), as a plain dict.
    # ``level`` is the comparison-friendly projection ``gpg.trust`` checks
    # for idempotence, so ``5`` and ``6`` both satisfy an ``ultimate``
    # request without rewriting the trustdb.

    fingerprint: str
    ownertrust: int
    level: str


class GpgTrust:
    """The ownertrust assignments in the executing user's trust database.

    **Commands:**

    ```bash
    gpg --batch --export-ownertrust 2>/dev/null || true
    ```

    **Returns:**

    ```python
    list[GpgKeyTrust]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/gpg/gpg_gpg_trust.py``.
    """

    # A reemote original: ``gpg
    # --export-ownertrust`` prints ``FPR:N:`` lines (plus ``#`` comment
    # headers) which parse into ``GpgKeyTrust`` entries, for example::
    #
    #     [
    #         {
    #             "fingerprint": "F8FCE1E7C282D5025B12B05E3B2CE2047E4DBF5B",
    #             "ownertrust": 6,
    #             "level": "ultimate",
    #         },
    #     ]
    #
    # The numeric-to-level mapping (``_TRUST_LEVELS``) was verified against
    # a local gpg 2.4.7 run: unknown=1, never=2, marginal=3, full=4 and
    # ultimate=5 or 6 (a freshly generated own key exports 6). Entries keep
    # the export order; comment lines, blank lines and lines whose trust
    # field is not an integer are skipped. The ``|| true`` guard swallows
    # every gpg failure -- including a missing ``gpg`` (exit 127) -- so an
    # unreadable or absent trust database reads as ``[]`` rather than
    # raising ``CommandNotFoundError``.

    #: Shell exporting the trust database's ownertrust assignments.
    COMMAND = "gpg --batch --export-ownertrust 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[GpgKeyTrust]:
        """Parse ``FPR:N:`` ownertrust lines into trust entries. Pure and testable."""
        entries: list[GpgKeyTrust] = []
        for raw in output:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            bits = line.split(":")
            if len(bits) < 2 or not bits[0]:
                continue
            try:
                ownertrust = int(bits[1])
            except ValueError:
                continue
            entries.append(
                {
                    "fingerprint": bits[0],
                    "ownertrust": ownertrust,
                    "level": _TRUST_LEVELS.get(ownertrust, "unknown"),
                }
            )
        return entries

    @classmethod
    async def process(cls, host: Host) -> list[GpgKeyTrust]:
        gpg_trust = getattr(host, "gpg_trust", None)
        if gpg_trust is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # each entry so callers never mutate the simulated state.
            return [{**entry} for entry in gpg_trust]
        # Real transport: export the ownertrust assignments over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class GpgKeyMetadata(TypedDict):
    """
    Attributes:
        id: The primary key's id (the last 16 hex digits of the
            fingerprint).
        fingerprint: The primary key's full fingerprint; ``""`` when the
            listing carried no ``fpr`` record.
        validity: GPG's validity letter for the key (``u`` ultimate,
            ``f`` full, ``-`` unknown, ...), exactly as the colon record
            prints it.
        length: The primary key's length in bits (e.g. 4096).
        algo: The public-key algorithm number as a string (``1`` RSA,
            ``22`` EdDSA, ...), exactly as the colon record prints it.
        created: The creation date as a raw epoch-seconds string (e.g.
            ``"1794067588"``), kept raw so entries stay JSON-printable.
        capabilities: The key's capability letters (e.g. ``escarESCA``),
            exactly as the colon record prints them.
        expires: The expiration date as a raw epoch-seconds string; absent
            when the key never expires (an empty colon field).
        uids: Every user id string attached to the key, in listing order;
            ``[]`` when the listing carried no ``uid`` records.
        subkeys: The key's subkeys as ``GpgKeyInfo`` entries (only the last
            ``sub`` record before the next primary key is kept, the shared
            parser's convention).
    """

    # One key's full metadata (``GpgKeyDetails``' answer), as a plain dict.
    # Dates stay raw epoch strings rather than datetime objects so the
    # whole dict serializes with ``json.dumps`` directly.

    id: str
    fingerprint: str
    validity: str
    length: int
    algo: str
    created: str
    capabilities: str
    expires: NotRequired[str]
    uids: list[str]
    subkeys: list[GpgKeyInfo]


class GpgKeyDetails:
    """One key's full metadata from the executing user's keychain, or None.

    **Commands:**

    ```bash
    gpg --list-keys --with-colons -- <keyid> 2>/dev/null || true
    ```

    **Returns:**

    ```python
    GpgKeyMetadata | None
    ```

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/gpg/gpg_gpg_key_details.py``.
    """

    # A reemote original: a targeted
    # ``gpg --list-keys --with-colons -- <keyid>`` (keyid, long id or
    # fingerprint, shlex-quoted) parses into one ``GpgKeyMetadata`` dict,
    # for example::
    #
    #     {
    #         "id": "3B2CE2047E4DBF5B",
    #         "fingerprint": "F8FCE1E7C282D5025B12B05E3B2CE2047E4DBF5B",
    #         "validity": "u",
    #         "length": 2048,
    #         "algo": "1",
    #         "created": "1794067588",
    #         "capabilities": "escarESCA",
    #         "uids": ["Trust Probe <probe@example.com>"],
    #         "subkeys": [],
    #     }
    #
    # ``_parse_key_records`` reads the structural fields (id, validity,
    # length, fingerprint, subkeys) from the first primary key; the first
    # ``pub`` record's fields 4/6/7/12 supply algo/created/expires/
    # capabilities and every ``uid`` record contributes to ``uids`` (the
    # shared parser keeps only the last one). An empty keyid raises
    # ``ValueError``; a keyid matching nothing prints no records and reads
    # as ``None``. The ``|| true`` guard swallows every gpg failure --
    # including a missing ``gpg`` (exit 127) -- so an absent key and an
    # absent gpg both read as ``None`` rather than raising
    # ``CommandNotFoundError``.

    @classmethod
    def command(cls, keyid: str) -> str:
        """Shell listing one key's colon records, shlex-quoted.

        An empty keyid raises ``ValueError`` (it would otherwise list the
        whole keychain).
        """
        if not keyid:
            raise ValueError("keyid must be a non-empty key id or fingerprint")
        return f"gpg --list-keys --with-colons -- {shlex.quote(keyid)} 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> GpgKeyMetadata | None:
        """Parse one key's colon records into metadata, or None. Pure and testable."""
        lines = list(output)
        keys = _parse_key_records(lines)
        if not keys:
            return None
        key = keys[0]

        # The first pub record exists whenever the parser found a key;
        # its fixed-position fields carry what GpgKeyInfo does not keep.
        pub = next(bits for line in lines if (bits := line.split(":"))[0] == "pub")

        metadata: GpgKeyMetadata = {
            "id": key["id"],
            "fingerprint": key.get("fingerprint", ""),
            "validity": key["validity"],
            "length": key["length"],
            "algo": pub[3],
            "created": pub[5],
            "capabilities": pub[11],
            "uids": [bits[9] for line in lines if (bits := line.split(":"))[0] == "uid"],
            "subkeys": key.get("subkeys", []),
        }
        if pub[6]:
            metadata["expires"] = pub[6]
        return metadata

    @classmethod
    async def process(cls, host: Host, keyid: str) -> GpgKeyMetadata | None:
        # Build the shell first, even on the simulated path, so an empty
        # keyid is rejected identically on both transports.
        shell = cls.command(keyid)
        gpg_key_details = getattr(host, "gpg_key_details", None)
        if gpg_key_details is not None:
            # SimulatedHost seam: the state is keyed by keyid. An unseeded
            # keyid reads as None (the simulated stand-in for an absent
            # key). Copy the entry (and its nested lists) so callers never
            # mutate the simulated state.
            entry = gpg_key_details.get(keyid)
            if entry is None:
                return None
            return {
                **entry,
                "uids": list(entry["uids"]),
                "subkeys": _copy_key_entries(entry["subkeys"]),
            }
        # Real transport: list the one key over asyncssh.
        stdout = await host.run_shell(shell)
        return cls.parse(stdout.splitlines())


class GpgVerification(TypedDict):
    """
    Attributes:
        valid: True only when gpg reported a good signature (``GOODSIG``).
        status: The deciding status line lowercased (``goodsig``,
            ``badsig``, ``no_pubkey`` or ``errsig``), or ``error`` when no
            recognized status line was printed at all.
        keyid: The signing key's id as the status line names it; absent
            when ``status`` is ``error``.
        signer: The signing key's user id string; present only for
            ``goodsig`` and ``badsig`` (the statuses that carry one).
    """

    # ``GpgVerify``'s answer, as a plain dict. ``valid`` is deliberately
    # signature-correctness only: a good signature from an untrusted key
    # is still ``valid=True`` (gpg's TRUST_* lines are not consulted).

    valid: bool
    status: str
    keyid: NotRequired[str]
    signer: NotRequired[str]


class GpgVerify:
    """The outcome of verifying a signature on the target host, read-only.

    **Commands:**

    ```bash
    gpg --batch --status-fd 1 --verify <signature> [<file>] 2>/dev/null || true
    ```

    **Returns:**

    ```python
    GpgVerification
    ```

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/gpg/gpg_gpg_verify.py``.
    """

    # A reemote original (verification is
    # modeled as a read-only fact, not an operation): ``gpg --status-fd 1
    # --verify`` prints machine-readable ``[GNUPG:]`` status lines which
    # parse into one ``GpgVerification`` dict, for example::
    #
    #     {
    #         "valid": True,
    #         "status": "goodsig",
    #         "keyid": "3B2CE2047E4DBF5B",
    #         "signer": "Trust Probe <probe@example.com>",
    #     }
    #
    # ``signature`` is a path on the TARGET host (shlex-quoted); an empty
    # one raises ``ValueError``. Without ``file`` the signature must be
    # attached or cleartext; with it the signature is detached over that
    # file (also shlex-quoted). When several status lines appear the
    # deciding one is picked by precedence GOODSIG > BADSIG > NO_PUBKEY >
    # ERRSIG -- NO_PUBKEY wins over the ERRSIG that accompanies it because
    # the missing key is the actionable diagnosis. No recognized status
    # line at all reads as ``{"valid": False, "status": "error"}``; the
    # ``|| true`` guard swallows every gpg failure -- including a missing
    # ``gpg`` (exit 127) -- so that error answer also covers an absent
    # gpg, deliberately not raising ``CommandNotFoundError``.

    @classmethod
    def command(cls, signature: str, file: str | None = None) -> str:
        """Shell verifying ``signature`` (optionally detached over ``file``).

        Both paths are shlex-quoted; an empty signature raises
        ``ValueError``.
        """
        if not signature:
            raise ValueError("signature must be a non-empty path on the target host")
        shell = f"gpg --batch --status-fd 1 --verify {shlex.quote(signature)}"
        if file:
            shell += f" {shlex.quote(file)}"
        return f"{shell} 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> GpgVerification:
        """Parse ``[GNUPG:]`` status lines into a verification. Pure and testable."""
        found: dict[str, list[str]] = {}
        for raw in output:
            line = raw.strip()
            if not line.startswith("[GNUPG:]"):
                continue
            bits = line.split()
            if len(bits) >= 3 and bits[1] in ("GOODSIG", "BADSIG", "NO_PUBKEY", "ERRSIG"):
                found.setdefault(bits[1], bits[2:])

        for status in ("GOODSIG", "BADSIG", "NO_PUBKEY", "ERRSIG"):
            if status in found:
                bits = found[status]
                verification: GpgVerification = {
                    "valid": status == "GOODSIG",
                    "status": status.lower(),
                    "keyid": bits[0],
                }
                if status in ("GOODSIG", "BADSIG"):
                    verification["signer"] = " ".join(bits[1:])
                return verification

        return {"valid": False, "status": "error"}

    @classmethod
    async def process(cls, host: Host, signature: str, file: str | None = None) -> GpgVerification:
        # Build the shell first, even on the simulated path, so an empty
        # signature is rejected identically on both transports.
        shell = cls.command(signature, file)
        gpg_verify = getattr(host, "gpg_verify", None)
        if gpg_verify is not None:
            # SimulatedHost seam: the state is keyed by the signature
            # path, so ``file`` does not select between entries. An
            # unseeded path reads as the error answer (the simulated
            # stand-in for a failed verification). Copy the entry so
            # callers never mutate the simulated state.
            return {**gpg_verify.get(signature, {"valid": False, "status": "error"})}
        # Real transport: verify the signature over asyncssh.
        stdout = await host.run_shell(shell)
        return cls.parse(stdout.splitlines())


# --- Shared operation helpers --------------------------------------------------


def _matches_keyid(existing_key_id: str, clean_key: str) -> bool:
    """Whether an existing key id matches a cleaned (no 0x, uppercase) key id.

    Supports every common GPG key
    id form -- a short 8-char id is a suffix of the existing id, a long
    16-char id matches exactly, a prefix matches the start, and a full
    fingerprint ends with the existing id. Shared by ``gpg.key`` (install
    idempotence and selective removal) and the keychain operations.
    """
    existing_upper = existing_key_id.upper()
    return (
        clean_key == existing_upper
        or existing_upper.endswith(clean_key)
        or existing_upper.startswith(clean_key)
        or clean_key.endswith(existing_upper)
    )


def _clean_keyid(keyid: str) -> str:
    """Strip a ``0x``/``0X`` prefix and uppercase a key id."""
    return keyid.replace("0x", "").replace("0X", "").upper()


# --- gpg.key -------------------------------------------------------------------


def _key_spool_path(dest: str) -> str:
    """The deterministic ``/tmp`` spool *file* path ``gpg.key`` uploads to.

    The key stream is spooled inside a per-dest directory rather than at the
    top level of ``/tmp``: the directory name is derived from the destination
    keyring path's sha1 so two keys targeting different paths never share (and
    clobber) one spool, while the same dest always yields the same name in both
    phases -- the request sequence stays deterministic. The stream is spooled to
    a fixed ``key.gpg`` file inside that directory, and the elevated install
    ``rm -rf``s the whole directory afterwards, so no leftover lands in
    ``/tmp``. The ``apt.key`` spool pattern, with a gpg-specific name so the two
    operations never collide on one host. The name avoids *collisions*, not a
    hostile local user: it is predictable, so on a multi-tenant host the ``/tmp``
    spool is still a symlink-race exposure (the same trade-off the other
    spool-and-install operations make).
    """
    digest = hashlib.sha1(dest.encode()).hexdigest()[:16]
    return f"/tmp/reemote-gpg-key-{digest}/key.gpg"


class _KeyringFileExists:
    """Whether a keyring file exists (gpg-local ``files.FileExists``).

    A private, in-namespace stand-in for ``files.FileExists``: ``gpg.key``
    must learn whether the destination keyring file is already installed
    (the no-keyid idempotence check) or still present (the whole-file
    removal check), but the zero-cross-module-import invariant forbids
    reaching into the ``files`` namespace -- the ``selinux._AutorelabelPending``
    pattern. It reads the *same* ``existing_files`` seam ``files.FileExists``
    reads (the store of existing paths) and runs the same ``test -e`` probe
    on the real transport, whose non-zero exit on a missing file maps to
    False, so only ``HostConnectionError`` can escape it.
    """

    @classmethod
    async def process(cls, host: Host, path: str) -> bool:
        existing = getattr(host, "existing_files", None)
        if existing is not None:
            # SimulatedHost seam (shared with files.FileExists): the file's
            # presence is membership in the existing-path store.
            return path in existing
        # Real transport: `test -e` over asyncssh (check is off in run_shell).
        stdout = await host.run_shell(f"test -e {shlex.quote(path)} && echo yes")
        return stdout.strip() == "yes"


@dataclass(frozen=True)
class GpgKeyUploadCommand(TransferCommand):
    """Spool the GPG key stream to a ``/tmp`` path on the target host.

    The first half of ``gpg.key``'s install and a *non-shell* descriptor: it
    copies the IO-like key content over the connection's scp channel via
    ``Host.upload_file`` rather than running shell text, so it has no
    ``shell()`` and its loggable form is the ``__str__`` rendering
    ``command_shell`` falls back to. The copy rides as the login user --
    transfers are unprivileged, so the request's ``sudo`` does not apply to it
    (and ``command_shell`` renders it without the prefix) -- which is why the
    spool path is world-writable ``/tmp`` rather than a root-owned directory.
    ``src`` is the IO-like object itself (never a path): ``OperationRequest``
    compares it by identity, so a deploy must pass the *same* stream object in
    every phase -- define it once (e.g. at module level), not inside the
    generator.
    """

    src: IO[Any]  # IO-like GPG key content on the control machine
    dest: str  # spool path on the target host

    def __str__(self) -> str:
        return f"scp {getattr(self.src, 'name', '<stream>')} -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        # scp does not create parent directories, and the spool now lives
        # inside a sha1-derived /tmp directory rather than at the top level,
        # so mkdir -p it first. Unprivileged: /tmp is world-writable, so the
        # login user can create the directory, and a transfer never elevates.
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        await host.run_shell(f"mkdir -p {parent}")
        await host.upload_file(self.src, self.dest)


@dataclass(frozen=True)
class GpgKeyInstallCommand(Command):
    """Install the spooled key as a keyring file, then remove the spool file.

    The second half of ``gpg.key``'s install and the elevated one: writing
    under ``/etc/apt/keyrings/`` (or any system keyring directory) needs root,
    so the request is flagged ``sudo=True`` and the whole sequence is wrapped
    in ``sh -c`` -- a bare ``sudo`` prefix would elevate only the first
    command of the ``&&`` chain and leave the cleanup running as the login
    user, whereas ``sudo sh -c '...'`` runs every step as root, which can
    always remove the login-user spool file. The sequence creates the
    destination directory, then applies a content sniff when
    ``dearmor`` is set: an ASCII-armored key (``BEGIN PGP PUBLIC KEY BLOCK``
    in the bytes, regardless of any file extension) is converted to binary
    form with ``gpg --batch --yes --dearmor`` (``--yes`` overwrites an
    existing output, which ``--batch`` alone refuses), binary content is
    copied verbatim;
    ``dearmor=False`` copies unconditionally (the ``no_dearmor``
    branch). Finally the keyring is chmod'ed ``0644`` (the fixed mode --
    the ``mode`` parameter is deliberately dropped, the ``apt.key``
    precedent) and the spool file removed. All paths are shell-quoted.
    """

    spool: str  # where GpgKeyUploadCommand wrote the key stream
    dest: str  # keyring file path on the target host
    dearmor: bool = True  # sniff for armor and convert, or copy verbatim

    def shell(self) -> str:
        spool = shlex.quote(self.spool)
        # The spool file lives inside a sha1-derived /tmp directory; remove
        # the whole directory so no leftover lands in /tmp.
        spool_dir = shlex.quote(str(PurePosixPath(self.spool).parent))
        dest = shlex.quote(self.dest)
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        if self.dearmor:
            marker = shlex.quote("BEGIN PGP PUBLIC KEY BLOCK")
            install = (
                f"if grep -q {marker} {spool}; then "
                f"gpg --batch --yes --dearmor -o {dest} {spool}; "
                f"else cp {spool} {dest}; fi"
            )
        else:
            install = f"cp {spool} {dest}"
        inner = f"mkdir -p {parent} && {install} && chmod 0644 {dest} && rm -rf {spool_dir}"
        return f"sh -c {shlex.quote(inner)}"


@dataclass(frozen=True)
class GpgKeyRemoveCommand(Command):
    """``rm -f <path>``: remove a whole keyring file.

    The ``present=False`` direction of ``gpg.key`` without ``keyid`` (and
    the all-keys-match removal of an armored ``.asc`` keyring, which cannot
    be edited in-place). ``rm -f`` never fails on an absent file, but the
    operation only yields it when the file exists, so an already-absent
    keyring reports no change. The path is shell-quoted.
    """

    path: str

    def shell(self) -> str:
        return f"rm -f {shlex.quote(self.path)}"


@dataclass(frozen=True)
class GpgKeyDeleteKeysCommand(Command):
    """``gpg --delete-keys`` against one explicit keyring file.

    The selective-removal half of ``gpg.key``'s ``present=False`` with
    ``keyid``: delete the named fingerprints from a binary keyring
    (``--no-default-keyring --keyring`` scopes gpg to that one file, never
    the user's keychain), with ``--batch --yes`` so no confirmation is
    asked. The fingerprints are the full ones the ``GpgKeyrings`` fact
    attached to the matching keys (gpg deletes by
    fingerprint to avoid short-id collisions). A single gpg invocation, so
    a bare ``sudo`` prefix elevates all of it; the emptied-file pruning is
    the separate ``GpgKeyEmptyCleanupCommand`` that follows. The keyring
    path and fingerprints are shell-quoted.
    """

    keyring: str
    fingerprints: tuple[str, ...]

    def shell(self) -> str:
        keyring = shlex.quote(self.keyring)
        joined = " ".join(shlex.quote(fpr) for fpr in self.fingerprints)
        return (
            f"gpg --batch --yes --no-default-keyring --keyring {keyring}"
            f" --delete-keys {joined}"
        )


@dataclass(frozen=True)
class GpgKeyEmptyCleanupCommand(Command):
    """Remove a keyring file if the delete left it empty.

    The second step of selective removal (the pruning command): list
    the keyring's remaining keys and ``rm -f`` the file when no ``pub``
    record survives. Wrapped in ``sh -c`` because the pipeline's ``||``
    must run at the same privilege as the listing -- a bare ``sudo`` prefix
    would elevate only the ``gpg`` at the head of the pipe and leave the
    ``rm`` running as the login user. The keyring path is shell-quoted.
    """

    keyring: str

    def shell(self) -> str:
        keyring = shlex.quote(self.keyring)
        inner = (
            f"gpg --batch --no-default-keyring --keyring {keyring}"
            f" --list-keys 2>/dev/null | grep -q {shlex.quote('^pub')}"
            f" || rm -f {keyring}"
        )
        return f"sh -c {shlex.quote(inner)}"


def _keyring_removal_commands(keyring: GpgKeyring, keyids: list[str]) -> list[Command]:
    """Plan the selective removal of ``keyids`` from one keyring entry.

    The shared selective-removal core: collect the fingerprints of the
    matching keys (deduped,
    in keyid order), then branch on the keyring format -- an armored
    ``.asc`` file cannot be edited in-place, so partial matches raise
    ``ValueError`` (the house validation exception) while an all-keys match
    removes the whole file; a binary keyring gets the delete-keys command
    plus the empty-file cleanup. No matches plans nothing.
    """
    fingerprints: list[str] = []
    for kid in keyids:
        clean_key = _clean_keyid(kid)
        for key in keyring["keys"]:
            if _matches_keyid(key["id"], clean_key):
                fingerprint = key.get("fingerprint", key["id"])
                if fingerprint not in fingerprints:
                    fingerprints.append(fingerprint)

    if not fingerprints:
        return []

    if keyring["format"] == "asc":
        if len(fingerprints) < len(keyring["keys"]):
            raise ValueError(
                f"Cannot remove individual keys from armored keyring {keyring['path']!r}: "
                "the file contains other keys and cannot be edited in-place. "
                "Remove the file explicitly with present=False and no keyid."
            )
        return [GpgKeyRemoveCommand(path=keyring["path"])]

    return [
        GpgKeyDeleteKeysCommand(keyring=keyring["path"], fingerprints=tuple(fingerprints)),
        GpgKeyEmptyCleanupCommand(keyring=keyring["path"]),
    ]


async def key(
    ctx: OpContext,
    src: IO[Any] | None = None,
    dest: str | None = None,
    keyid: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    working_dirs: list[str] | tuple[str, ...] | None = None,
    dearmor: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install or remove GPG keyring files on the target host.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (IO[Any] | None, optional): The key content as a seekable IO-like
            object on the control machine (an ``io.BytesIO``/``io.StringIO``,
            a file opened with ``open()``, ...), rewound to position 0 before
            it is read; ASCII-armored and binary key content are both
            accepted. Required for installation, unused for removal.
            Defaults to None.
        dest (str | None, optional): The keyring file path on the target
            host. Required for installation; for removal it names the one
            keyring to act on (the whole file without ``keyid``, selective
            keys with it). Defaults to None.
        keyid (str | list[str] | tuple[str, ...] | None, optional): Key id(s)
            or fingerprint(s) (``0x`` prefixes accepted). Prefer tuples when
            building requests via ``op()``, since ``OperationRequest`` kwargs
            must stay hashable. With ``present=True`` they make the install
            idempotent per key via ``GpgKeyrings``; with ``present=False``
            they select the keys to delete from ``dest`` or from every
            keyring under ``working_dirs``. Defaults to None.
        present (bool, optional): Whether the key/keyring should be present
            (True) or absent (False). Defaults to True.
        working_dirs (list[str] | tuple[str, ...] | None, optional):
            Directories to scan for keyrings when removing by ``keyid``
            without ``dest`` (prefer tuples via ``op()``). Unused for
            installation. Defaults to None.
        dearmor (bool, optional): Whether to convert ASCII-armored key
            content to binary form on install (the content is sniffed, not
            the file extension); False copies the stream verbatim. Defaults
            to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated gpg keyring commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/gpg/gpg_key.py``.
    """

    # ``gpg.key``: install and remove keyring files, stream-only sources.
    #
    # Install: ``src`` is an IO-like *stream* on the controller (a
    # filename/URL ``src`` -- which the target host reads or downloads itself
    # -- is deliberately dropped: reemote takes IO streams, not
    # controller-side file locations, and a ``str`` raises ``ValueError``;
    # fetch a URL on the controller and pass its bytes). The bytes ride an
    # unprivileged ``GpgKeyUploadCommand`` scp transfer to a fixed ``/tmp``
    # spool path, then a ``GpgKeyInstallCommand`` runs the elevated install:
    # mkdir the parent, dearmor-or-copy the content, chmod 0644, remove the
    # spool. Flag the request ``sudo=True`` for system keyring directories;
    # the transfer stays unprivileged regardless. Idempotence:
    # without ``keyid`` an existing ``dest`` file is left alone
    # (the gpg-local ``_KeyringFileExists`` probe), with ``keyid`` every
    # requested id must already be in ``dest``'s ``GpgKeyrings`` entry.
    # The mode-repair chmod on the unchanged path is dropped along
    # with the ``mode`` parameter (0644 is fixed, the ``apt.key`` precedent),
    # and its ``keyserver`` fetch is dropped -- ``gpg.refresh_keys`` is the
    # keyserver touchpoint.
    #
    # Removal (``present=False``): ``dest`` alone removes the whole keyring
    # file (idempotent on the same existence probe); ``dest`` + ``keyid``
    # selectively deletes the matching keys from that one keyring, pruning
    # the file when the delete empties it; ``keyid`` + ``working_dirs``
    # scans every keyring under those directories for the same selective
    # removal. Selective removal from an armored ``.asc`` file that still
    # holds other keys raises ``ValueError`` (the house validation
    # exception). ``**kwargs`` absorbs
    # any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    if isinstance(src, str):
        # ValueError (not TypeError) keeps gpg.key consistent with apt.key
        # and dnf.key, which also reject an unusable string ``src`` with
        # ValueError; TRY004 assumes any isinstance guard should raise
        # TypeError instead.
        raise ValueError(  # noqa: TRY004
            "gpg.key does not support a filename/URL src (reemote takes IO "
            "streams, not controller-side file locations); pass the key content "
            f"as an IO-like object, got: {src!r}"
        )
    keyids = [keyid] if isinstance(keyid, str) else list(keyid) if keyid else []

    if not present:
        if not dest and not (keyids and working_dirs):
            raise ValueError(
                "For removal, either `dest` or both `keyid` and `working_dirs` "
                "must be provided"
            )

        if dest and not keyids:
            # Whole-file removal, idempotent on the existence probe.
            if await ctx.facts.get(_KeyringFileExists, dest):
                ctx.changed = True
                ctx.reason = f"remove GPG keyring file {dest}"
                yield GpgKeyRemoveCommand(path=dest)
            else:
                ctx.reason = f"GPG keyring file {dest} already absent"
            return

        # Selective removal: one keyring (dest) or a directory scan. The
        # directories ride as a tuple -- FactCache keys on hashable args.
        directories = (
            (str(PurePosixPath(dest).parent),) if dest else tuple(working_dirs or [])
        )
        keyrings = await ctx.facts.get(GpgKeyrings, directories)
        reasons: list[str] = []
        for keyring in keyrings:
            if dest and keyring["path"] != dest:
                continue
            commands = _keyring_removal_commands(keyring, keyids)
            if commands:
                reasons.append(f"remove GPG keys {keyids} from {keyring['path']}")
                for command in commands:
                    yield command
        if reasons:
            ctx.changed = True
            ctx.reason = "; ".join(reasons)
        else:
            scope = dest or ", ".join(directories)
            ctx.reason = f"GPG keys {keyids} already absent from {scope}"
        return

    # Installation.
    if src is None:
        raise ValueError("`src` must be provided for installation")
    if not dest:
        raise ValueError("`dest` must be provided for installation")

    if keyids:
        # Every requested key must already be in dest's keyring entry.
        keyrings = await ctx.facts.get(GpgKeyrings, (str(PurePosixPath(dest).parent),))
        entry = next((kr for kr in keyrings if kr["path"] == dest), None)
        if entry and all(
            any(_matches_keyid(key["id"], _clean_keyid(kid)) for key in entry["keys"])
            for kid in keyids
        ):
            ctx.reason = f"GPG keys {keyids} already exist in {dest}"
            return
    elif await ctx.facts.get(_KeyringFileExists, dest):
        ctx.reason = f"GPG keyring {dest} already exists"
        return

    ctx.changed = True
    ctx.reason = f"install GPG key from {getattr(src, 'name', '<stream>')} to {dest}"
    spool = _key_spool_path(dest)
    yield GpgKeyUploadCommand(src=src, dest=spool)
    yield GpgKeyInstallCommand(spool=spool, dest=dest, dearmor=dearmor)


# --- gpg.dearmor ---------------------------------------------------------------


@dataclass(frozen=True)
class GpgDearmorCommand(Command):
    """Convert an armored key file on the target host to binary form.

    The whole of ``gpg.dearmor``: create the destination directory,
    ``gpg --batch --yes --dearmor -o <dest> <src>`` (``--yes`` overwrites
    an existing output, which ``--batch`` alone refuses -- the stateless
    operation converts on every run), then chmod the output
    ``0644`` (the fixed mode -- the ``mode`` parameter is deliberately
    dropped). Both paths are files already on the target host (no
    transfer). Wrapped in ``sh -c`` so a ``sudo``-flagged request elevates
    every step of the ``&&`` chain, not just the ``mkdir``. All paths are
    shell-quoted.
    """

    src: str
    dest: str

    def shell(self) -> str:
        src = shlex.quote(self.src)
        dest = shlex.quote(self.dest)
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        inner = (
            f"mkdir -p {parent} && gpg --batch --yes --dearmor -o {dest} {src}"
            f" && chmod 0644 {dest}"
        )
        return f"sh -c {shlex.quote(inner)}"


async def dearmor(
    ctx: OpContext, src: str, dest: str, **kwargs: Any
) -> AsyncGenerator[Command]:
    """Convert an ASCII-armored GPG key file to binary format on the target host.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (str): Path of the ASCII-armored key file on the target host.
        dest (str): Path of the binary key file to write on the target host;
            its parent directory is created when missing.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The dearmor command, always (the operation is stateless).

    **Raises:**

    ```python
    ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/gpg/gpg_dearmor.py``.
    """

    # ``gpg.dearmor``: convert an armored key file to binary form.
    #
    # Stateless/non-idempotent: it always converts and always
    # reports a change, reading no fact (the output is overwritten on every
    # run). Both paths are remote -- to convert a controller-side stream,
    # install it with ``gpg.key`` instead, whose install dearmors on the
    # target. It deliberately stays in-place rather than joining the
    # spool-and-install family (``gpg.key``, ``apt.repo``): the payload is
    # gpg's own binary output produced on the target host, not
    # controller-side file text, and ``dest`` is overwritten wholesale --
    # there is no existing content a read-back fact could preserve.
    # Spooling would mean reimplementing armor decoding (base64 + CRC24)
    # controller-side just to produce binary content the family's ``str``
    # payload cannot carry, and the ``sh -c`` wrap already keeps a
    # ``sudo``-flagged request elevated across the whole chain. The
    # ``mode`` parameter is deliberately dropped (0644 is fixed, the
    # ``apt.key`` precedent). Flag the request ``sudo=True`` when
    # ``dest`` lives in a system directory. ``**kwargs`` absorbs any
    # global arguments (``op()`` already strips ``name``/``sudo``/``su``).
    if not src:
        raise ValueError("gpg.dearmor requires a non-empty src path on the target host")
    if not dest:
        raise ValueError("gpg.dearmor requires a non-empty dest path on the target host")
    ctx.changed = True
    ctx.reason = f"dearmor {src} to {dest}"
    yield GpgDearmorCommand(src=src, dest=dest)


# --- gpg.import_key --------------------------------------------------------------


def _import_spool_path(dest: str | None = None) -> str:
    """The deterministic ``/tmp`` spool *file* path ``gpg.import_key`` uploads to.

    The key stream is spooled inside a subdirectory of ``/tmp`` rather than at
    its top level. With ``dest`` the directory name is derived from the
    destination keyring path's sha1 (the ``_key_spool_path`` pattern), so two
    imports targeting different keyrings never share (and clobber) one spool;
    without it -- the key lands in the executing user's keychain, so there is
    no path to hash -- the directory name is derived from a fixed sha1 constant
    instead. Either way the name is identical across the prepare and apply
    phases (a per-materialization temp name would diverge and trip the
    determinism guard). The stream is spooled to a fixed ``import.gpg`` file
    inside that directory, and the elevated import ``rm -rf``s the whole
    directory afterwards, so no leftover lands in ``/tmp``. An import-specific
    name so ``gpg.key`` and ``gpg.import_key`` never collide on one host.
    """
    seed = dest.encode() if dest else b"gpg.import_key"
    digest = hashlib.sha1(seed).hexdigest()[:16]
    return f"/tmp/reemote-gpg-import-{digest}/import.gpg"


@dataclass(frozen=True)
class GpgImportUploadCommand(TransferCommand):
    """Spool the GPG key stream to a ``/tmp`` path for ``gpg.import_key``.

    The first half of ``gpg.import_key`` and a *non-shell* descriptor, the
    ``GpgKeyUploadCommand`` pattern: copy the IO-like key content over the
    connection's scp channel via ``Host.upload_file``, unprivileged (the
    request's ``sudo`` never applies to a transfer), logging via
    ``__str__``. ``src`` is the IO-like object itself -- compared by
    identity across phases, so a deploy must pass the same stream object
    in every phase.
    """

    src: IO[Any]  # IO-like GPG key content on the control machine
    dest: str  # spool path on the target host

    def __str__(self) -> str:
        return f"scp {getattr(self.src, 'name', '<stream>')} -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        # scp does not create parent directories, and the spool now lives
        # inside a sha1-derived /tmp directory rather than at the top level,
        # so mkdir -p it first. Unprivileged: /tmp is world-writable, so the
        # login user can create the directory, and a transfer never elevates.
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        await host.run_shell(f"mkdir -p {parent}")
        await host.upload_file(self.src, self.dest)


@dataclass(frozen=True)
class GpgImportCommand(Command):
    """``gpg --import`` the spooled key, then remove the spool file.

    The second half of ``gpg.import_key``: ``gpg --batch --yes --import``
    reads the spooled stream into the executing user's default keychain
    (``--yes`` so an existing key is merged without a prompt), then the
    spool file is removed. With ``keyring`` the import is scoped to that one
    keyring file instead (``--no-default-keyring --keyring``, the
    ``GpgKeyDeleteKeysCommand`` form): the destination directory is created,
    gpg merges the key into the file -- additive, the keys already there
    stay -- the file is chmod'ed ``0644`` (the fixed mode, the
    ``GpgKeyInstallCommand`` precedent) and the spool removed. Wrapped in
    ``sh -c`` so a ``sudo``-flagged request elevates every step of the
    ``&&`` chain -- though the keychain operations normally run as the login
    user, whose ``GNUPGHOME`` they act on. All paths are shell-quoted.
    """

    spool: str  # where GpgImportUploadCommand wrote the key stream
    keyring: str | None = None  # explicit keyring file, or the default keychain

    def shell(self) -> str:
        spool = shlex.quote(self.spool)
        # The spool file lives inside a sha1-derived /tmp directory; remove
        # the whole directory so no leftover lands in /tmp.
        spool_dir = shlex.quote(str(PurePosixPath(self.spool).parent))
        if self.keyring:
            keyring = shlex.quote(self.keyring)
            parent = shlex.quote(str(PurePosixPath(self.keyring).parent))
            inner = (
                f"mkdir -p {parent} && "
                f"gpg --batch --yes --no-default-keyring --keyring {keyring}"
                f" --import {spool} && chmod 0644 {keyring} && rm -rf {spool_dir}"
            )
        else:
            inner = f"gpg --batch --yes --import {spool} && rm -rf {spool_dir}"
        return f"sh -c {shlex.quote(inner)}"


async def import_key(
    ctx: OpContext,
    src: IO[Any],
    keyid: str | None = None,
    secret: bool = False,
    dest: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Import a GPG key stream into the executing user's keychain or a keyring file.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (IO[Any]): The key content as a seekable IO-like object on the
            control machine (an ``io.BytesIO``/``io.StringIO``, a file
            opened with ``open()``, ...), rewound to position 0 before it
            is read; public and secret key material are both accepted.
        keyid (str | None, optional): The key id or fingerprint (``0x``
            prefix accepted) that makes the import idempotent: when the
            keychain -- or ``dest``'s keyring -- already holds a matching
            key, nothing is imported. Without it the import always runs.
            Defaults to None.
        secret (bool, optional): Whether ``keyid`` is checked against the
            secret keys (``GpgSecretKeys``) instead of the public keys
            (``GpgKeys``). Cannot be combined with ``dest``. Defaults to
            False.
        dest (str | None, optional): Path of a keyring file on the target
            host to import into instead of the default keychain
            (``--no-default-keyring --keyring``); its parent directory is
            created when missing and the file is chmod'ed ``0644``. The
            import is additive -- keys already in the file stay -- and with
            ``keyid`` idempotence is decided against that file's
            ``GpgKeyrings`` entry. Defaults to None (the executing user's
            keychain).
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated gpg import commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/gpg/gpg_import_key.py``.
    """

    # A reemote original (``gpg.key`` installs keyring *files* wholesale;
    # this imports into the user's keychain, or additively into one keyring
    # file with ``dest``).
    #
    # The stream rides an unprivileged ``GpgImportUploadCommand`` scp
    # transfer to a fixed ``/tmp`` spool path, then ``GpgImportCommand``
    # runs ``gpg --batch --yes --import`` and removes the spool. Without
    # ``dest`` the import acts on the *executing user's* default
    # ``GNUPGHOME`` -- flag the request ``sudo=True`` to import into root's
    # keychain instead. With ``dest`` it is scoped to that one keyring file
    # (``--no-default-keyring --keyring``): gpg merges the key into the
    # file, leaving the keys already there in place -- the additive import
    # ``gpg.key``'s whole-file install cannot do -- and the file is
    # chmod'ed ``0644`` (the ``GpgKeyInstallCommand`` precedent); flag the
    # request ``sudo=True`` when ``dest`` lives in a system directory.
    # ``dest`` cannot combine with ``secret``: gpg keeps secret material in
    # ``GNUPGHOME``, never in an explicit keyring file. With ``keyid`` the
    # operation is idempotent against ``GpgKeys`` (or ``GpgSecretKeys``
    # with ``secret=True``), or against ``dest``'s own ``GpgKeyrings``
    # entry when ``dest`` is set (the ``gpg.key`` install pattern); without
    # it the import always runs and always reports a change: the stream's
    # fingerprint is unknown controller-side (reading it would mean parsing
    # OpenPGP packets), and gpg's own merge of an already-imported key is
    # harmless. ``str`` src is rejected like ``gpg.key``'s. ``**kwargs``
    # absorbs any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    if isinstance(src, str):
        # ValueError (not TypeError) for consistency with gpg.key/apt.key.
        raise ValueError(  # noqa: TRY004
            "gpg.import_key does not support a filename/URL src (reemote takes "
            "IO streams, not controller-side file locations); pass the key "
            f"content as an IO-like object, got: {src!r}"
        )
    if dest is not None and not dest:
        raise ValueError("gpg.import_key requires a non-empty dest keyring path")
    if secret and dest:
        raise ValueError(
            "gpg.import_key cannot combine dest with secret: gpg stores secret "
            "material in GNUPGHOME, not in an explicit keyring file"
        )

    if keyid:
        clean_key = _clean_keyid(keyid)
        if dest:
            # Path-scoped: decide against dest's own keyring entry, never
            # the default keychain (the gpg.key install pattern).
            keyrings = await ctx.facts.get(GpgKeyrings, (str(PurePosixPath(dest).parent),))
            entry = next((kr for kr in keyrings if kr["path"] == dest), None)
            if entry and any(_matches_keyid(key["id"], clean_key) for key in entry["keys"]):
                ctx.reason = f"GPG key {keyid} already imported into {dest}"
                return
        else:
            fact_cls = GpgSecretKeys if secret else GpgKeys
            keys = await ctx.facts.get(fact_cls)
            if any(_matches_keyid(key["id"], clean_key) for key in keys):
                ctx.reason = f"GPG key {keyid} already imported"
                return

    ctx.changed = True
    target = f" into {dest}" if dest else ""
    ctx.reason = f"import GPG key from {getattr(src, 'name', '<stream>')}{target}"
    spool = _import_spool_path(dest)
    yield GpgImportUploadCommand(src=src, dest=spool)
    yield GpgImportCommand(spool=spool, keyring=dest)


# --- gpg.export_key --------------------------------------------------------------


@dataclass(frozen=True)
class GpgExportCommand(Command):
    """``gpg --export`` one key to a file on the target host.

    The whole of ``gpg.export_key``: create the destination directory,
    then ``gpg --batch --yes [--armor] --export <keyid>`` (or
    ``--pinentry-mode loopback --export-secret-keys`` with ``secret``)
    redirected into ``dest``. The redirect needs a shell, so the sequence
    is wrapped in ``sh -c`` -- which also keeps a ``sudo``-flagged request
    elevated across the whole chain. ``--pinentry-mode loopback`` makes a
    passphrase-protected secret key fail fast instead of prompting (no
    passphrase is ever passed). The keyid and paths are shell-quoted.
    """

    keyid: str
    dest: str
    armor: bool = False
    secret: bool = False

    def shell(self) -> str:
        keyid = shlex.quote(self.keyid)
        dest = shlex.quote(self.dest)
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        parts = ["gpg", "--batch", "--yes"]
        if self.armor:
            parts.append("--armor")
        if self.secret:
            parts.append("--pinentry-mode loopback --export-secret-keys")
        else:
            parts.append("--export")
        export = " ".join(parts)
        inner = f"mkdir -p {parent} && {export} {keyid} > {dest}"
        return f"sh -c {shlex.quote(inner)}"


async def export_key(
    ctx: OpContext,
    keyid: str,
    dest: str,
    armor: bool = False,
    secret: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Export a GPG key from the executing user's keychain to a file.

    Args:
        ctx (OpContext): Execution context for the operation.
        keyid (str): The key id, long id or fingerprint to export
            (``0x`` prefixes accepted by gpg).
        dest (str): Path of the export file to write on the target host;
            its parent directory is created when missing.
        armor (bool, optional): Whether to write ASCII-armored output
            (``--armor``) instead of binary. Defaults to False.
        secret (bool, optional): Whether to export the secret key
            (``--export-secret-keys``) instead of the public key.
            Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The export command, always (the operation is stateless).

    **Raises:**

    ```python
    ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/gpg/gpg_export_key.py``.
    """

    # A reemote original.
    #
    # Stateless/non-idempotent like ``gpg.dearmor``: it always exports and
    # always reports a change, reading no fact (the output is overwritten
    # on every run). The export reads the *executing user's* default
    # ``GNUPGHOME``; flag the request ``sudo=True`` to export from root's
    # keychain or to write ``dest`` into a system directory. No passphrase
    # parameter exists -- secrets never ride in plans or logs -- so a
    # passphrase-protected secret export fails fast under
    # ``--pinentry-mode loopback`` rather than prompting; only unprotected
    # secret keys can be exported. ``**kwargs`` absorbs any further global
    # arguments (``op()`` already strips ``name``/``sudo``/``su``).
    #
    # Deliberately NOT a spool-and-install operation (unlike ``gpg.key``
    # and ``gpg.import_key``): the payload is ``gpg --export`` output
    # produced on the *target host*, not controller-side content, and the
    # redirect overwrites ``dest`` wholesale -- there is no existing file
    # text to preserve. Spooling would mean pulling the exported key
    # (secret material with ``secret=True``) back to the controller and
    # into plans/logs, which the no-secrets rule above forbids. The
    # ``sh -c`` wrap already keeps a ``sudo``-flagged request elevated
    # across the redirect, so the transfers-never-elevate problem the
    # spool family exists to solve does not arise here.
    if not keyid:
        raise ValueError("gpg.export_key requires a non-empty keyid")
    if not dest:
        raise ValueError("gpg.export_key requires a non-empty dest path on the target host")
    ctx.changed = True
    ctx.reason = f"export GPG key {keyid} to {dest}"
    yield GpgExportCommand(keyid=keyid, dest=dest, armor=armor, secret=secret)


# --- gpg.delete_key --------------------------------------------------------------


@dataclass(frozen=True)
class GpgDeleteKeyCommand(Command):
    """``gpg --delete-keys`` one key from the executing user's keychain.

    The whole of ``gpg.delete_key``: ``gpg --batch --yes --delete-keys
    <fingerprint>`` (or ``--delete-secret-and-public-keys``, which removes
    the secret *and* public halves, with ``secret``). The fingerprint is
    the full one the keychain fact attached to the matching key -- gpg
    deletes by fingerprint to avoid short-id collisions. A single gpg
    invocation, so a bare ``sudo`` prefix elevates all of it. The
    fingerprint is shell-quoted.
    """

    fingerprint: str
    secret: bool = False

    def shell(self) -> str:
        action = "--delete-secret-and-public-keys" if self.secret else "--delete-keys"
        return f"gpg --batch --yes {action} {shlex.quote(self.fingerprint)}"


async def delete_key(
    ctx: OpContext, keyid: str, secret: bool = False, **kwargs: Any
) -> AsyncGenerator[Command]:
    """Delete a GPG key from the executing user's keychain.

    Args:
        ctx (OpContext): Execution context for the operation.
        keyid (str): The key id, long id or fingerprint to delete (``0x``
            prefixes accepted); resolved to the full fingerprint from the
            keychain listing before deleting.
        secret (bool, optional): Whether to delete the secret *and* public
            key (``--delete-secret-and-public-keys``, looked up in
            ``GpgSecretKeys``) instead of only the public key
            (``--delete-keys``, looked up in ``GpgKeys``). Defaults to
            False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated gpg key-deletion commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/gpg/gpg_delete_key.py``.
    """

    # A reemote original (``gpg.key``
    # removes keyring *files*; this deletes from the user's keychain).
    #
    # Idempotent against ``GpgKeys`` (or ``GpgSecretKeys`` with
    # ``secret=True``): an absent key reports no change. The matching entry
    # is resolved to its full ``fingerprint`` (falling back to the key id
    # when the listing attached none -- the ``gpg.key`` selective-removal
    # precedent) because gpg refuses ambiguous short ids under ``--batch``.
    # The deletion acts on the *executing user's* default ``GNUPGHOME``;
    # flag the request ``sudo=True`` to delete from root's keychain.
    # ``--batch --yes`` skips gpg's confirmation prompt.
    # ``**kwargs`` absorbs any further global arguments (``op()`` already
    # strips ``name``/``sudo``/``su``).
    if not keyid:
        raise ValueError("gpg.delete_key requires a non-empty keyid")

    fact_cls = GpgSecretKeys if secret else GpgKeys
    keys = await ctx.facts.get(fact_cls)
    clean_key = _clean_keyid(keyid)
    entry = next((key for key in keys if _matches_keyid(key["id"], clean_key)), None)
    if entry is None:
        ctx.reason = f"GPG key {keyid} already absent"
        return

    ctx.changed = True
    ctx.reason = f"delete GPG key {keyid}"
    yield GpgDeleteKeyCommand(
        fingerprint=entry.get("fingerprint", entry["id"]), secret=secret
    )


# --- gpg.gen_key -----------------------------------------------------------------


@dataclass(frozen=True)
class GpgGenKeyCommand(Command):
    """``gpg --gen-key`` from a printf-fed batch parameter block.

    The whole of ``gpg.gen_key``: ``printf '%s\\n' <batch lines> |
    gpg --batch --gen-key`` generates one key from the Key-Type/Key-Length/
    Name-Real/Name-Email/Expire-Date parameters plus ``%no-protection``
    (the key is created without a passphrase -- no secret ever rides in a
    plan or log). Every batch line is a hashable string field, so no
    transfer is needed. Wrapped in ``sh -c`` because the pipeline's gpg
    must run at the same privilege as a ``sudo``-flagged request's printf.
    All parameter values are shell-quoted.
    """

    real_name: str
    email: str
    algo: str
    length: int
    expire: str

    def shell(self) -> str:
        lines = (
            f"Key-Type: {self.algo}",
            f"Key-Length: {self.length}",
            f"Name-Real: {self.real_name}",
            f"Name-Email: {self.email}",
            f"Expire-Date: {self.expire}",
            "%no-protection",
        )
        quoted = " ".join(shlex.quote(line) for line in lines)
        inner = f"printf '%s\\n' {quoted} | gpg --batch --gen-key"
        return f"sh -c {shlex.quote(inner)}"


async def gen_key(
    ctx: OpContext,
    real_name: str,
    email: str,
    algo: str = "rsa",
    length: int = 4096,
    expire: str = "never",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Generate a GPG key in the executing user's keychain.

    Args:
        ctx (OpContext): Execution context for the operation.
        real_name (str): The key user id's real name (the ``Name-Real``
            batch parameter).
        email (str): The key user id's email address (the ``Name-Email``
            batch parameter).
        algo (str, optional): The key algorithm (the ``Key-Type`` batch
            parameter, e.g. ``rsa``, ``eddsa``). Defaults to "rsa".
        length (int, optional): The key length in bits (the ``Key-Length``
            batch parameter). Defaults to 4096.
        expire (str, optional): The key's expiry (the ``Expire-Date``
            batch parameter: ``never``, a date, or a ``2y``-style
            interval). Defaults to "never".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated gpg key-generation commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/gpg/gpg_gen_key.py``.
    """

    # A reemote original.
    #
    # Idempotent on the user id: when ``GpgKeys`` already holds a key whose
    # ``uid`` is exactly "{real_name} <{email}>", nothing is generated.
    # The parameter is ``real_name``, not ``name``: ``op()`` reserves
    # ``name`` as the request-label meta-parameter and would silently
    # swallow it. The key is generated *without a passphrase*
    # (``%no-protection``) -- passphrase-protected generation is
    # deliberately out of scope, since no secret may ride in a plan or
    # log. Generation acts on the *executing user's* default ``GNUPGHOME``
    # (flag the request ``sudo=True`` for root's keychain) and needs
    # entropy; on a slow virtual host the apply step can take a while.
    # ``**kwargs`` absorbs any further global arguments (``op()`` already
    # strips ``name``/``sudo``/``su``).
    if not real_name:
        raise ValueError("gpg.gen_key requires a non-empty real_name")
    if not email:
        raise ValueError("gpg.gen_key requires a non-empty email")

    uid = f"{real_name} <{email}>"
    keys = await ctx.facts.get(GpgKeys)
    if any(key.get("uid") == uid for key in keys):
        ctx.reason = f"GPG key for {uid} already exists"
        return

    ctx.changed = True
    ctx.reason = f"generate GPG key for {uid}"
    yield GpgGenKeyCommand(
        real_name=real_name, email=email, algo=algo, length=length, expire=expire
    )


# --- gpg.trust -------------------------------------------------------------------

#: Trust level names ``gpg.trust`` accepts, mapped to the numeric value it
#: writes through ``--import-ownertrust`` -- the import-side inverse of
#: ``_TRUST_LEVELS``. ``ultimate`` writes ``6`` (what a fresh own key
#: exports on gpg 2.4.7); the idempotence check compares *level names*, so
#: an existing ``5`` also satisfies an ``ultimate`` request.
_TRUST_VALUES = {
    "unknown": 1,
    "never": 2,
    "marginal": 3,
    "full": 4,
    "ultimate": 6,
}


@dataclass(frozen=True)
class GpgTrustCommand(Command):
    """``gpg --import-ownertrust`` one ``FPR:N:`` assignment.

    The whole of ``gpg.trust``: ``printf '%s:%d:\\n' <fingerprint> <value> |
    gpg --batch --import-ownertrust`` upserts one ownertrust assignment in
    the executing user's trust database (the same ``FPR:N:`` line format
    ``GpgTrust`` exports). Wrapped in ``sh -c`` because the pipeline's gpg
    must run at the same privilege as a ``sudo``-flagged request's printf.
    The fingerprint is shell-quoted.
    """

    fingerprint: str
    ownertrust: int

    def shell(self) -> str:
        fmt = shlex.quote("%s:%d:\\n")
        inner = (
            f"printf {fmt} {shlex.quote(self.fingerprint)} {self.ownertrust}"
            " | gpg --batch --import-ownertrust"
        )
        return f"sh -c {shlex.quote(inner)}"


async def trust(
    ctx: OpContext, fingerprint: str, level: str, **kwargs: Any
) -> AsyncGenerator[Command]:
    """Set one key's ownertrust in the executing user's trust database.

    Args:
        ctx (OpContext): Execution context for the operation.
        fingerprint (str): The full fingerprint of the key whose
            ownertrust is set.
        level (str): The trust level: ``unknown``, ``never``, ``marginal``,
            ``full`` or ``ultimate``.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated gpg ownertrust commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/gpg/gpg_trust.py``.
    """

    # A reemote original -- the write side of
    # the ``GpgTrust`` fact.
    #
    # Idempotent against ``GpgTrust`` on the *level name*: an existing
    # assignment whose ``level`` equals the request reports no change, so
    # both raw values 5 and 6 satisfy an ``ultimate`` request without
    # rewriting the trustdb. The write itself always sends 6 for ultimate
    # (``_TRUST_VALUES``). An unknown level raises ``ValueError`` before
    # any fact is read. Ownertrust is the executing user's trust database;
    # flag the request ``sudo=True`` for root's. ``**kwargs`` absorbs
    # any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    if not fingerprint:
        raise ValueError("gpg.trust requires a non-empty fingerprint")
    if level not in _TRUST_VALUES:
        raise ValueError(
            f"Invalid trust level {level!r}; expected one of "
            f"{', '.join(_TRUST_VALUES)}"
        )

    entries = await ctx.facts.get(GpgTrust)
    if any(
        entry["fingerprint"] == fingerprint and entry["level"] == level
        for entry in entries
    ):
        ctx.reason = f"GPG trust for {fingerprint} already {level}"
        return

    ctx.changed = True
    ctx.reason = f"set GPG trust for {fingerprint} to {level}"
    yield GpgTrustCommand(fingerprint=fingerprint, ownertrust=_TRUST_VALUES[level])


# --- gpg.refresh_keys --------------------------------------------------------------


@dataclass(frozen=True)
class GpgRefreshKeysCommand(Command):
    """``gpg --refresh-keys`` from the configured keyservers.

    The whole of ``gpg.refresh_keys``: ``gpg --batch --refresh-keys`` with
    the named key ids, or every key in the executing user's keychain when
    the tuple is empty. A network operation -- gpg contacts the configured
    keyservers for updated key material (new uids, revocations, extended
    expiry). A single gpg invocation, so a bare ``sudo`` prefix elevates
    all of it. The key ids are shell-quoted.
    """

    keyids: tuple[str, ...] = ()

    def shell(self) -> str:
        shell = "gpg --batch --refresh-keys"
        if self.keyids:
            joined = " ".join(shlex.quote(kid) for kid in self.keyids)
            shell = f"{shell} {joined}"
        return shell


async def refresh_keys(
    ctx: OpContext,
    keyids: str | list[str] | tuple[str, ...] | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Refresh GPG keys from the keyservers.

    Args:
        ctx (OpContext): Execution context for the operation.
        keyids (str | list[str] | tuple[str, ...] | None, optional): The key
            id(s) or fingerprint(s) to refresh (``0x`` prefixes accepted).
            Prefer tuples when building requests via ``op()``, since
            ``OperationRequest`` kwargs must stay hashable. None refreshes
            every key in the keychain. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The refresh command, always (the operation is stateless).

    **Raises:**

    ```python
    None
    ```

    Examples:
        Examples can be found in ``examples/modules/gpg/gpg_refresh_keys.py``.
    """

    # A reemote original -- the keyserver
    # touchpoint ``gpg.key`` deliberately dropped from its install.
    #
    # Stateless/non-idempotent: whether a keyserver holds an update is
    # unknowable without contacting it, so the refresh always runs and
    # always reports a change. This is a *network* operation -- it can be
    # slow or fail when the keyservers are unreachable (a gpg failure
    # surfaces as ``CommandError`` at apply time). The refresh acts on the
    # *executing user's* default ``GNUPGHOME``; flag the request
    # ``sudo=True`` for root's keychain. ``**kwargs`` absorbs any
    # global arguments (``op()`` already strips ``name``/``sudo``/``su``).
    ids = [keyids] if isinstance(keyids, str) else list(keyids) if keyids else []
    ctx.changed = True
    ctx.reason = f"refresh GPG keys {ids}" if ids else "refresh all GPG keys"
    yield GpgRefreshKeysCommand(keyids=tuple(ids))


__all__ = [
    "GpgDearmorCommand",
    "GpgDeleteKeyCommand",
    "GpgExportCommand",
    "GpgGenKeyCommand",
    "GpgImportCommand",
    "GpgImportUploadCommand",
    "GpgKey",
    "GpgKeyDeleteKeysCommand",
    "GpgKeyDetails",
    "GpgKeyEmptyCleanupCommand",
    "GpgKeyInfo",
    "GpgKeyInstallCommand",
    "GpgKeyMetadata",
    "GpgKeyRemoveCommand",
    "GpgKeyTrust",
    "GpgKeyUploadCommand",
    "GpgKeyring",
    "GpgKeyrings",
    "GpgKeys",
    "GpgRefreshKeysCommand",
    "GpgSecretKeys",
    "GpgTrust",
    "GpgTrustCommand",
    "GpgVerification",
    "GpgVerify",
    "dearmor",
    "delete_key",
    "export_key",
    "gen_key",
    "import_key",
    "key",
    "refresh_keys",
    "trust",
]