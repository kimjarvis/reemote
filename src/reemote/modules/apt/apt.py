# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The ``apt`` module: facts and operations in one source.
#
# Fact side: ``AptAutoremovable``, the no-longer-needed packages an
# autoremove would clean up, parsed from the ``Remv`` rows of an
# ``apt-get --just-print autoremove`` simulation, ``AptHeldPackages``, the
# package names marked as held via ``apt-mark hold``, parsed from
# ``apt-mark showhold``, and ``AptPackages``, the installed packages with
# their versions, parsed from ``apt list --installed`` -- all three into
# plain ``AptPackageInfo`` TypedDict-shaped dicts (name and versions),
# ``AptKeys``, the
# GPG keys available to APT, gathered from the modern keyring directories
# with ``gpg --with-colons`` instead of the deprecated ``apt-key`` command
# into plain ``AptKey`` TypedDict-shaped dicts,
# ``AptPolicy``, the install candidate and
# repository origins of one package, parsed from ``apt-cache policy``,
# ``AptSources``, the configured repositories, parsed from the legacy
# ``.list`` files and the modern deb822 ``.sources`` files into plain
# ``AptSource`` TypedDict-shaped dicts, ``AptSourcesFile``, the raw content
# of a single ``.sources`` file (read by ``apt.sources_file``), and
# ``AptUpgradable``, the packages
# ``apt upgrade`` would move with both ends of each version jump, parsed
# from ``apt list --upgradable`` into plain ``AptPackageUpgrade``
# TypedDict-shaped dicts, ``DebPackage``, the name/version of a
# ``.deb`` file or an installed dpkg package that ``apt.deb`` reads to
# decide idempotence (a plain ``DebPackageInfo`` TypedDict-shaped dict),
# and ``SimulateOperationWillChange``, the proposed
# change counts of a simulated ``apt-get`` operation, parsed from the
# ``N upgraded, ...`` summary line of a ``--dry-run`` into a plain
# ``AptSimulationDict`` TypedDict-shaped dict. Like every fact
# they are phase-unaware: their ``process`` coroutines just gather the raw
# value.
#
# Operation side: ``apt.deb`` plus the typed command descriptors it yields
# (``DebInstallCommand``/``AptInstallFixCommand``/``AptRemoveCommand``),
# ``apt.dist_upgrade`` (``AptDistUpgradeCommand``), ``apt.auto_remove``
# (``AptAutoRemoveCommand``), ``apt.key``
# (``AptKeyUploadCommand``/``AptKeyInstallCommand``), ``apt.mark``
# (``AptMarkHoldCommand``/``AptMarkUnholdCommand``), ``apt.packages``
# (``AptUpdateCommand``/``AptUpgradeCommand``/``AptInstallCommand`` plus the
# shared ``AptRemoveCommand``), ``apt.ppa`` (``AptPpaCommand``),
# ``apt.repo`` (``AptRepoUploadCommand``/``AptRepoInstallCommand``) and
# ``apt.sources_file``
# (``AptSourcesFileUploadCommand``/``AptSourcesFileInstallCommand``/``AptSourcesFileRemoveCommand``).
# ``apt.deb`` mirrors the baseline's ``apt.deb`` (``src``, ``present``,
# ``wait_for_lock``) for a ``.deb`` file already on the target host;
# The baseline's URL source -- which downloads the file mid-deploy -- is dropped,
# since ``apt.deb`` installs a *binary* package file that must already be on the
# host and reemote's URL download (``files.download``) is text-only, so a URL
# ``src`` raises ``ValueError``, and the baseline's ``force`` option (the
# deprecated ``--force-yes`` apt flag) is deliberately dropped. ``apt.dist_upgrade``
# mirrors the baseline's ``apt.dist_upgrade`` (simulate ``dist-upgrade``, skip
# when nothing would change), with the baseline's ``auto_remove`` parameter
# split out into the standalone ``apt.auto_remove`` operation -- a reemote
# original that cleans up orphaned dependencies without an upgrade,
# idempotent on ``AptAutoremovable``. ``apt.key`` mirrors the install half
# of the baseline's ``apt.key``: a GPG key stream is spooled over scp and
# installed as a keyring file for ``signed-by=`` references, dearmored on
# the target when the content is ASCII-armored; it is deliberately
# non-idempotent (always installs, like ``dnf.key``), and the baseline's
# ``keyserver``/``keyid`` fetch and ``present=False`` removal are dropped.
# ``apt.mark`` is a reemote original -- the baseline ships no operation managing
# package hold states -- holding packages back from upgrades (or releasing
# held ones) with ``apt-mark hold``/``unhold``, idempotent on
# ``AptHeldPackages``. ``apt.packages`` mirrors the baseline's ``apt.packages`` (install/remove/update
# packages with version pins, ``latest`` upgrades and ``update``/``upgrade``
# prefixes), reading ``AptPackages`` rather than the baseline's ``DebPackages``
# (the same substitution ``apt.deb`` made); the baseline's ``cache_time``
# stamp-file caching and ``force`` (the deprecated ``--force-yes`` flag) are
# deliberately dropped. ``apt.ppa`` mirrors the baseline's ``apt.ppa`` (add/remove
# an Ubuntu PPA with ``apt-add-repository``), idempotent on ``AptSources`` --
# it resolves the ``ppa:user/repo`` name to its Launchpad archive and reports a
# change only when the entry is actually added or removed, unlike the baseline,
# which reads no fact and always re-runs the command. ``apt.repo`` mirrors the baseline's ``apt.repo`` (add/remove one source
# line in ``/etc/apt/sources.list`` or a ``sources.list.d`` file), idempotent
# on ``AptSources`` and on the managed file's own text; the baseline's delegation
# to ``files.line`` is replaced by a pure content transform installed through
# the spool-and-install pair -- reemote has no line operation and
# namespaces never reuse each other's operations.
# ``apt.sources_file`` mirrors the baseline's ``apt.sources_file`` (manage a
# deb822 ``.sources`` file under ``sources.list.d``), idempotent on
# ``AptSources`` for the write direction: the generated content is spooled
# over scp and installed with an elevated shell (the ``apt.key`` pattern
# replacing the baseline's ``files.put`` delegation, since transfers are
# unprivileged); the ``present=False`` removal is idempotent on the
# in-namespace ``AptSourcesFile`` content fact (the ``DnfRepoFile``
# pattern), so an absent file is a no-op. Operations are async
# generators that receive an ``OpContext``, await facts, set
# ``ctx.changed``/``ctx.reason``, and yield command descriptors; they never
# touch the host directly.
# """

import hashlib
import io
import re
import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import IO, Any, NotRequired, TypedDict
from urllib.parse import urlparse

from ...commands import Command, TransferCommand
from ...core import OpContext
from ...hosts import Host

# --- Shared helpers ------------------------------------------------------------


def _apt_lock_timeout(wait_for_lock: bool | int) -> str:
    """The ``DPkg::Lock::Timeout`` apt option for a ``wait_for_lock`` value.

    ``True`` maps to a 60-second default, an int sets the timeout in seconds
    (the baseline's ``apt_lock_timeout``).
    """
    timeout = 60 if wait_for_lock is True else int(wait_for_lock)
    return f"-o DPkg::Lock::Timeout={timeout}"


def _noninteractive_apt(command: str, wait_for_lock: bool | int = False) -> str:
    """The non-interactive ``apt-get`` prefix plus ``command``.

    Mirrors the baseline's ``noninteractive_apt``: ``DEBIAN_FRONTEND=noninteractive
    apt-get -y``, an optional lock timeout, the two ``--force-confdef`` /
    ``--force-confold`` dpkg options (keep existing config files on upgrade),
    then the subcommand. Shared by ``SimulateOperationWillChange``'s dry-run
    command and the operation descriptors below. The baseline's ``force``
    (``--force-yes``) is deliberately dropped -- the flag is deprecated in
    modern apt.
    """
    args = ["DEBIAN_FRONTEND=noninteractive apt-get -y"]
    if wait_for_lock:
        args.append(_apt_lock_timeout(wait_for_lock))
    args.extend(
        (
            '-o Dpkg::Options::="--force-confdef"',
            '-o Dpkg::Options::="--force-confold"',
            command,
        )
    )
    return " ".join(args)


# --- Facts -------------------------------------------------------------------


class AptPackageInfo(TypedDict):
    """
    Attributes:
        name: The package name, architecture suffixes (``libc6:i386``)
            intact, exactly as the underlying apt command prints it.
        versions: The package's versions, de-duplicated and sorted,
            epoch prefixes (``5:24.0.7-1~ubuntu.22.04~jammy``) intact:
            every installed version for ``AptPackages``, the single
            bracketed ``Remv`` version for ``AptAutoremovable``, and
            ``[]`` for ``AptHeldPackages`` (``apt-mark showhold`` prints
            no version).
    """

    # The shared package entry of ``AptPackages``, ``AptAutoremovable``
    # and ``AptHeldPackages`` -- one shape for every apt package-list
    # question.
    #
    # A TypedDict rather than a class instance, like ``AptSource``: entries
    # are ordinary dicts, so equality against a literal dict works and
    # ``json.dumps`` serializes them directly. Groups every version under
    # one name, where apk's ``ApkPackageVersion`` entries carry one
    # name-and-version pair each. The ``Info`` suffix follows the
    # ``ApkPackageInfo`` / ``DebPackageInfo`` precedent.

    name: str
    versions: list[str]


class AptAutoremovable:
    """Packages ``apt autoremove`` would remove, as ``AptPackageInfo`` dicts.

    **Commands:**

    ```bash
    apt-get --just-print autoremove 2>/dev/null
    ```

    **Returns:**

    ```python
    list[AptPackageInfo]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_apt_autoremovable.py``.
    """

    # A reemote original: the baseline ships no ``apt.AptAutoremovable``
    # counterpart (its ``apt.SimulateOperationWillChange`` only counts a
    # simulation's changes, never names them).
    # ``apt-get --just-print autoremove`` simulates the autoremove --
    # nothing is ever removed -- and prints one machine-readable
    # ``Remv <name> [<version>]`` row per package that would go, parsed
    # into ``AptPackageInfo`` entries de-duplicated by name and
    # sorted, for example::
    #
    #     [
    #         {"name": "libllvm17t64", "versions": ["1:17.0.6-9ubuntu1"]},
    #         {"name": "linux-headers-6.8.0-45", "versions": ["6.8.0-45.45"]},
    #     ]
    #
    # Only the ``Remv`` rows are read: the progress lines, the indented
    # names under ``The following packages will be REMOVED:`` and the
    # ``0 upgraded, ...`` count line are never mistaken for package names,
    # and each row's bracketed version is kept as the entry's
    # single-element ``versions`` list (the shared ``AptPackageInfo``
    # shape ``AptPackages`` and ``AptHeldPackages`` also return).
    # ``apt-mark showauto`` is
    # deliberately not used: it lists every auto-installed package,
    # including ones something still requires, while the simulation
    # reports exactly the no-longer-needed set. Architecture-suffixed
    # names like ``libc6:i386`` keep their suffix, exactly as apt prints
    # them. Nothing autoremovable (or no output at all) reads as ``[]``.
    # This fact fills a coverage gap of ``AptPackages``: the ``apt list
    # --installed`` rows that fact parses cannot tell a legacy dependency
    # from one still in use, so a deploy cleaning up after itself reads
    # the removable names here -- and only collects the list; removal
    # stays an explicit operator decision, never a fact side effect -- the
    # ``apt.auto_remove`` operation performs that cleanup. The
    # simulation needs no root, and a missing ``apt-get`` exits 127, which
    # ``run_shell`` reports as ``CommandNotFoundError``.

    #: Shell simulating the autoremove over SSH, one ``Remv`` row per
    #: removable package. ``--just-print`` never removes anything (the
    #: fact only collects the list), and ``2>/dev/null`` suppresses the
    #: lock/permission warnings an unprivileged simulation writes to
    #: stderr (like ``AptPackages``' redirect).
    COMMAND = "apt-get --just-print autoremove 2>/dev/null"

    #: A simulation removal row: the ``Remv`` token followed by the
    #: package name (an optional ``:arch`` suffix included) and its
    #: bracketed version, both kept in the entry.
    REMV_REGEX = re.compile(r"^Remv\s+(\S+)\s+\[([^\]]+)\]")

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[AptPackageInfo]:
        """Parse autoremove-simulation rows into sorted, unique entries.

        Only lines starting with the ``Remv`` token contribute their
        package name and bracketed version (wrapped as the single-element
        ``versions`` list); every other line -- progress
        output, the indented removal summary, the count line, blank lines
        -- is skipped. A repeated name keeps its last row and the entries
        are sorted by name. Pure and testable.
        """
        removable: dict[str, AptPackageInfo] = {}
        for line in output:
            matches = cls.REMV_REGEX.match(line)
            if matches:
                removable[matches.group(1)] = {
                    "name": matches.group(1),
                    "versions": [matches.group(2)],
                }
        return [removable[name] for name in sorted(removable)]

    @classmethod
    async def process(cls, host: Host) -> list[AptPackageInfo]:
        apt_autoremovable = getattr(host, "apt_autoremovable", None)
        if apt_autoremovable is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # each entry dict (and its versions list) so callers never
            # mutate simulated state.
            return [
                {"name": entry["name"], "versions": list(entry["versions"])}
                for entry in apt_autoremovable
            ]
        # Real transport: simulate the autoremove over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class AptHeldPackages:
    """Packages marked as held, as a sorted list of ``AptPackageInfo`` dicts.

    **Commands:**

    ```bash
    apt-mark showhold
    ```

    **Returns:**

    ```python
    list[AptPackageInfo]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_apt_held_packages.py``.
    """

    # A reemote original: the baseline ships no ``apt.AptHeldPackages``
    # counterpart. ``apt-mark showhold`` prints one held package name per
    # line (the names marked with ``apt-mark hold``, which keeps them out
    # of upgrades); blank lines are skipped and the names are
    # de-duplicated and sorted so reads stay deterministic and
    # JSON-printable, for example::
    #
    #     [
    #         {"name": "docker-ce", "versions": []},
    #         {"name": "libc6:i386", "versions": []},
    #         {"name": "linux-image-generic", "versions": []},
    #     ]
    #
    # Every entry carries an empty ``versions`` list: ``apt-mark
    # showhold`` prints no version, so the shared ``AptPackageInfo``
    # shape's version information is simply absent here (unlike
    # ``ApkHeldPackages``, whose entries keep the world constraint as
    # the version -- the constraint IS the hold state).
    # Architecture-suffixed names like ``libc6:i386`` keep their suffix,
    # exactly as ``apt-mark`` prints them. No held packages (or no output
    # at all) read as ``[]``. This fact fills a coverage gap of
    # ``AptPackages``: the ``apt list --installed`` rows that fact parses
    # report versions but never the hold state, so a deploy that must not
    # upgrade critical packages reads the held names here -- and only
    # collects the list; marking stays an explicit operator decision, never
    # a fact side effect -- the ``apt.mark`` operation sets or clears those
    # marks. A missing ``apt-mark`` exits 127, which ``run_shell`` reports as
    # ``CommandNotFoundError``.

    #: Shell listing the held package names over SSH, one name per line.
    COMMAND = "apt-mark showhold"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[AptPackageInfo]:
        """Parse ``apt-mark showhold`` lines into sorted, unique entries.

        Each non-blank line is one package name, stripped of surrounding
        whitespace; duplicates collapse into one entry and the result is
        sorted. Every entry carries an empty ``versions`` list --
        ``showhold`` prints no version. Pure and testable.
        """
        names: set[str] = set()
        for line in output:
            name = line.strip()
            if name:
                names.add(name)
        return [{"name": name, "versions": []} for name in sorted(names)]

    @classmethod
    async def process(cls, host: Host) -> list[AptPackageInfo]:
        apt_held_packages = getattr(host, "apt_held_packages", None)
        if apt_held_packages is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # each entry dict (and its versions list) so callers never
            # mutate simulated state.
            return [
                {"name": entry["name"], "versions": list(entry["versions"])}
                for entry in apt_held_packages
            ]
        # Real transport: list the held package names over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class AptKey(TypedDict):
    """
    Attributes:
        id: The key id (the last 16 hex digits of the fingerprint), the
            identity a keyring file lists the key under.
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
        subkeys: The key's subkeys as further ``AptKey`` entries (only
            the last ``sub`` record before the next ``pub`` is kept);
            present on primary keys, absent on subkey entries.
    """

    # One GPG key (``AptKeys``' entry), as a plain dict.
    #
    # A TypedDict rather than a class instance, like ``AptSource``: entries
    # are ordinary dicts, so equality against a literal dict works and
    # ``json.dumps`` serializes them directly. The optional fields are
    # NotRequired because a colon record stream may carry no ``fpr``/``uid``
    # line for a key, and subkey entries never carry ``subkeys``.

    id: str
    validity: str
    length: int
    fingerprint: NotRequired[str]
    uid_hash: NotRequired[str]
    uid: NotRequired[str]
    subkeys: NotRequired[list["AptKey"]]


def _parse_key_records(output: Iterable[str]) -> list[AptKey]:
    """Parse one keyring's ``gpg --with-colons`` records into key entries.

    The colon-record parser behind the baseline's ``GpgFactBase.process`` (see
    GnuPG's doc/DETAILS for the field values): a ``pub`` record starts a new
    key (``validity``, ``length`` as an int, ``id``), a ``sub`` record starts
    its subkey, and ``fpr``/``uid`` records attach the ``fingerprint``,
    ``uid_hash`` and ``uid`` to whichever of the two is current. Only the
    last ``sub`` record before the next ``pub`` is kept, exactly like
    the baseline. Blank lines and other record types (``tru``, ...) are skipped,
    and records before the first ``pub`` are ignored. Each key keeps its
    ``id`` and its subkeys stay a list of their own ``AptKey`` entries.
    """
    keys: list[AptKey] = []
    current_key: AptKey | None = None
    current_subkey: AptKey | None = None

    for line in output:
        if not line:
            continue

        bits = line.split(":")

        if bits[0] in ("pub", "sub"):
            key_details: AptKey = {
                "validity": bits[1],
                "length": int(bits[2]),
                "id": bits[4],
            }

            if bits[0] == "pub":
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


class AptKeys:
    r"""The GPG keys available to APT, as a list of ``AptKey`` dicts.

    **Commands:**

    ```bash
    find "/etc/apt/trusted.gpg.d" "/etc/apt/keyrings" "/usr/share/keyrings" -type f \( -name '*.gpg' -o -name '*.kbx' \) 2>/dev/null -exec sh -c 'echo "KEYRING:$1"; gpg --list-keys --with-colons --keyring "$1" --no-default-keyring 2>/dev/null || true' _ {} \; ; find "/etc/apt/trusted.gpg.d" "/etc/apt/keyrings" "/usr/share/keyrings" -type f -name '*.asc' 2>/dev/null -exec sh -c 'echo "KEYRING:$1"; gpg --with-colons "$1" 2>/dev/null || true' _ {} \;
    ```

    **Returns:**

    ```python
    list[AptKey]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_apt_keys.py``.
    """

    # Mirrors the baseline's ``apt.AptKeys``: two ``find`` sweeps list every
    # ``.gpg``/``.kbx`` binary keyring and every ``.asc`` armored keyring under
    # ``directories`` (defaulting to ``/etc/apt/trusted.gpg.d``,
    # ``/etc/apt/keyrings`` and ``/usr/share/keyrings``), each is read with
    # ``gpg --with-colons`` behind a ``KEYRING:`` marker line, and the parsed
    # keys are flattened into one list of ``AptKey`` entries, for example::
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
    # ``directories`` is a list of paths, a single path string or None (the
    # defaults). If the same key id appears in several keyring files, the last
    # one wins -- the baseline's flattening order -- and entries keep the order
    # their ids first appeared in. The baseline keys its answer by key id (with
    # subkeys re-keyed by their own ids); reemote returns the list of typed
    # entries instead, each carrying its ``id`` and its subkeys as a list.
    # The baseline builds this fact on its
    # ``gpg.GpgKeyrings`` infrastructure; reemote collapses that stack into
    # this one class, so the per-keyring paths and formats the parent reports
    # are deliberately dropped (``AptKeys`` discards them anyway) and only the
    # flattened keys remain. reemote's own ``gpg.GpgKeyrings`` in the gpg
    # namespace is the general-purpose keyring inspection fact that keeps
    # those per-keyring paths and formats. The baseline's ``requires_command``
    # gating on ``gpg`` is deliberately dropped too: a missing ``gpg`` is
    # swallowed by the command's ``|| true`` and reads as ``[]``, while a
    # missing ``find`` exits 127, which ``run_shell`` reports as
    # ``CommandNotFoundError``.

    #: APT's modern keyring directories, searched when ``directories`` is
    #: None (the baseline's ``AptKeys.command`` defaults).
    DIRECTORIES = (
        "/etc/apt/trusted.gpg.d",
        "/etc/apt/keyrings",
        "/usr/share/keyrings",
    )

    #: Marker prefixing each keyring's ``gpg`` output, naming the keyring
    #: file (the baseline's ``GpgKeyrings`` ``KEYRING:`` echo).
    KEYRING_MARKER = "KEYRING:"

    @classmethod
    def command(cls, directories: str | list[str] | None = None) -> str:
        """Shell listing every keyring under ``directories`` with ``gpg``.

        Two separate ``find``+``-exec`` calls avoid bash-specific syntax:
        binary keyrings (``.gpg``, ``.kbx``) are read with ``--keyring`` /
        ``--no-default-keyring``, armored keyrings (``.asc``) are passed
        directly to ``gpg``. Each file's output is prefixed with a
        ``KEYRING:`` marker line; a file ``gpg`` cannot read contributes its
        marker only (the ``|| true`` swallows the failure). None selects
        ``DIRECTORIES``; a single string is treated as one directory.
        """
        if directories is None:
            directories = list(cls.DIRECTORIES)
        elif isinstance(directories, str):
            directories = [directories]

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
    def parse(cls, output: Iterable[str]) -> list[AptKey]:
        """Parse marker-grouped ``gpg --with-colons`` output into flat keys.

        Each ``KEYRING:`` line starts a new keyring group whose colon
        records are parsed by ``_parse_key_records``; the groups' keys are
        merged by id where a later keyring's key wins over an earlier one,
        and the entries keep the order their ids first appeared in. Lines
        before the first marker are ignored, and a keyring with a marker
        but no records (an unreadable file) contributes nothing. Pure and
        testable.
        """
        keys: dict[str, AptKey] = {}
        current_keyring: str | None = None
        current_output: list[str] = []

        for raw in output:
            line = raw.strip()
            if not line:
                continue

            if line.startswith(cls.KEYRING_MARKER):
                if current_keyring and current_output:
                    keys.update(
                        {key["id"]: key for key in _parse_key_records(current_output)}
                    )
                current_keyring = line[len(cls.KEYRING_MARKER) :]
                current_output = []
            else:
                current_output.append(line)

        if current_keyring and current_output:
            keys.update({key["id"]: key for key in _parse_key_records(current_output)})

        return list(keys.values())

    @classmethod
    async def process(
        cls, host: Host, directories: str | list[str] | None = None
    ) -> list[AptKey]:
        apt_keys = getattr(host, "apt_keys", None)
        if apt_keys is not None:
            # SimulatedHost seam: read the in-memory state directly. The seam
            # holds the already-flattened answer, so ``directories`` does not
            # filter it. Copy each entry dict (and its subkey dicts) so
            # callers never mutate the simulated state.
            copies: list[AptKey] = []
            for key in apt_keys:
                copy: AptKey = {**key}
                if "subkeys" in key:
                    copy["subkeys"] = [{**sub} for sub in key["subkeys"]]
                copies.append(copy)
            return copies
        # Real transport: sweep the keyring directories over asyncssh.
        stdout = await host.run_shell(cls.command(directories))
        return cls.parse(stdout.splitlines())


class AptPackages:
    """Installed apt packages, as a list of ``AptPackageInfo`` dicts.

    **Commands:**

    ```bash
    apt list --installed 2>/dev/null
    ```

    **Returns:**

    ```python
    list[AptPackageInfo]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_apt_packages.py``.
    """

    # Mirrors the baseline's ``apt.AptPackages``: ``apt list --installed`` rows
    # (``name/source-info version arch [installed,...]``) are matched with
    # The baseline's package regex, so architecture-suffixed names like
    # ``libc6:i386`` keep their suffix and the ``Listing...`` header and any
    # other noise lines are ignored, for example::
    #
    #     [
    #         {"name": "adduser", "versions": ["3.118ubuntu5"]},
    #         {"name": "docker-ce", "versions": ["5:24.0.7-1~ubuntu.22.04~jammy"]},
    #         {"name": "libc6:i386", "versions": ["2.35-0ubuntu3.4"]},
    #     ]
    #
    # Entries are sorted by name with de-duplicated, sorted version lists:
    # The baseline's ``parse_packages`` collects versions into a set per name
    # and keys its answer by name, while reemote returns the typed entry
    # list (the same deliberate divergence ``ApkInstalledPackages`` made)
    # so reads stay deterministic and JSON-printable; the main reason to
    # reach for ``AptPackages`` is packages whose presence is
    # more reliably visible from ``apt`` than from ``dpkg`` (for example
    # architecture-suffixed names). The baseline's
    # ``requires_command`` gating on ``apt`` is deliberately dropped: a
    # missing ``apt`` exits 127, which ``run_shell`` reports as
    # ``CommandNotFoundError``. Hold state is not visible in these rows:
    # ``AptHeldPackages`` reports the names marked with ``apt-mark hold``.
    # Candidate versions and repository origins are not visible either:
    # ``AptPolicy`` reports which version ``apt install`` would pick and
    # which repositories offer it. Which packages an upgrade would move
    # is not visible either: ``AptUpgradable`` reports every pending
    # version jump, and ``AptAutoremovable`` the no-longer-needed packages
    # an autoremove would clean up.

    #: Shell listing installed packages over SSH. The ``2>/dev/null``
    #: suppresses the "Listing..." progress notice modern apt writes to
    #: stderr (the baseline's ``AptPackages.command``).
    COMMAND = "apt list --installed 2>/dev/null"

    #: The baseline's package-line regex: ``name/source-info version arch
    #: [installed...`` -- the leading anchor and the ``[installed`` tail
    #: filter out the optional "Listing..." header and any other noise.
    #: The name keeps an optional ``:arch`` suffix; the version allows
    #: epoch colons, tildes, dots, dashes and plus signs.
    PACKAGE_REGEX = (
        r"^([a-zA-Z0-9\+\-\.]+(?::[a-zA-Z0-9]+)?)/\S+\s+"
        r"([a-zA-Z0-9:~\.\-\+]+)\s+\S+\s+\[installed"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[AptPackageInfo]:
        """Parse ``apt list --installed`` rows into sorted package entries.

        Each matching line contributes its version to the name's set
        (the baseline's ``parse_packages`` de-dup); non-matching lines -- the
        ``Listing...`` header, blank lines, rows not marked ``[installed``
        -- are skipped. The entries are sorted by name with sorted version
        lists. Pure and testable.
        """
        packages: dict[str, set[str]] = {}
        for line in output:
            matches = re.match(cls.PACKAGE_REGEX, line)
            if matches:
                packages.setdefault(matches.group(1), set()).add(matches.group(2))
        return [
            {"name": name, "versions": sorted(versions)}
            for name, versions in sorted(packages.items())
        ]

    @classmethod
    async def process(cls, host: Host) -> list[AptPackageInfo]:
        apt_packages = getattr(host, "apt_packages", None)
        if apt_packages is not None:
            # SimulatedHost seam: the state store stays the name -> versions
            # dict the mutators edit; project it into the sorted entry list.
            # Copy the version lists so callers never mutate simulated state.
            return [
                {"name": name, "versions": list(versions)}
                for name, versions in sorted(apt_packages.items())
            ]
        # Real transport: list installed packages over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class AptPolicyVersion(TypedDict):
    """
    Attributes:
        version: The version string, epoch prefixes (``5:24.0.7-1``)
            intact, exactly as ``apt-cache policy`` prints it.
        priority: The pin priority apt assigns the version (500 for a
            normal repository, 100 for the installed ``dpkg`` status
            entry; negative for ``Pin-Priority: -1`` blocks).
        origins: The origin lines listed under the version, each with
            its leading priority stripped: a repository line like
            ``"http://archive.ubuntu.com/ubuntu noble/main amd64 Packages"``
            or the local ``"/var/lib/dpkg/status"`` for the installed copy.
    """

    # One version-table row of ``AptPolicy``'s answer, as a plain dict.

    version: str
    priority: int
    origins: list[str]


class AptPackagePolicy(TypedDict):
    """
    Attributes:
        installed: The installed version, or None when apt prints
            ``(none)`` (the package is known but not installed).
        candidate: The version ``apt install <package>`` would pick right
            now, or None when apt prints ``(none)`` (no installable
            version, e.g. every offering pinned below zero).
        versions: The version table as ``AptPolicyVersion`` dicts, in the
            order apt prints them (highest-priority offering first).
    """

    # One package's ``apt-cache policy`` answer (``AptPolicy``'s result).
    #
    # A TypedDict rather than a class instance, like ``AptSource``: entries
    # are ordinary dicts, so equality against a literal dict works and
    # ``json.dumps`` serializes them directly.

    installed: str | None
    candidate: str | None
    versions: list[AptPolicyVersion]


class AptPolicy:
    """One package's install candidate and origins, as an ``AptPackagePolicy``.

    **Commands:**

    ```bash
    LC_ALL=C apt-cache policy <package>
    ```

    **Returns:**

    ```python
    AptPackagePolicy | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_apt_policy.py``.
    """

    # A reemote original: the baseline ships no ``apt.AptPolicy`` counterpart.
    # ``apt-cache policy <package>`` prints the installed version, the
    # candidate ``apt install`` would pick, and a version table listing
    # every offering with its pin priority and repository origins, parsed
    # into a plain dict, for example::
    #
    #     {
    #         "installed": "1.24.0-2ubuntu7.1",
    #         "candidate": "1.24.0-2ubuntu7.3",
    #         "versions": [
    #             {
    #                 "version": "1.24.0-2ubuntu7.3",
    #                 "priority": 500,
    #                 "origins": [
    #                     "http://archive.ubuntu.com/ubuntu noble-updates/main amd64 Packages",
    #                 ],
    #             },
    #             {
    #                 "version": "1.24.0-2ubuntu7.1",
    #                 "priority": 500,
    #                 "origins": [
    #                     "http://security.ubuntu.com/ubuntu noble-security/main amd64 Packages",
    #                     "/var/lib/dpkg/status",
    #                 ],
    #             },
    #         ],
    #     }
    #
    # This fact fills a coverage gap of ``AptPackages``: the ``apt list
    # --installed`` rows that fact parses report only installed versions,
    # never which version an install would pick or which repository offers
    # it, so a deploy validating that a specific version is available
    # before rolling it out reads the candidate and origins here. A
    # ``(none)`` installed or candidate reads as None; the ``***`` marker
    # on the installed version-table row is ignored (``installed`` already
    # conveys it) and rows keep apt's printed order. An unknown package
    # prints nothing on stdout (apt's "Unable to locate package" notice
    # goes to stderr), which reads as None. The command is prefixed with
    # ``LC_ALL=C`` (like ``SimulateOperationWillChange``) so the ``Installed:`` /
    # ``Candidate:`` labels stay untranslated on localized hosts. A missing
    # ``apt-cache`` exits 127, which ``run_shell`` reports as
    # ``CommandNotFoundError``.

    #: A version-table row: an optional ``***`` installed marker, the
    #: version, then its pin priority (which apt pinning can make
    #: negative).
    VERSION_REGEX = re.compile(r"^(?:\*\*\* )?(\S+) (-?\d+)$")

    #: An origin line under a version-table row: the origin's own
    #: priority followed by the repository description (or the local
    #: ``/var/lib/dpkg/status`` path).
    ORIGIN_REGEX = re.compile(r"^(-?\d+) (\S.*)$")

    @classmethod
    def command(cls, package: str) -> str:
        """Shell printing ``package``'s policy over SSH.

        The package name is ``shlex``-quoted, and ``LC_ALL=C`` keeps apt's
        field labels in English on localized hosts.
        """
        return f"LC_ALL=C apt-cache policy {shlex.quote(package)}"

    @classmethod
    def parse(cls, output: Iterable[str]) -> AptPackagePolicy | None:
        """Parse ``apt-cache policy`` output into an ``AptPackagePolicy``.

        The ``Installed:`` and ``Candidate:`` header values map ``(none)``
        to None; after the ``Version table:`` line each version row
        starts a new entry and the origin lines below it append to that
        entry's origins (their leading priority stripped). The package
        name header and blank lines are ignored. Returns None when
        neither header line appears -- an unknown package prints nothing
        on stdout. Pure and testable.
        """
        installed: str | None = None
        candidate: str | None = None
        versions: list[AptPolicyVersion] = []
        current: AptPolicyVersion | None = None
        in_table = False
        found = False

        for raw in output:
            line = raw.strip()
            if not line:
                continue
            if not in_table:
                if line.startswith("Installed:"):
                    found = True
                    value = line[len("Installed:") :].strip()
                    installed = None if value == "(none)" else value
                elif line.startswith("Candidate:"):
                    found = True
                    value = line[len("Candidate:") :].strip()
                    candidate = None if value == "(none)" else value
                elif line == "Version table:":
                    in_table = True
                continue
            version_match = cls.VERSION_REGEX.match(line)
            if version_match:
                current = {
                    "version": version_match.group(1),
                    "priority": int(version_match.group(2)),
                    "origins": [],
                }
                versions.append(current)
                continue
            origin_match = cls.ORIGIN_REGEX.match(line)
            if origin_match and current is not None:
                current["origins"].append(origin_match.group(2))

        if not found:
            return None
        return {"installed": installed, "candidate": candidate, "versions": versions}

    @classmethod
    async def process(cls, host: Host, package: str) -> AptPackagePolicy | None:
        apt_policy = getattr(host, "apt_policy", None)
        if apt_policy is not None:
            # SimulatedHost seam: the package-keyed store holds the parsed
            # answers; a name absent from it reads as None, like an unknown
            # package. Copy the dict, each version entry and its origins
            # list so callers never mutate simulated state.
            policy = apt_policy.get(package)
            if policy is None:
                return None
            return {
                **policy,
                "versions": [
                    {**version, "origins": list(version["origins"])}
                    for version in policy["versions"]
                ],
            }
        # Real transport: query the package's policy over asyncssh.
        stdout = await host.run_shell(cls.command(package))
        return cls.parse(stdout.splitlines())


class AptSource(TypedDict):
    """
    Attributes:
        type: ``"deb"`` (binary packages) or ``"deb-src"`` (source packages).
        url: The repository URL, e.g. ``"http://archive.ubuntu.com/ubuntu"``.
        distribution: The suite/distribution, e.g. ``"noble"`` or
            ``"jammy/mongodb-org/7.0"``; ``"/"`` for a flat repository.
        components: The components, e.g. ``["main", "restricted"]``; empty
            for a flat repository.
        options: The bracketed ``[key=value]`` options (``arch``,
            ``signed-by``, ``trusted``, ...). Every value is a list of
            strings, even a single-valued one -- the baseline collapses single
            values to a bare string, forcing callers to type-check; here
            ``options["arch"]`` is always e.g. ``["amd64"]``. An inline
            armored ``Signed-By`` PGP block stays one entry holding the
            whole key text.
    """

    # One configured apt repository, as a plain dict (``AptSources``' entry).
    #
    # A TypedDict rather than a class instance: entries are ordinary dicts,
    # so there is exactly one access style (``source["url"]``), equality
    # against a literal dict works, and ``json.dumps`` serializes them
    # directly. The baseline's ``AptRepo`` frozen-dataclass/dict hybrid -- built
    # for backward compatibility with its old dict results -- is deliberately
    # dropped in favor of this shape.

    type: str
    url: str
    distribution: str
    components: list[str]
    options: dict[str, list[str]]


class AptSources:
    """The configured apt sources, as a list of ``AptSource`` dicts.

    **Commands:**

    ```bash
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do [ -e "$f" ] || continue; printf "#reemote-aptsource %s\\n" "$f"; cat "$f"; echo; done
    ```

    **Returns:**

    ```python
    list[AptSource]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_apt_sources.py``.
    """

    # Mirrors the baseline's ``apt.AptSources``: the shell cats
    # ``/etc/apt/sources.list``, every ``/etc/apt/sources.list.d/*.list``
    # (legacy one-line format) and every ``/etc/apt/sources.list.d/*.sources``
    # (modern deb822 format), each behind a marker line naming the file so
    # the parser can pick the format by extension, for example::
    #
    #     [
    #         {
    #             "type": "deb",
    #             "url": "http://archive.ubuntu.com/ubuntu",
    #             "distribution": "trusty",
    #             "components": ["restricted"],
    #             "options": {},
    #         },
    #     ]
    #
    # Legacy lines parse with the baseline's regex (options in brackets, flat
    # ``/``-suite repositories with no components included); deb822 stanzas
    # are split on blank lines, continuation lines fold into their field
    # (a bare ``.`` encodes an empty line, inline PGP blocks stay whole),
    # stanzas missing ``Types``/``URIs``/``Suites`` or carrying ``Enabled:
    # no`` are skipped, and multi-valued ``Types``/``URIs``/``Suites`` expand
    # into one entry per combination -- the baseline's expansion order. Entries
    # keep file order (no dedup, no sorting); no sources files at all read
    # as ``[]`` (the baseline's ``default = list``).
    #
    # Deliberate usability deviations from the baseline: entries are the plain
    # ``AptSource`` TypedDict-shaped dicts instead of its ``AptRepo``
    # dataclass/dict hybrid, and every ``options`` value is a list of strings
    # instead of the baseline's single-value-collapses-to-str mix. The file
    # marker is reemote's own (``#reemote-aptsource``, like
    # ``DnfRepositories``' filename marker) instead of ``##UPSTREAM_FILE``.
    # The baseline's ``requires_command`` gating on ``apt`` is deliberately
    # dropped: the command is pure shell and ``cat`` and never runs ``apt``,
    # so a host without apt simply reports its files (usually ``[]``).

    #: Marker prefixing each cat'd file in ``COMMAND``'s output, naming the
    #: source file so ``parse`` can pick the format by extension
    #: (the baseline's ``##UPSTREAM_FILE`` marker, renamed).
    FILE_MARKER = "#reemote-aptsource"

    #: Cat the legacy sources file and every ``.list``/``.sources`` file,
    #: each prefixed by a marker line naming it and followed by a blank
    #: line (so a deb822 file's last stanza is flushed). The ``[ -e ]``
    #: guard skips missing files, including unexpanded globs.
    COMMAND = (
        "for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list"
        " /etc/apt/sources.list.d/*.sources; do "
        f'[ -e "$f" ] || continue; printf "{FILE_MARKER} %s\\n" "$f"; cat "$f"; echo; '
        "done"
    )

    #: The baseline's legacy source-line regex: ``deb [options] url suite
    #: components``. The components group is optional -- flat repositories
    #: use a suite ending in ``/`` and supply none -- and the bracketed
    #: options group is optional too.
    LINE_REGEX = re.compile(
        r"^(deb(?:-src)?)(?:\s+\[([^\]]+)\])?\s+([^\s]+)\s+([^\s]+)"
        r"(?:\s+([a-z-\s\d]+))?\s*$"
    )

    @classmethod
    def _parse_list_line(cls, line: str) -> AptSource | None:
        """Parse one legacy ``deb ...`` line, or None when it does not match.

        Each bracketed ``key=value`` option becomes a list: a comma-separated
        value splits into several entries, a single value stays a one-entry
        list (the usability deviation from the baseline's str-or-list mix).
        """
        matches = cls.LINE_REGEX.match(line)
        if matches is None:
            return None

        options: dict[str, list[str]] = {}
        if matches.group(2):
            for option in matches.group(2).split():
                key, value = option.split("=", 1)
                options[key] = value.split(",")

        return {
            "type": matches.group(1),
            "url": matches.group(3),
            "distribution": matches.group(4),
            "components": matches.group(5).split() if matches.group(5) else [],
            "options": options,
        }

    @classmethod
    def _parse_deb822_stanza(cls, lines: list[str]) -> list[AptSource]:
        """Parse one deb822 stanza into its expanded repository entries.

        Continuation lines (leading space/tab) fold into the current field
        with newlines, a bare ``.`` encoding an empty line; ``#`` comment
        lines never continue a field. Stanzas missing a required field or
        disabled with ``Enabled: no`` contribute nothing. ``Architectures``,
        ``Signed-By`` and ``Trusted`` become the list-valued ``arch``,
        ``signed-by`` and ``trusted`` options; an inline armored PGP block
        stays a single ``signed-by`` entry. Multi-valued ``Types``, ``URIs``
        and ``Suites`` expand into one entry per combination, in the baseline's
        type-then-uri-then-suite order.
        """
        data: dict[str, str] = {}
        current_key: str | None = None
        for line in lines:
            if line.startswith("#"):
                continue
            if line and line[0] in (" ", "\t") and current_key is not None:
                continuation = line[1:]
                data[current_key] += "\n" + ("" if continuation == "." else continuation)
                continue
            if not line.strip():
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                current_key = key.strip()
                data[current_key] = value.strip()

        if not all(field in data for field in ("Types", "URIs", "Suites")):
            return []
        if data.get("Enabled", "yes").lower() != "yes":
            return []

        options: dict[str, list[str]] = {}
        if data.get("Architectures"):
            options["arch"] = data["Architectures"].split()
        signed_by = data.get("Signed-By", "").strip()
        if signed_by:
            options["signed-by"] = (
                [signed_by] if "-----BEGIN PGP" in signed_by else signed_by.split()
            )
        if data.get("Trusted"):
            options["trusted"] = [data["Trusted"].lower()]

        components = data.get("Components", "").split()
        return [
            {
                "type": repo_type,
                "url": uri,
                "distribution": suite,
                # Copy per entry so combinations never share mutable state.
                "components": list(components),
                "options": {key: list(value) for key, value in options.items()},
            }
            for repo_type in data.get("Types", "").split()
            for uri in data.get("URIs", "").split()
            for suite in data.get("Suites", "").split()
        ]

    @classmethod
    def _parse_deb822_file(cls, lines: list[str]) -> list[AptSource]:
        """Split a deb822 file on blank lines and parse each stanza."""
        repos: list[AptSource] = []
        stanza: list[str] = []
        for raw in [*lines, ""]:  # sentinel blank line flushes the last stanza
            if raw.strip():
                stanza.append(raw)
            elif stanza:
                repos.extend(cls._parse_deb822_stanza(stanza))
                stanza = []
        return repos

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[AptSource]:
        """Parse marker-grouped sources files into repository entries.

        Each ``FILE_MARKER`` line starts a new file group; a ``.sources``
        group parses as deb822 stanzas, anything else as legacy lines
        (blank lines and ``#`` comments skipped, non-matching lines
        ignored). Lines before the first marker are ignored. Pure and
        testable.
        """
        repos: list[AptSource] = []
        current_file: str | None = None
        buffer: list[str] = []

        def flush() -> None:
            nonlocal buffer
            if current_file is not None and buffer:
                if current_file.endswith(".sources"):
                    repos.extend(cls._parse_deb822_file(buffer))
                else:
                    for raw in buffer:
                        line = raw.strip()
                        if not line or line.startswith("#"):
                            continue
                        repo = cls._parse_list_line(line)
                        if repo is not None:
                            repos.append(repo)
            buffer = []

        prefix = f"{cls.FILE_MARKER} "
        for line in output:
            if line.startswith(prefix):
                flush()
                current_file = line[len(prefix) :].strip()
                continue
            buffer.append(line)
        flush()
        return repos

    @classmethod
    async def process(cls, host: Host) -> list[AptSource]:
        apt_sources = getattr(host, "apt_sources", None)
        if apt_sources is not None:
            # SimulatedHost seam: read the in-memory entries directly. Copy
            # each dict, its components list and every option list so
            # callers never mutate the simulated state.
            return [
                {
                    **source,
                    "components": list(source["components"]),
                    "options": {key: list(value) for key, value in source["options"].items()},
                }
                for source in apt_sources
            ]
        # Real transport: cat the sources files over asyncssh and parse.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class AptSourcesFile:
    """The current content of one apt sources file, or None when it is absent.

    **Commands:**

    ```bash
    test -e <path> && { printf '#reemote-sourcesfile\\n'; cat -- <path>; }
    ```

    **Returns:**

    ```python
    str | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_sources_file.py``.
    """

    # The path-generic raw-content fact of this namespace (the
    # ``DnfRepoFile`` pattern): ``apt.sources_file(present=False)`` reads it
    # to make its removal idempotent and ``apt.repo`` reads it to compute the
    # content it spools back, for a ``.sources`` or a ``.list`` file alike --
    # the fact never interprets the text. Where
    # ``AptSources`` parses *every* configured repository into ``AptSource``
    # dicts, this reports the raw text of a *single* file so the operation
    # can tell an existing file from a missing one. Absence is reported as
    # ``None`` -- never the empty string -- so a file that exists but is
    # empty stays distinguishable from one that is missing, which is what
    # lets the removal delete an empty leftover.

    #: Marker printed before the ``cat``'d content, only when ``test -e``
    #: passes, so ``process`` can tell an existing (possibly empty) file from
    #: a missing one (no marker on stdout).
    MARKER = "#reemote-sourcesfile"

    @classmethod
    def command(cls, path: str) -> str:
        """Shell printing ``MARKER`` then the file's content, only if it exists.

        The ``test -e`` guard means a missing file produces no output at all
        (not even the marker), while an existing one -- empty or not -- always
        leads with the marker line (``DnfRepoFile.command``'s form).
        """
        quoted = shlex.quote(path)
        return f"test -e {quoted} && {{ printf '{cls.MARKER}\\n'; cat -- {quoted}; }}"

    @classmethod
    async def process(cls, host: Host, path: str) -> str | None:
        remote_files = getattr(host, "remote_files", None)
        if remote_files is not None:
            # SimulatedHost seam: decode the content the simulated remote
            # filesystem holds at the path (None when absent). The same store
            # backs files.put/files.get, so a simulated apt.sources_file write
            # -- which stores its content here -- reads back through this fact.
            data = remote_files.get(path)
            return data.decode("utf-8") if data is not None else None
        # Real transport: cat the file over asyncssh behind the marker, so a
        # missing file (no marker) reads as None rather than empty content.
        stdout = await host.run_shell(cls.command(path))
        prefix = f"{cls.MARKER}\n"
        if not stdout.startswith(prefix):
            return None
        return stdout[len(prefix) :]


class AptPackageUpgrade(TypedDict):
    """
    Attributes:
        name: The package name, architecture suffixes (``libc6:i386``)
            intact, exactly as ``apt list --upgradable`` prints it.
        installed: The currently installed version (the row's
            ``[upgradable from: ...]`` value), epoch prefixes
            (``2:9.1.0016-1ubuntu7.4``) intact, exactly as apt prints it.
        candidate: The version ``apt upgrade`` would move the package
            to, epoch prefixes intact.
    """

    # One package's pending upgrade (``AptUpgradable``'s entry).
    #
    # A TypedDict rather than a class instance, like ``AptPackagePolicy``:
    # entries are ordinary dicts, so equality against a literal dict works
    # and ``json.dumps`` serializes them directly. The ``installed`` /
    # ``candidate`` keys match ``AptPackagePolicy``'s, so the two facts'
    # answers line up per package.

    name: str
    installed: str
    candidate: str


class AptUpgradable:
    """Packages with a pending upgrade, as a list of ``AptPackageUpgrade`` dicts.

    **Commands:**

    ```bash
    apt list --upgradable 2>/dev/null
    ```

    **Returns:**

    ```python
    list[AptPackageUpgrade]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_apt_upgradable.py``.
    """

    # A reemote original: the baseline ships no ``apt.AptUpgradable``
    # counterpart. ``apt list --upgradable`` rows
    # (``name/source-info version arch [upgradable from: old-version]``)
    # are matched with a package regex modeled on ``AptPackages``', so
    # architecture-suffixed names like ``libc6:i386`` keep their suffix,
    # epoch-prefixed versions stay intact and the ``Listing...`` header
    # and any other noise lines are ignored, for example::
    #
    #     [
    #         {
    #             "name": "libc6:i386",
    #             "installed": "2.39-0ubuntu8.3",
    #             "candidate": "2.39-0ubuntu8.4",
    #         },
    #         {
    #             "name": "vim",
    #             "installed": "2:9.1.0016-1ubuntu7.4",
    #             "candidate": "2:9.1.0016-1ubuntu7.6",
    #         },
    #     ]
    #
    # Each entry is an ``AptPackageUpgrade`` dict naming both ends of the
    # version jump ``apt upgrade`` would perform. This fact fills a
    # coverage gap of ``AptPackages`` and ``AptPolicy``: the installed
    # rows report only what is on the machine and a policy query answers
    # one package at a time, while this listing names every package an
    # upgrade would move and both versions of each jump -- the
    # change-management and auditing view to read before executing
    # ``apt upgrade``. The row's source-info (offering suite) and
    # architecture columns are deliberately dropped, like ``AptPackages``
    # drops them; ``AptPolicy`` reports the offering repositories when
    # they matter. If a name appears twice the last row wins (apt prints
    # one row per package) and the entries are sorted by name. No pending
    # upgrades (or no output at all) read as ``[]``. A missing ``apt``
    # exits 127, which ``run_shell`` reports as ``CommandNotFoundError``.

    #: Shell listing the upgradable packages over SSH. The ``2>/dev/null``
    #: suppresses the "Listing..." progress notice modern apt writes to
    #: stderr (like ``AptPackages``' command).
    COMMAND = "apt list --upgradable 2>/dev/null"

    #: The upgradable-row regex, modeled on ``AptPackages``' package
    #: regex: ``name/source-info version arch [upgradable from:
    #: old-version]`` -- the leading anchor and the ``[upgradable from:``
    #: tail filter out the optional "Listing..." header and any other
    #: noise. The name keeps an optional ``:arch`` suffix; both versions
    #: allow epoch colons, tildes, dots, dashes and plus signs.
    PACKAGE_REGEX = (
        r"^([a-zA-Z0-9\+\-\.]+(?::[a-zA-Z0-9]+)?)/\S+\s+"
        r"([a-zA-Z0-9:~\.\-\+]+)\s+\S+\s+\[upgradable from: ([a-zA-Z0-9:~\.\-\+]+)\]"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[AptPackageUpgrade]:
        """Parse ``apt list --upgradable`` rows into sorted upgrade entries.

        Each matching line contributes its candidate version (the row's
        main version column) and installed version (the ``[upgradable
        from: ...]`` value); non-matching lines -- the ``Listing...``
        header, blank lines, rows without the ``[upgradable from:`` tail
        -- are skipped, a repeated name keeps its last row and the entries
        are sorted by name. Pure and testable.
        """
        upgrades: dict[str, AptPackageUpgrade] = {}
        for line in output:
            matches = re.match(cls.PACKAGE_REGEX, line)
            if matches:
                upgrades[matches.group(1)] = {
                    "name": matches.group(1),
                    "installed": matches.group(3),
                    "candidate": matches.group(2),
                }
        return [upgrades[name] for name in sorted(upgrades)]

    @classmethod
    async def process(cls, host: Host) -> list[AptPackageUpgrade]:
        apt_upgradable = getattr(host, "apt_upgradable", None)
        if apt_upgradable is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # each entry dict so callers never mutate simulated state.
            return [{**upgrade} for upgrade in apt_upgradable]
        # Real transport: list the upgradable packages over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class DebPackageInfo(TypedDict):
    """
    Attributes:
        name: The package name from the ``Package:`` control field.
        version: The package version from the ``Version:`` control
            field, epoch prefixes intact.
    """

    # One ``.deb`` file's or installed package's identity (``DebPackage``'s
    # result). The bare singular ``DebPackage`` slot is taken by the fact
    # class, so the entry carries the ``Info`` suffix (the ``ApkPackageInfo``
    # convention).
    #
    # A TypedDict rather than a class instance, like ``AptPackagePolicy``:
    # the answer is an ordinary dict, so equality against a literal dict
    # works and ``json.dumps`` serializes it directly.

    name: str
    version: str


class DebPackage:
    """The name and version of a ``.deb`` file or an installed dpkg package.

    **Commands:**

    ```bash
    test -f <package> && case <package> in *.deb) dpkg -I <package> ;; *) dpkg -s <package> 2>/dev/null || true ;; esac || dpkg -s <package> 2>/dev/null || true
    ```

    **Returns:**

    ```python
    DebPackageInfo | None
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_deb.py``.
    """

    # Mirrors the baseline's ``deb.DebPackage``: given a ``.deb`` file *path* or a
    # package *name*, returns a ``DebPackageInfo`` dict (``{"name": ...,
    # "version": ...}``), or None when neither an existing ``.deb`` file nor
    # an installed package answers.
    # ``apt.deb`` reads it for the ``.deb`` file's metadata, then compares the
    # file's version against the installed versions ``AptPackages`` reports for
    # that name to decide whether the file's package is already installed at
    # that version.
    #
    # The shell queries the *file* first when the path exists and ends in
    # ``.deb`` (``dpkg -I``), and otherwise falls back to the *installed*
    # package database (``dpkg -s``) -- the baseline's ``test -f ... && case ...``
    # form. A name that is neither an existing ``.deb`` file nor installed
    # produces no ``Package:``/``Version:`` line, so ``process`` reports None.
    # The baseline returns an empty dict in that case; reemote reports None,
    # mirroring ``RpmPackage``'s None-on-absent convention so the operation can
    # test ``info is not None``. The baseline's ``requires_command`` gating on
    # ``dpkg`` is deliberately dropped: a missing ``dpkg`` is swallowed by the
    # command's ``|| true`` and reads as None.

    #: Matches the ``Package: <name>`` control field (the baseline's name regex).
    NAME_REGEX = re.compile(r"^Package:\s+([a-zA-Z0-9\+\-\.]+)$")

    #: Matches the ``Version: <version>`` control field (the baseline's version
    #: regex); the version allows epoch colons, tildes, dots, dashes and plus.
    VERSION_REGEX = re.compile(r"^Version:\s+([a-zA-Z0-9:~\.\-\+]+)$")

    @classmethod
    def command(cls, package: str) -> str:
        """Shell reporting ``package``'s name and version.

        Inspects the ``.deb`` archive (``dpkg -I``) when ``package`` is an
        existing file ending in ``.deb``; otherwise queries the installed
        package database (``dpkg -s``). The trailing ``|| true`` swallows a
        non-zero exit so an unknown name produces no output rather than
        failing the gather. The path is shell-quoted.
        """
        quoted = shlex.quote(package)
        return (
            f"test -f {quoted} && case {quoted} in "
            f"*.deb) dpkg -I {quoted} ;; "
            f"*) dpkg -s {quoted} 2>/dev/null || true ;; esac || "
            f"dpkg -s {quoted} 2>/dev/null || true"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> DebPackageInfo | None:
        """Parse ``dpkg``'s ``Package:``/``Version:`` control fields.

        Returns a ``DebPackageInfo`` from the matching lines (each line
        stripped first, so ``dpkg -I``'s tab-indented fields parse), or
        None when either field is missing -- an unknown name or an absent
        ``.deb`` file. Pure and testable.
        """
        name: str | None = None
        version: str | None = None
        for raw in output:
            line = raw.strip()
            name_match = cls.NAME_REGEX.match(line)
            if name_match:
                name = name_match.group(1)
                continue
            version_match = cls.VERSION_REGEX.match(line)
            if version_match:
                version = version_match.group(1)
        if name is None or version is None:
            return None
        return {"name": name, "version": version}

    @classmethod
    async def process(cls, host: Host, package: str) -> DebPackageInfo | None:
        deb_files = getattr(host, "deb_files", None)
        if deb_files is not None:
            # SimulatedHost seam: a ``.deb`` file path (``deb_files``) is
            # queried before an installed package name (``apt_packages``),
            # mirroring ``command``'s file-before-name precedence for ``.deb``
            # paths. Copy the dict so callers never mutate simulated state.
            info = deb_files.get(package)
            if info is not None:
                return dict(info)
            versions = host.apt_packages.get(package)
            if versions:
                return {"name": package, "version": versions[0]}
            return None
        # Real transport: query the .deb file / installed package over asyncssh.
        stdout = await host.run_shell(cls.command(package))
        return cls.parse(stdout.splitlines())


class AptSimulationDict(TypedDict):
    """
    Attributes:
        upgraded: Packages the simulated operation would upgrade.
        newly_installed: Packages the simulated operation would install
            anew (dependency pulls and dist-upgrade additions).
        removed: Packages the simulated operation would remove.
        not_upgraded: Packages the simulated operation would hold back.
    """

    # One simulation's proposed change counts (``SimulateOperationWillChange``).
    #
    # A TypedDict rather than a class instance, like ``AptPackageUpgrade``:
    # entries are ordinary dicts, so equality against a literal dict works
    # and ``json.dumps`` serializes them directly. Mirrors the baseline's
    # ``AptSimulationDict`` field for field.

    upgraded: int
    newly_installed: int
    removed: int
    not_upgraded: int


class SimulateOperationWillChange:
    """Proposed change counts of a simulated ``apt-get`` operation.

    **Commands:**

    ```bash
    LC_ALL=C DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" <command> --dry-run
    ```

    **Returns:**

    ```python
    AptSimulationDict | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_simulate_operation_will_change.py``.
    """

    # Mirrors the baseline's ``apt.SimulateOperationWillChange``: given an
    # ``apt-get`` subcommand phrase (e.g. ``"dist-upgrade"``), runs it with
    # ``--dry-run`` behind the non-interactive prefix and ``LC_ALL=C`` (so
    # the summary line stays English on localized hosts) and parses the
    # ``N upgraded, N newly installed, N to remove and N not upgraded.``
    # line into an ``AptSimulationDict``, for example::
    #
    #     {
    #         "upgraded": 15,
    #         "newly_installed": 0,
    #         "removed": 0,
    #         "not_upgraded": 0,
    #     }
    #
    # The simulation never changes anything and needs no root; every other
    # line -- the ``NOTE: This is only a simulation!`` banner, the progress
    # rows, the indented package lists, the ``Inst``/``Conf``/``Remv`` detail
    # rows -- is skipped. ``apt.dist_upgrade`` reads it to decide whether the
    # real ``dist-upgrade`` would do anything, counting newly-installed and
    # removed packages too -- which ``AptUpgradable`` (a plain ``upgrade``
    # listing) cannot see. ``command`` is interpolated verbatim (the baseline
    # parity): it is an ``apt-get`` subcommand phrase, possibly with flags,
    # not a single quoted argument.
    #
    # Deliberate deviation from the baseline: when no summary line appears,
    # upstream raises an exception (which its caller swallows into "run the
    # real command anyway"); reemote reports None instead, so the caller
    # branches on a value rather than on exception control flow. A non-zero
    # exit of the simulation itself still raises ``CommandError`` through
    # ``run_shell`` -- reemote's fail-fast convention. The baseline's
    # ``requires_command`` gating on ``apt-get`` is deliberately dropped: a
    # missing ``apt-get`` exits 127, which ``run_shell`` reports as
    # ``CommandNotFoundError``.

    #: The baseline's ``APT_CHANGES_RE``: the simulation's summary count line.
    CHANGES_REGEX = re.compile(
        r"^(\d+) upgraded, (\d+) newly installed, (\d+) to remove and (\d+) not upgraded.$"
    )

    @classmethod
    def command(cls, command: str) -> str:
        """Shell simulating ``command`` over SSH without changing anything.

        ``LC_ALL=C`` keeps apt's summary line in English (the baseline's
        ``SimulateOperationWillChange.command``); the non-interactive
        prefix matches the real operation's so the simulation and the
        apply resolve dependencies identically.
        """
        return "LC_ALL=C " + _noninteractive_apt(f"{command} --dry-run")

    @classmethod
    def parse(cls, output: Iterable[str]) -> AptSimulationDict | None:
        """Parse the simulation's summary line into change counts.

        The first line matching ``CHANGES_REGEX`` wins; every other line
        is skipped. Returns None when no summary line appears -- the
        simulation failed to propose changes (the None-on-failure
        deviation from the baseline's exception). Pure and testable.
        """
        for line in output:
            matches = cls.CHANGES_REGEX.match(line)
            if matches:
                return {
                    "upgraded": int(matches.group(1)),
                    "newly_installed": int(matches.group(2)),
                    "removed": int(matches.group(3)),
                    "not_upgraded": int(matches.group(4)),
                }
        return None

    @classmethod
    async def process(cls, host: Host, command: str) -> AptSimulationDict | None:
        apt_simulations = getattr(host, "apt_simulations", None)
        if apt_simulations is not None:
            # SimulatedHost seam: the command-keyed store holds the parsed
            # answers; a phrase absent from it reads as None, like a
            # simulation with no summary line. Copy the dict so callers
            # never mutate simulated state.
            simulation = apt_simulations.get(command)
            return dict(simulation) if simulation is not None else None
        # Real transport: simulate the operation over asyncssh.
        stdout = await host.run_shell(cls.command(command))
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------


@dataclass(frozen=True)
class DebInstallCommand(Command):
    """``dpkg --force-confdef --force-confold -i <src>``: install a ``.deb``.

    ``src`` is shell-quoted. ``ignore_deps_errors`` appends
    ``2> /dev/null || true`` for the *first* pass of ``apt.deb``'s install
    sequence, which tolerates unmet dependencies so the following
    ``AptInstallFixCommand`` can resolve them; the final pass leaves it off so
    a still-broken package fails fast (``CommandError``). The ``|| true``
    fallback is privilege-safe -- ``true`` needs no root, so a ``sudo`` prefix
    on the whole line still elevates the ``dpkg`` arm (unlike dnf.rpm's
    rejected ``|| rpm -i`` chain, whose fallback did real work unprivileged).
    """

    src: str
    ignore_deps_errors: bool = False

    def shell(self) -> str:
        base = f"dpkg --force-confdef --force-confold -i {shlex.quote(self.src)}"
        if self.ignore_deps_errors:
            return f"{base} 2> /dev/null || true"
        return base


@dataclass(frozen=True)
class AptInstallFixCommand(Command):
    """``apt-get ... install -f``: resolve a ``.deb``'s unmet dependencies.

    The middle pass of ``apt.deb``'s install sequence, between the tolerant
    and the final ``dpkg -i``. ``wait_for_lock`` adds the
    ``DPkg::Lock::Timeout`` option (``True`` -> 60s, an int -> that many
    seconds) so apt waits for its lock instead of failing instantly.
    """

    wait_for_lock: bool | int = False

    def shell(self) -> str:
        return _noninteractive_apt("install -f", self.wait_for_lock)


@dataclass(frozen=True)
class AptRemoveCommand(Command):
    """``apt-get ... remove <names>``: remove installed packages by name.

    The ``present=False`` direction of ``apt.deb`` and ``apt.packages``.
    ``packages`` are shell-quoted; ``wait_for_lock`` adds the
    ``DPkg::Lock::Timeout`` option. ``purge`` switches the verb to
    ``apt-get purge`` (``apt.packages(purge=True)``), removing configuration
    files alongside the package, and ``extra_args`` appends additional
    uninstall arguments verbatim before the names.
    """

    packages: tuple[str, ...]
    wait_for_lock: bool | int = False
    purge: bool = False
    extra_args: str | None = None

    def shell(self) -> str:
        command = "purge" if self.purge else "remove"
        if self.extra_args:
            command += f" {self.extra_args}"
        base = _noninteractive_apt(command, self.wait_for_lock)
        names = " ".join(shlex.quote(package) for package in self.packages)
        return f"{base} {names}"


async def deb(
    ctx: OpContext,
    src: str,
    present: bool = True,
    wait_for_lock: bool | int = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add/remove a ``.deb`` file package.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (str): Path to the ``.deb`` file on the *target host*.
        present (bool, optional): Whether the package should be installed.
            Defaults to True.
        wait_for_lock (bool | int, optional): Wait for the apt lock instead of
            failing instantly; ``True`` waits up to 60 seconds, an int sets
            the timeout in seconds. Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated apt deb package commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_deb.py``.
    """

    # baseline ``apt.deb`` parity.
    #
    # ``src`` names a ``.deb`` that already exists on the target host (upload
    # one from the controller with ``files.put`` first). The ``DebPackage`` fact
    # reads the file's ``name``/``version``; the package counts as present only
    # when ``AptPackages`` lists that exact version for the file's name.
    # ``present=True`` installs when it does not, with the baseline's three-pass
    # sequence -- a tolerant ``dpkg -i`` (ignoring unmet dependencies), an
    # ``apt-get install -f`` to resolve them, then a final ``dpkg -i`` that
    # configures the package and fails if dependencies are still missing -- and
    # is otherwise a no-op. ``present=False`` removes the file's package by name
    # with ``apt-get remove`` only when that exact version is installed.
    # Installing or removing needs root, so flag the request ``sudo=True`` (or
    # ``su=True``).
    #
    # The baseline's URL ``src`` -- downloading a remote ``.deb`` mid-deploy --
    # is deliberately dropped: ``apt.deb`` installs a *binary* package file that
    # must already be on the host, and reemote's URL download (``files.download``)
    # is text-only, so a ``src`` with a URL scheme raises ``ValueError``
    # (mirroring ``dnf.rpm``). Stage the ``.deb`` control-side and ``files.put``
    # it onto the host first. Because removal reads the package name from the file, a
    # ``present=False`` request whose ``.deb`` is absent from the host cannot
    # resolve a name and is a no-op -- the same limitation the baseline documents for
    # an un-downloaded URL source. The baseline's ``force`` option (``--force-yes``)
    # is deliberately dropped: the flag is deprecated in modern apt.
    # ``**kwargs`` absorbs any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    if urlparse(src).scheme:
        raise ValueError(
            "apt.deb does not support a URL src (it installs a binary .deb "
            "that must already be on the host; files.download is text-only); "
            f"put the .deb on the host first, got: {src}"
        )

    info = await ctx.facts.get(DebPackage, src)
    current = await ctx.facts.get(AptPackages)
    installed = {entry["name"]: entry["versions"] for entry in current}
    # ``installed_name`` is set only when the file's package is installed at
    # the file's exact version, so it doubles as the baseline's ``exists`` flag and
    # carries the name the removal needs.
    installed_name: str | None = None
    if info is not None and info["version"] in installed.get(info["name"], []):
        installed_name = info["name"]

    if present:
        if installed_name is not None:
            ctx.reason = f"deb {src} is installed"
        else:
            ctx.changed = True
            ctx.reason = f"deb {src} not installed, will install"
            yield DebInstallCommand(src=src, ignore_deps_errors=True)
            yield AptInstallFixCommand(wait_for_lock=wait_for_lock)
            yield DebInstallCommand(src=src)
    else:
        if installed_name is not None:
            ctx.changed = True
            ctx.reason = f"deb {src} installed, will remove"
            yield AptRemoveCommand(
                packages=(installed_name,), wait_for_lock=wait_for_lock
            )
        else:
            ctx.reason = f"deb {src} is not installed"


@dataclass(frozen=True)
class AptDistUpgradeCommand(Command):
    """``apt-get ... dist-upgrade``: upgrade every package, resolving deps.

    The mutation ``apt.dist_upgrade`` yields when the simulation proposes
    changes. ``wait_for_lock`` adds the ``DPkg::Lock::Timeout`` option
    (``True`` -> 60s, an int -> that many seconds) so apt waits for its
    lock instead of failing instantly.
    """

    wait_for_lock: bool | int = False

    def shell(self) -> str:
        return _noninteractive_apt("dist-upgrade", self.wait_for_lock)


async def dist_upgrade(
    ctx: OpContext,
    wait_for_lock: bool | int = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Update all apt packages, employing dist-upgrade.

    Args:
        ctx (OpContext): Execution context for the operation.
        wait_for_lock (bool | int, optional): Wait for the apt lock instead of
            failing instantly; ``True`` waits up to 60 seconds, an int sets
            the timeout in seconds. Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated apt dist-upgrade commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_dist_upgrade.py``.
    """

    # baseline ``apt.dist_upgrade`` parity.
    #
    # The ``SimulateOperationWillChange`` fact simulates ``dist-upgrade``
    # first: when every proposed count is zero the operation is a no-op,
    # otherwise it yields the ``dist-upgrade`` -- which, unlike a plain
    # ``upgrade``, may also install new dependencies and remove conflicting
    # packages, all of which the simulation counts. A failed simulation
    # (None, no summary line) runs the real command anyway, the baseline's
    # fallback: if the dry-run cannot propose changes the operation will
    # probably fail too, and failing on the real command reports the true
    # error. Upgrading needs root, so flag the request ``sudo=True`` (or
    # ``su=True``).
    #
    # The baseline's ``auto_remove`` parameter (appending ``--autoremove`` to the
    # same command) is deliberately split out into the standalone
    # ``apt.auto_remove`` operation, so orphaned dependencies can be cleaned
    # up without also performing an upgrade; this operation never passes
    # ``--autoremove``. ``**kwargs`` absorbs any further global arguments
    # (``op()`` already strips ``name``/``sudo``/``su``).
    changes = await ctx.facts.get(SimulateOperationWillChange, "dist-upgrade")
    if changes is not None and not any(changes.values()):
        ctx.reason = "dist-upgrade skipped, no changes would be performed"
    else:
        ctx.changed = True
        if changes is None:
            ctx.reason = "dist-upgrade simulation failed, will run anyway"
        else:
            ctx.reason = (
                f"dist-upgrade will change packages ({changes['upgraded']} upgraded, "
                f"{changes['newly_installed']} newly installed, "
                f"{changes['removed']} to remove, "
                f"{changes['not_upgraded']} not upgraded)"
            )
        yield AptDistUpgradeCommand(wait_for_lock=wait_for_lock)


@dataclass(frozen=True)
class AptAutoRemoveCommand(Command):
    """``apt-get ... autoremove``: remove no-longer-needed dependencies.

    The mutation ``apt.auto_remove`` yields when ``AptAutoremovable``
    names removable packages. ``wait_for_lock`` adds the
    ``DPkg::Lock::Timeout`` option (``True`` -> 60s, an int -> that many
    seconds).
    """

    wait_for_lock: bool | int = False

    def shell(self) -> str:
        return _noninteractive_apt("autoremove", self.wait_for_lock)


async def auto_remove(
    ctx: OpContext,
    wait_for_lock: bool | int = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Remove transitive dependencies that are no longer needed.

    Args:
        ctx (OpContext): Execution context for the operation.
        wait_for_lock (bool | int, optional): Wait for the apt lock instead of
            failing instantly; ``True`` waits up to 60 seconds, an int sets
            the timeout in seconds. Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated apt autoremove commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_auto_remove.py``.
    """

    # A reemote original: the baseline only offers autoremove as the
    # ``auto_remove`` parameter of ``apt.dist_upgrade``/``apt.upgrade``,
    # never as a standalone operation, so orphaned dependencies cannot be
    # cleaned up without also performing an upgrade. The
    # ``AptAutoremovable`` fact simulates the autoremove and names exactly
    # the no-longer-needed packages: a non-empty list yields the
    # ``autoremove`` (the names are already sorted, so the reason reads
    # identically in both phases), an empty list is a no-op. Removing
    # packages needs root, so flag the request ``sudo=True`` (or
    # ``su=True``). ``**kwargs`` absorbs any further global arguments
    # (``op()`` already strips ``name``/``sudo``/``su``).
    removable = await ctx.facts.get(AptAutoremovable)
    if removable:
        ctx.changed = True
        names = ", ".join(entry["name"] for entry in removable)
        ctx.reason = f"autoremovable packages will be removed: {names}"
        yield AptAutoRemoveCommand(wait_for_lock=wait_for_lock)
    else:
        ctx.reason = "no autoremovable packages"


# --- apt.key -------------------------------------------------------------------


def _key_spool_path(dest: str) -> str:
    """The deterministic ``/tmp`` spool *file* path ``apt.key`` uploads to.

    The key stream is spooled inside a per-dest directory rather than at the
    top level of ``/tmp``: the directory name is derived from the resolved
    keyring path's sha1 so two keys targeting different paths never share
    (and clobber) one spool, while the same dest always yields the same name
    in both phases -- the request sequence stays deterministic. The stream is
    spooled to a fixed ``key.gpg`` file inside that directory, and the
    elevated install ``rm -rf``s the whole directory afterwards, so no
    leftover lands in ``/tmp``. The name avoids *collisions*, not a hostile
    local user: it is predictable, so on a multi-tenant host the ``/tmp``
    spool is still a symlink-race exposure (the same trade-off the other
    spool-and-install operations make).
    """
    digest = hashlib.sha1(dest.encode()).hexdigest()[:16]
    return f"/tmp/reemote-apt-key-{digest}/key.gpg"


@dataclass(frozen=True)
class AptKeyUploadCommand(TransferCommand):
    """Spool the GPG key stream to a ``/tmp`` path on the target host.

    The first half of ``apt.key`` and a *non-shell* descriptor: it copies the
    IO-like key content over the connection's scp channel via
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
class AptKeyInstallCommand(Command):
    """Install the spooled key as a keyring file, then remove the spool file.

    The second half of ``apt.key`` and the elevated one: writing under
    ``/etc/apt/keyrings/`` needs root, so the request is flagged ``sudo=True``
    and the whole sequence is wrapped in ``sh -c`` -- a bare ``sudo`` prefix
    would elevate only the first command of the ``&&`` chain and leave the
    cleanup running as the login user, whereas ``sudo sh -c '...'`` runs every
    step as root, which can always remove the login-user spool file. The
    sequence creates the destination directory, then mirrors the baseline's
    content sniff: an ASCII-armored key (``BEGIN PGP PUBLIC KEY BLOCK`` in the
    bytes, regardless of any file extension) is converted to the binary form
    APT's ``signed-by=`` expects with ``gpg --batch --yes --dearmor``
    (``--yes`` overwrites an existing keyring -- ``--batch`` alone refuses,
    which would break the always-install contract on a second run), binary
    content is copied verbatim; finally the keyring is chmod'ed ``0644``
    (the baseline's fixed ``mode`` for apt keys) and the spool file removed.
    All paths are shell-quoted.
    """

    spool: str  # where AptKeyUploadCommand wrote the key stream
    dest: str  # resolved keyring file path on the target host

    def shell(self) -> str:
        spool = shlex.quote(self.spool)
        # The spool file lives inside a sha1-derived /tmp directory; remove
        # the whole directory so no leftover lands in /tmp.
        spool_dir = shlex.quote(str(PurePosixPath(self.spool).parent))
        dest = shlex.quote(self.dest)
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        marker = shlex.quote("BEGIN PGP PUBLIC KEY BLOCK")
        inner = (
            f"mkdir -p {parent} && "
            f"if grep -q {marker} {spool}; then "
            f"gpg --batch --yes --dearmor -o {dest} {spool}; "
            f"else cp {spool} {dest}; fi && "
            f"chmod 0644 {dest} && rm -rf {spool_dir}"
        )
        return f"sh -c {shlex.quote(inner)}"


async def key(
    ctx: OpContext, src: IO[Any], dest: str, **kwargs: Any
) -> AsyncGenerator[Command]:
    """Install a GPG keyring file for APT.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (IO[Any]): The key content as a seekable IO-like object on the
            control machine (an ``io.BytesIO``/``io.StringIO``, a file opened
            with ``open()``, ...), rewound to position 0 before it is read;
            ASCII-armored and binary key content are both accepted.
        dest (str): The keyring filename or absolute path on the target host;
            a relative name is resolved under ``/etc/apt/keyrings/`` so a
            sources entry can reference it via ``signed-by=`` (the modern,
            non-deprecated replacement for ``apt-key add``).
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated apt GPG keyring commands.

    **Raises:**

    ```python
    ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_key.py``.
    """

    # baseline ``apt.key`` parity, install half only.
    #
    # This is **non-idempotent**: it always installs, so it always reports a
    # change and yields its commands on every run -- a deliberate deviation
    # from the baseline's ``apt.key``, which skips installation when the destination
    # file already exists (and, like the existence check, its mode-repair step
    # is dropped). It yields *two* descriptors from the one request -- an
    # ``AptKeyUploadCommand`` transfer that spools the stream to a ``/tmp`` path
    # over scp, then an ``AptKeyInstallCommand`` running the elevated install:
    # create the destination directory, dearmor the key with ``gpg`` when the
    # content is ASCII-armored (copy it verbatim when already binary), chmod the
    # keyring ``0644`` and remove the spool file. Because the install needs
    # root, flag the request ``sudo=True`` (or ``su=True``); the transfer stays
    # unprivileged regardless (transfers ride the connection as the login
    # user), so the two commands of one request run at different privilege
    # levels -- the driver dispatches the transfer to scp and elevates only the
    # shell install.
    #
    # The baseline's ``src`` is a *filename or URL* the target host reads itself.
    # reemote does not support controller-side file locations, so ``src`` is an
    # IO-like *stream* of the key content instead and the bytes reach the host
    # over the connection's scp channel; a ``str`` ``src`` raises ``ValueError``
    # (fetch a URL on the controller and pass its bytes). ``dest`` is required:
    # The baseline derives a destination name from the source URL or key IDs, which
    # an anonymous stream cannot provide; an explicit ``dest`` is used verbatim
    # after the directory resolution (upstream's "``.gpg`` extension is
    # enforced" applies only to *derived* names, not explicit ones).
    # ``mode`` is fixed at ``0644`` as the baseline's ``apt.key`` hard-codes it.
    # The baseline's ``keyserver``/``keyid`` fetch (a target-side ``gpg --recv-keys``
    # into a temporary ``GNUPGHOME``) and its ``present=False`` removal (whole
    # keyring file, or individual keys with empty-file pruning) are deliberately
    # dropped: keys arrive as streams from the controller, and removal is
    # expressible with the ``files`` primitives. ``**kwargs`` absorbs the baseline's
    # global arguments (``op()`` already strips ``name``/``sudo``/``su``).
    if isinstance(src, str):
        # ValueError (not TypeError) keeps apt.key consistent with apt.deb and
        # dnf.key, which also reject an unusable string ``src`` with
        # ValueError; TRY004 assumes any isinstance guard should raise
        # TypeError instead.
        raise ValueError(  # noqa: TRY004
            "apt.key does not support a filename/URL src (reemote takes IO "
            "streams, not controller-side file locations); pass the key content "
            f"as an IO-like object, got: {src!r}"
        )
    if not dest:
        raise ValueError("apt.key requires a non-empty dest keyring filename or path")
    if not dest.startswith("/"):
        dest = f"/etc/apt/keyrings/{dest}"
    ctx.changed = True
    ctx.reason = f"install GPG key from {getattr(src, 'name', '<stream>')} to {dest}"
    spool = _key_spool_path(dest)
    yield AptKeyUploadCommand(src=src, dest=spool)
    yield AptKeyInstallCommand(spool=spool, dest=dest)


# --- apt.mark ------------------------------------------------------------------


@dataclass(frozen=True)
class AptMarkHoldCommand(Command):
    """``apt-mark hold <names>``: keep packages out of upgrades.

    The ``held=True`` direction of ``apt.mark``. The package names are
    shell-quoted. Hold and unhold are different ``apt-mark`` verbs, so each
    direction carries its own descriptor (the ``DnfModuleEnableCommand`` /
    ``DnfModuleDisableCommand`` pattern) rather than one flagged descriptor.
    """

    packages: tuple[str, ...]

    def shell(self) -> str:
        names = " ".join(shlex.quote(package) for package in self.packages)
        return f"apt-mark hold {names}"


@dataclass(frozen=True)
class AptMarkUnholdCommand(Command):
    """``apt-mark unhold <names>``: let held packages upgrade again.

    The ``held=False`` direction of ``apt.mark``. The package names are
    shell-quoted.
    """

    packages: tuple[str, ...]

    def shell(self) -> str:
        names = " ".join(shlex.quote(package) for package in self.packages)
        return f"apt-mark unhold {names}"


async def mark(
    ctx: OpContext,
    packages: str | list[str] | tuple[str, ...],
    held: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Hold packages back from upgrades, or release held ones.

    Args:
        ctx (OpContext): Execution context for the operation.
        packages (str | list[str] | tuple[str, ...]): Package names to mark; a
            single string or a list/tuple. Prefer tuples when building requests
            via ``op()``, since ``OperationRequest`` kwargs must stay hashable.
        held (bool, optional): Whether the packages should be held
            (``apt-mark hold``, keeping them out of upgrades) or released
            (``apt-mark unhold``). Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated apt-mark hold/unhold commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_mark.py``.
    """

    # A reemote original: the baseline ships no operation managing package hold
    # states. Idempotent on ``AptHeldPackages``: ``held=True`` yields one
    # ``AptMarkHoldCommand`` for only the requested names not already held,
    # ``held=False`` one ``AptMarkUnholdCommand`` for only the names
    # currently held; a request whose names are all in the wanted state is a
    # no-op. The command keeps the requested order (stable across phases),
    # and names compare verbatim against what ``apt-mark showhold`` prints,
    # so an architecture-suffixed package must be requested in its suffixed
    # form (``libc6:i386``). Marking writes to the root-owned dpkg database,
    # so flag the request ``sudo=True`` (or ``su=True``); a missing
    # ``apt-mark`` exits 127 -- reported as ``CommandNotFoundError``.
    #
    # ``apt-mark``'s ``auto``/``manual`` installation-reason marks are
    # deliberately out of scope: this operation manages only the hold state
    # ``AptHeldPackages`` reports. There is no ``wait_for_lock`` option --
    # ``apt-mark`` is not ``apt-get`` and takes no ``DPkg::Lock::Timeout``
    # wrapper (the fact's ``showhold`` command is bare for the same reason).
    # ``**kwargs`` absorbs any further global arguments (``op()`` already
    # strips ``name``/``sudo``/``su``).

    # Normalize like apt.packages: a bare string is one name.
    requested = (packages,) if isinstance(packages, str) else tuple(packages)

    if not requested:
        ctx.reason = "no packages requested"
        return

    current = await ctx.facts.get(AptHeldPackages)
    held_names = {entry["name"] for entry in current}
    if held:
        to_hold = tuple(pkg for pkg in requested if pkg not in held_names)
        if to_hold:
            ctx.changed = True
            ctx.reason = f"{len(to_hold)} package(s) to hold"
            yield AptMarkHoldCommand(packages=to_hold)
        else:
            ctx.reason = "all packages held"
    else:
        to_unhold = tuple(pkg for pkg in requested if pkg in held_names)
        if to_unhold:
            ctx.changed = True
            ctx.reason = f"{len(to_unhold)} package(s) to unhold"
            yield AptMarkUnholdCommand(packages=to_unhold)
        else:
            ctx.reason = "no held packages to unhold"


# --- apt.packages --------------------------------------------------------------


def _split_version(package: str) -> tuple[str, str | None]:
    """Split ``pkg=version`` into ``(name, version)``; version is None if absent.

    The baseline's ``PkgInfo.from_possible_pair`` splits on the *last* ``=``
    (``rsplit``), so a version string carrying ``=`` stays intact.
    """
    name, sep, version = package.rpartition("=")
    return (name, version) if sep else (package, None)


@dataclass(frozen=True)
class AptUpdateCommand(Command):
    """``apt-get update``: refresh the package lists.

    The stateless ``update=True`` prefix of ``apt.packages`` and the
    standalone ``apt.update`` operation. Like the baseline's ``apt.update`` this
    is the bare ``apt-get`` form -- no
    ``DEBIAN_FRONTEND``/``-y`` wrapper, since ``update`` asks no questions --
    with only the optional ``DPkg::Lock::Timeout`` option inserted
    (``True`` -> 60s, an int -> that many seconds).
    """

    wait_for_lock: bool | int = False

    def shell(self) -> str:
        if self.wait_for_lock:
            return f"apt-get {_apt_lock_timeout(self.wait_for_lock)} update"
        return "apt-get update"


@dataclass(frozen=True)
class AptUpgradeCommand(Command):
    """``apt-get ... upgrade``: upgrade every upgradable package.

    The mutation ``apt.packages(upgrade=True)`` and the standalone
    ``apt.upgrade`` operation yield when the simulation proposes changes.
    Unlike ``dist-upgrade`` it never installs new
    dependencies or removes conflicting packages. ``wait_for_lock`` adds
    the ``DPkg::Lock::Timeout`` option (``True`` -> 60s, an int -> that many
    seconds).
    """

    wait_for_lock: bool | int = False

    def shell(self) -> str:
        return _noninteractive_apt("upgrade", self.wait_for_lock)


@dataclass(frozen=True)
class AptInstallCommand(Command):
    """``apt-get ... install <packages>``: install (or upgrade) packages.

    The ``present=True`` direction of ``apt.packages``; the ``latest``
    upgrade path reuses it with ``extra_args=None`` (the baseline builds its
    upgrade command before appending ``extra_install_args``) but keeps
    ``default_release``, so release targeting stays consistent across the
    install and the upgrade and a re-run converges on the same release.
    ``default_release`` adds apt's ``-t <release>`` (``--target-release``)
    right after ``install``, ``no_recommends``/``allow_downgrades`` add apt's
    ``--no-install-recommends``/``--allow-downgrades`` flags, ``extra_args``
    is appended verbatim after them, and the release and the package names
    (``<pkg>=<version>`` pins included) are shell-quoted.
    ``wait_for_lock`` adds the ``DPkg::Lock::Timeout`` option.
    """

    packages: tuple[str, ...]
    default_release: str | None = None
    no_recommends: bool = False
    allow_downgrades: bool = False
    extra_args: str | None = None
    wait_for_lock: bool | int = False

    def shell(self) -> str:
        command = "install"
        if self.default_release is not None:
            command += f" -t {shlex.quote(self.default_release)}"
        if self.no_recommends:
            command += " --no-install-recommends"
        if self.allow_downgrades:
            command += " --allow-downgrades"
        if self.extra_args:
            command += f" {self.extra_args}"
        base = _noninteractive_apt(command, self.wait_for_lock)
        names = " ".join(shlex.quote(package) for package in self.packages)
        return f"{base} {names}"


async def packages(
    ctx: OpContext,
    packages: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    latest: bool = False,
    update: bool = False,
    upgrade: bool = False,
    no_recommends: bool = False,
    allow_downgrades: bool = False,
    default_release: str | None = None,
    purge: bool = False,
    extra_install_args: str | None = None,
    extra_uninstall_args: str | None = None,
    wait_for_lock: bool | int = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install/remove/update apt packages.

    Args:
        ctx (OpContext): Execution context for the operation.
        packages (str | list[str] | tuple[str, ...] | None, optional): Packages
            to ensure; a single string, a list/tuple, or None. Prefer tuples
            when building requests via ``op()``, since ``OperationRequest``
            kwargs must stay hashable. Versions can be pinned as
            ``<pkg>=<version>``. Defaults to None.
        present (bool, optional): Whether the packages should be installed.
            Defaults to True.
        latest (bool, optional): Whether to upgrade installed packages without
            a pinned version. Defaults to False.
        update (bool, optional): Run ``apt-get update`` before installing
            packages. Defaults to False.
        upgrade (bool, optional): Run ``apt-get upgrade`` before installing
            packages. Defaults to False.
        no_recommends (bool, optional): Add ``--no-install-recommends`` to the
            install command. Defaults to False.
        allow_downgrades (bool, optional): Add ``--allow-downgrades`` to the
            install command. Defaults to False.
        default_release (str | None, optional): Pin the install to a target
            release with apt's ``-t <release>`` (``--target-release``), so
            unpinned names resolve their candidate version from that release.
            Unlike ``extra_install_args`` this is carried on the ``latest``
            upgrade command too, so a re-run converges on the same release;
            the ``AptPackages`` presence comparison has no release dimension
            and stays name/version based. Defaults to None.
        purge (bool, optional): When removing (``present=False``), use
            ``apt-get purge`` so configuration files are removed alongside
            the package. Defaults to False.
        extra_install_args (str | None, optional): Additional arguments to the
            apt install command. Defaults to None.
        extra_uninstall_args (str | None, optional): Additional arguments to
            the apt remove command. Defaults to None.
        wait_for_lock (bool | int, optional): Wait for the apt lock instead of
            failing instantly; ``True`` waits up to 60 seconds, an int sets
            the timeout in seconds. Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated apt package commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_packages.py``.
    """

    # baseline ``apt.packages`` parity.
    #
    # ``update`` is stateless (always yields, like the baseline's non-idempotent
    # ``apt.update``). ``upgrade`` is gated on the
    # ``SimulateOperationWillChange`` fact exactly like ``apt.dist_upgrade``:
    # all-zero proposed counts are a no-op, a failed simulation (None)
    # runs the real ``upgrade`` anyway. The ``AptPackages`` fact supplies
    # the installed versions, so a pinned ``<pkg>=<version>`` is present
    # only when that *exact* version is installed (the baseline's
    # ``ensure_packages`` version match), and ``latest`` follows the baseline's
    # legacy behavior: every installed, unpinned package is upgraded
    # without checking whether an upgrade actually exists (the upgrade
    # command reuses ``install`` without ``extra_install_args``, as the baseline
    # builds it). Removing needs root, as do installing and upgrading, so
    # flag the request ``sudo=True`` (or ``su=True``).
    #
    # Deliberate deviations from the baseline: the installed-versions fact is
    # ``AptPackages`` (``apt list --installed``) rather than the baseline's
    # ``DebPackages`` (``dpkg -l``) -- the same substitution ``apt.deb``
    # made, reading the same per-name version information from its
    # ``AptPackageInfo`` entries. The baseline's ``cache_time``
    # option (skip ``update`` when the
    # ``/var/lib/apt/periodic/update-success-stamp`` file is fresh, and
    # ``touch`` it after updating) is deliberately dropped: ``update`` is
    # always stateless, and a ``cache_time`` keyword is absorbed by
    # ``**kwargs`` with no effect. The baseline's ``force`` option
    # (``--force-yes``) is deliberately dropped: the flag is deprecated in
    # modern apt (the same decision as ``apt.deb``). ``default_release`` is
    # a reemote addition beyond the baseline -- the Ansible ``apt`` module's
    # ``default_release``, apt's ``-t <release>`` -- and a first-class
    # alternative to smuggling ``-t`` through ``extra_install_args``: it is
    # rendered on the install command *and*, unlike the raw
    # ``extra_install_args`` the upgrade path drops, carried on the ``latest``
    # upgrade command so release targeting stays consistent and a re-run
    # converges. It scopes which release unpinned candidates resolve from;
    # the ``AptPackages`` presence comparison has no release dimension, so it
    # stays name/version based (the ``dnf.packages`` ``repos`` command-scoping
    # precedent). ``**kwargs`` absorbs
    # any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).

    reasons: list[str] = []

    if update:
        ctx.changed = True
        reasons.append("apt update")
        yield AptUpdateCommand(wait_for_lock=wait_for_lock)

    if upgrade:
        changes = await ctx.facts.get(SimulateOperationWillChange, "upgrade")
        if changes is not None and not any(changes.values()):
            reasons.append("upgrade skipped, no changes would be performed")
        else:
            ctx.changed = True
            if changes is None:
                reasons.append("upgrade simulation failed, will run anyway")
            else:
                reasons.append(
                    f"upgrade will change packages ({changes['upgraded']} upgraded, "
                    f"{changes['newly_installed']} newly installed, "
                    f"{changes['removed']} to remove, "
                    f"{changes['not_upgraded']} not upgraded)"
                )
            yield AptUpgradeCommand(wait_for_lock=wait_for_lock)

    # Normalize: the baseline accepts a bare string, a list, or None.
    if packages is None:
        requested: tuple[str, ...] = ()
    elif isinstance(packages, str):
        requested = (packages,)
    else:
        requested = tuple(packages)

    if requested:
        installed = await ctx.facts.get(AptPackages)
        versions_by_name = {entry["name"]: entry["versions"] for entry in installed}

        def is_installed(package: str) -> bool:
            name, version = _split_version(package)
            versions = versions_by_name.get(name)
            if versions is None:
                return False
            return version is None or version in versions

        if present:
            to_install = tuple(pkg for pkg in requested if not is_installed(pkg))
            # ``latest``: installed, unpinned packages are upgrade candidates.
            to_upgrade = (
                tuple(
                    pkg
                    for pkg in requested
                    if _split_version(pkg)[1] is None and is_installed(pkg)
                )
                if latest
                else ()
            )
            if to_install:
                ctx.changed = True
                reasons.append(f"{len(to_install)} package(s) to install")
                yield AptInstallCommand(
                    packages=to_install,
                    default_release=default_release,
                    no_recommends=no_recommends,
                    allow_downgrades=allow_downgrades,
                    extra_args=extra_install_args,
                    wait_for_lock=wait_for_lock,
                )
            if to_upgrade:
                ctx.changed = True
                reasons.append(f"{len(to_upgrade)} package(s) to upgrade")
                yield AptInstallCommand(
                    packages=to_upgrade,
                    default_release=default_release,
                    no_recommends=no_recommends,
                    allow_downgrades=allow_downgrades,
                    wait_for_lock=wait_for_lock,
                )
            if not to_install and not to_upgrade:
                reasons.append("all packages present")
        else:
            to_remove = tuple(pkg for pkg in requested if is_installed(pkg))
            if to_remove:
                ctx.changed = True
                reasons.append(f"{len(to_remove)} package(s) to remove")
                yield AptRemoveCommand(
                    packages=to_remove,
                    wait_for_lock=wait_for_lock,
                    purge=purge,
                    extra_args=extra_uninstall_args,
                )
            else:
                reasons.append("no packages to remove")
    elif not reasons:
        reasons.append("no packages requested")

    ctx.reason = "; ".join(reasons)


# --- apt.update / apt.upgrade ----------------------------------------------------


async def update(
    ctx: OpContext,
    wait_for_lock: bool | int = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Update apt repositories.

    Args:
        ctx (OpContext): Execution context for the operation.
        wait_for_lock (bool | int, optional): Wait for the apt lock instead of
            failing instantly; ``True`` waits up to 60 seconds, an int sets
            the timeout in seconds. Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated apt update commands.

    **Raises:**

    ```python
    None
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_update.py``.
    """

    # baseline ``apt.update`` parity.
    #
    # This is **non-idempotent**: refreshing the package lists always yields
    # the bare ``apt-get update`` and reports a change, exactly like the
    # stateless ``update=True`` prefix of ``apt.packages``. Updating the
    # lists needs root, so flag the request ``sudo=True`` (or ``su=True``).
    #
    # The baseline's ``cache_time`` option (skip the update when the
    # ``/var/lib/apt/periodic/update-success-stamp`` file is fresh, and
    # ``touch`` it after updating) is deliberately dropped -- the same
    # decision ``apt.packages`` made, since the ``File``/``Date`` fact reads
    # it needs would break the zero-cross-module-import invariant; a
    # ``cache_time`` keyword is absorbed by ``**kwargs`` with no effect.
    # ``**kwargs`` also absorbs any further global arguments (``op()`` already
    # strips ``name``/``sudo``/``su``).
    ctx.changed = True
    ctx.reason = "apt update"
    yield AptUpdateCommand(wait_for_lock=wait_for_lock)


async def upgrade(
    ctx: OpContext,
    wait_for_lock: bool | int = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Upgrade all apt packages.

    Args:
        ctx (OpContext): Execution context for the operation.
        wait_for_lock (bool | int, optional): Wait for the apt lock instead of
            failing instantly; ``True`` waits up to 60 seconds, an int sets
            the timeout in seconds. Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated apt upgrade commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_upgrade.py``.
    """

    # baseline ``apt.upgrade`` parity.
    #
    # The ``SimulateOperationWillChange`` fact simulates ``upgrade`` first,
    # exactly like the ``upgrade=True`` branch of ``apt.packages``: when
    # every proposed count is zero the operation is a no-op, otherwise it
    # yields the ``upgrade`` -- which, unlike ``dist-upgrade``, never
    # installs new dependencies or removes conflicting packages. A failed
    # simulation (None, no summary line) runs the real command anyway,
    # The baseline's fallback: if the dry-run cannot propose changes the
    # operation will probably fail too, and failing on the real command
    # reports the true error. Upgrading needs root, so flag the request
    # ``sudo=True`` (or ``su=True``).
    #
    # The baseline's ``auto_remove`` parameter (appending ``--autoremove`` to the
    # same command) is deliberately dropped in favour of the standalone
    # ``apt.auto_remove`` operation -- the same split ``apt.dist_upgrade``
    # made -- so orphaned dependencies can be cleaned up without also
    # performing an upgrade; this operation never passes ``--autoremove``
    # and an ``auto_remove`` keyword is absorbed by ``**kwargs`` with no
    # effect. ``**kwargs`` also absorbs any further global arguments (``op()``
    # already strips ``name``/``sudo``/``su``).
    changes = await ctx.facts.get(SimulateOperationWillChange, "upgrade")
    if changes is not None and not any(changes.values()):
        ctx.reason = "upgrade skipped, no changes would be performed"
    else:
        ctx.changed = True
        if changes is None:
            ctx.reason = "upgrade simulation failed, will run anyway"
        else:
            ctx.reason = (
                f"upgrade will change packages ({changes['upgraded']} upgraded, "
                f"{changes['newly_installed']} newly installed, "
                f"{changes['removed']} to remove, "
                f"{changes['not_upgraded']} not upgraded)"
            )
        yield AptUpgradeCommand(wait_for_lock=wait_for_lock)


# --- apt.ppa -------------------------------------------------------------------


def _ppa_owner_name(src: str) -> str | None:
    """The ``user/repo`` Launchpad path a ``ppa:user/repo`` name carries.

    ``apt.ppa``'s ``src`` is the full ``ppa:`` token ``apt-add-repository``
    takes; strip the scheme to get the owner/name path the PPA's sources
    entry is keyed on. Returns ``None`` for a token that is not a ``ppa:``
    name or carries no ``user/repo`` path, so ``ppa`` can tell a resolvable
    name from one whose presence it cannot read and fall back to the
    baseline's always-change behaviour for the latter.
    """
    if not src.startswith("ppa:"):
        return None
    owner_name = src[len("ppa:") :].strip("/")
    return owner_name if "/" in owner_name else None


def _source_is_ppa(url: str, owner_name: str) -> bool:
    """Whether a source URL is the Launchpad archive of ``owner_name``'s PPA.

    ``apt-add-repository ppa:user/repo`` writes a sources entry pointing at
    the PPA's Launchpad archive -- historically
    ``http://ppa.launchpad.net/user/repo/ubuntu``, on modern Ubuntu
    ``https://ppa.launchpadcontent.net/user/repo/ubuntu``. Match the host
    (either name, either scheme) and the ``user/repo/ubuntu`` path so the
    presence read is robust across releases instead of pinning one URL.
    """
    parsed = urlparse(url)
    if parsed.netloc not in ("ppa.launchpad.net", "ppa.launchpadcontent.net"):
        return False
    return parsed.path.strip("/").startswith(f"{owner_name}/ubuntu")


@dataclass(frozen=True)
class AptPpaCommand(Command):
    """``apt-add-repository -y [--remove] <src>``: add or remove a PPA.

    The mutation ``apt.ppa`` yields. ``src`` is the full ``ppa:user/repo``
    name, shell-quoted; ``present=False`` inserts ``--remove`` to drop the
    repository instead of adding it. Both directions are the same base
    command, so one descriptor carries the ``present`` flag (like
    ``ModprobeCommand``) rather than two verb descriptors.
    """

    src: str
    present: bool = True

    def shell(self) -> str:
        parts = ["apt-add-repository", "-y"]
        if not self.present:
            parts.append("--remove")
        parts.append(shlex.quote(self.src))
        return " ".join(parts)


async def ppa(
    ctx: OpContext,
    src: str,
    present: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add/remove an Ubuntu PPA repository.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (str): The PPA name, in full ``ppa:user/repo`` format.
        present (bool, optional): Whether the repository should exist.
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated apt PPA repository commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_ppa.py``.
    """

    # baseline ``apt.ppa`` parity.
    #
    # Presence is decided by reading ``AptSources`` and looking for the PPA's
    # Launchpad archive, so the operation is **idempotent** and reports a
    # change only when it actually adds or removes the entry -- unlike the
    # baseline, which reads no fact and always re-runs ``apt-add-repository``.
    # ``_ppa_owner_name`` reduces ``src`` to its ``user/repo`` path and
    # ``_source_is_ppa`` matches that against every configured source's URL
    # (either ``ppa.launchpad.net`` or the modern ``ppa.launchpadcontent.net``
    # host, either scheme), so a PPA already configured in *any* sources file
    # is a no-op for ``present=True`` and an unconfigured one is a no-op for
    # ``present=False``. When the delta is real the operation yields one
    # ``AptPpaCommand``: ``present=True`` runs ``apt-add-repository -y <src>``,
    # ``present=False`` runs ``apt-add-repository -y --remove <src>``. This
    # unblocks the ``register``+``when: ... is changed`` pattern an Ansible
    # ``apt_repository`` migration relies on, which a non-idempotent operation
    # would fire on every run. Adding or removing a repository writes to the
    # root-owned apt sources, so flag the request ``sudo=True`` (or ``su=True``).
    #
    # A ``src`` that is not a resolvable ``ppa:user/repo`` name cannot be
    # proven present or absent, so it falls through to the baseline's
    # always-change behaviour rather than silently skipping a legitimate add
    # or remove. Requires ``apt-add-repository`` (from
    # ``software-properties-common``) on the target host; the baseline's
    # ``requires_command`` gating is deliberately dropped, so a missing binary
    # exits 127 -- reported as ``CommandNotFoundError``. ``**kwargs`` absorbs
    # any further global arguments (``op()`` already strips ``name``/``sudo``/
    # ``su``).
    #
    # Deliberately NOT spool-and-install: the payload is a CLI invocation, not
    # the file's text -- ``apt-add-repository`` owns the sources file and also
    # fetches the PPA's Launchpad signing key, adds the deb-src entry and
    # resolves the distro codename, none of which a hand-rolled content
    # transform can reproduce. The spool family is for whole-content edits of
    # a protected file (``apt.repo`` is that shape here); a tool-owned
    # mutation stays in place, like ``git.config``.
    sources = await ctx.facts.get(AptSources)
    owner_name = _ppa_owner_name(src)
    if owner_name is not None:
        is_present = any(_source_is_ppa(source["url"], owner_name) for source in sources)
        if present and is_present:
            ctx.reason = f"ppa {src} exists"
            return
        if not present and not is_present:
            ctx.reason = f"ppa {src} does not exist"
            return

    ctx.changed = True
    if present:
        ctx.reason = f"ppa {src} will be added"
    else:
        ctx.reason = f"ppa {src} will be removed"
    yield AptPpaCommand(src=src, present=present)


# --- apt.repo ------------------------------------------------------------------


def _repo_line_pattern(line: str) -> str:
    """The anchored, escaped whole-line regex for an apt source line.

    A port of the baseline's ``adjust_regex(line, escape_regex_characters=True)``
    as ``apt.repo`` uses it: every regex metacharacter in ``line`` is
    escaped so the source string matches literally, then the pattern is
    anchored with ``^.*`` / ``.*$`` so a line carrying extra surrounding
    text still matches. Both content transforms below share it, so the
    append never duplicates a line the file already carries and the
    removal deletes exactly the lines the append would have skipped.
    """
    escaped = re.sub(r"([\.\\\+\*\?\[\^\]\$\(\)\{\}\-])", r"\\\1", line)
    return f"^.*{escaped}.*$"


def _repo_spool_path(dest: str) -> str:
    """The deterministic ``/tmp`` spool *file* path ``apt.repo`` uploads to.

    Derived from the managed sources file's sha1 (``files.install``'s
    ``_install_spool_path`` pattern), because ``apt.repo`` edits *several*
    distinct files -- the shared ``/etc/apt/sources.list`` and any number of
    ``/etc/apt/sources.list.d/<filename>.list`` -- and two repository edits
    naming different files in one deploy must not clobber each other's
    spool. The content is spooled to a fixed ``repo`` file inside that
    directory rather than at the top level of ``/tmp``, and the elevated
    install ``rm -rf``s the whole directory afterwards, so no leftover lands
    in ``/tmp``. The same ``dest`` always yields the same name in both
    phases, so the descriptors stay deterministic. The name avoids
    *collisions*, not a hostile local user: it is predictable, so on a
    multi-tenant host the ``/tmp`` spool is still a symlink-race exposure
    (the trade-off every spool-and-install operation here makes).
    """
    digest = hashlib.sha1(dest.encode()).hexdigest()[:16]
    return f"/tmp/reemote-apt-repo-{digest}/repo"


def _add_repo_line(content: str | None, line: str) -> str:
    """The pure append transform: ``content`` carrying ``line`` exactly once.

    ``apt.repo(present=True)``'s edit of the managed file's current text:
    an absent file (``None``) becomes the single line, a file whose text
    already holds a matching line is returned verbatim -- the baseline's
    ``files.line`` match semantics through the anchored pattern, so even a
    line the ``AptSources`` parser cannot read is never duplicated -- and
    otherwise the line is appended, keeping the file's own newline endings.
    Idempotent (``f(f(x)) == f(x)``) and side-effect free, so both deploy
    phases compute the same content from the same fact value.
    """
    if not content:
        return f"{line}\n"
    regex = re.compile(_repo_line_pattern(line))
    if any(regex.match(row) for row in content.splitlines()):
        return content
    separator = "" if content.endswith("\n") else "\n"
    return f"{content}{separator}{line}\n"


def _remove_repo_line(content: str | None, line: str) -> str:
    """The pure remove transform: ``content`` without any line matching ``line``.

    ``apt.repo(present=False)``'s edit: every line the anchored, escaped
    pattern matches is dropped -- the baseline's ``files.line`` deletion
    semantics, so a line carrying extra surrounding text goes too -- while
    the remaining lines keep their order and the file's trailing newline.
    An absent file and a file with no match come back unchanged (empty),
    which is what makes the transform a fixpoint.
    """
    if not content:
        return content or ""
    regex = re.compile(_repo_line_pattern(line))
    kept = [row for row in content.splitlines() if regex.match(row) is None]
    if not kept:
        return ""
    new = "\n".join(kept)
    if content.endswith("\n"):
        new += "\n"
    return new


@dataclass(frozen=True)
class AptRepoUploadCommand(TransferCommand):
    """Spool the transformed sources content to a ``/tmp`` path on the target.

    The first half of ``apt.repo``'s write and a *non-shell* descriptor: it
    copies the content over the connection's scp channel via
    ``Host.upload_file`` rather than running shell text, so it has no
    ``shell()`` and its loggable form is the ``__str__`` rendering
    ``command_shell`` falls back to. Like ``AptSourcesFileUploadCommand`` the
    content is not a caller-supplied stream but the whole sources text the
    operation just derived from the file's current content, so the field is
    a plain ``str`` -- hashable and identical across phases -- wrapped in a
    fresh ``io.BytesIO`` only at transfer time. The copy rides as the login
    user (transfers are unprivileged, so the request's ``sudo`` does not
    apply to it), which is why the spool path is world-writable ``/tmp``
    rather than the root-owned apt directory.
    """

    content: str  # the whole transformed sources text
    dest: str  # spool path on the target host

    def __str__(self) -> str:
        return f"scp <apt sources> -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        # scp does not create parent directories, and the spool now lives
        # inside a sha1-derived /tmp directory rather than at the top level,
        # so mkdir -p it first. Unprivileged: /tmp is world-writable, so the
        # login user can create the directory, and a transfer never elevates.
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        await host.run_shell(f"mkdir -p {parent}")
        await host.upload_file(io.BytesIO(self.content.encode()), self.dest)


@dataclass(frozen=True)
class AptRepoInstallCommand(Command):
    """Install the spooled sources file ``0644``, then remove the spool file.

    The second half of ``apt.repo``'s write and the elevated one: writing
    under ``/etc/apt`` needs root, so the request is flagged ``sudo=True``
    and the whole sequence is wrapped in ``sh -c`` -- a bare ``sudo`` prefix
    would elevate only the first command of the ``&&`` chain and leave the
    cleanup running as the login user, whereas ``sudo sh -c '...'`` runs
    every step as root, which can always remove the login-user spool file.
    The sequence creates the destination directory (``sources.list.d`` may
    not exist yet), copies the spooled content over the target file, chmods
    it ``0644`` (the baseline's fixed mode for apt sources, and the reset the
    overlay needs because a ``cp`` would otherwise leave the login-user
    spool's ownership and mode on a stock system file) and removes the
    spool -- the ``AptSourcesFileInstallCommand`` pattern. All paths are
    shell-quoted.
    """

    spool: str  # where AptRepoUploadCommand wrote the content
    dest: str  # the /etc/apt/sources.list or sources.list.d/<name>.list path

    def shell(self) -> str:
        spool = shlex.quote(self.spool)
        # The spool file lives inside a sha1-derived /tmp directory; remove
        # the whole directory so no leftover lands in /tmp.
        spool_dir = shlex.quote(str(PurePosixPath(self.spool).parent))
        dest = shlex.quote(self.dest)
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        inner = (
            f"mkdir -p {parent} && "
            f"cp {spool} {dest} && "
            f"chmod 0644 {dest} && rm -rf {spool_dir}"
        )
        return f"sh -c {shlex.quote(inner)}"


async def repo(
    ctx: OpContext,
    src: str,
    present: bool = True,
    filename: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add/remove an apt repository line.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (str): Apt source string, e.g. ``deb http://X hardy main``.
        present (bool, optional): Whether the repository should exist on the
            system. Defaults to True.
        filename (str | None, optional): Base name under
            ``/etc/apt/sources.list.d/`` (the ``.list`` extension is appended);
            by default ``/etc/apt/sources.list`` is managed. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated apt repository line commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_repo.py``.
    """

    # baseline ``apt.repo`` parity, spool-and-install write family.
    #
    # Presence is decided by parsing ``src`` with ``AptSources``' legacy
    # line parser and comparing against the fact's entries -- the baseline's
    # ``parse_apt_repo(src) in AptSources`` check -- so a repository already
    # configured in *any* sources file (legacy or deb822) is a no-op, and an
    # unparseable ``src`` counts as absent but is still appended when
    # ``present=True`` (the baseline's tolerance). Either direction then *rewrites
    # the managed file* instead of patching it in place: the file's current
    # text is read with the in-namespace ``AptSourcesFile`` content fact (the
    # ``DnfRepoFile`` pattern, path-generic), one pure transform of that text
    # computes the desired content -- ``_add_repo_line`` appends the line once,
    # ``_remove_repo_line`` drops every line the anchored pattern matches --
    # and when the result differs from what is there the request yields *two*
    # descriptors: an ``AptRepoUploadCommand`` transfer spooling the new text
    # to a ``/tmp`` path over scp, then an ``AptRepoInstallCommand`` running
    # the elevated install that overlays the protected file and removes the
    # spool (the ``apt.key``/``apt.sources_file``/``dnf.repo`` pattern).
    # Writing the apt sources needs root, so flag the request ``sudo=True``
    # (or ``su=True``); the transfer stays unprivileged regardless.
    #
    # Deliberate deviations from the baseline: the mutation rides self-contained
    # apt descriptors instead of delegating to ``files.line`` (reemote has
    # no line operation -- ``files.edit`` is its generic editing primitive --
    # and namespaces never reuse each other's operations), so it belongs to
    # the spool-and-install family rather than the in-place one of the baseline's
    # ``echo >>``/``sed -i``. That also drops the baseline's timestamped sed backup
    # dance (its timestamp is non-deterministic across phases) and keeps the
    # file's other lines, comments and blank lines exactly as they were,
    # because the transform is line-based over the real content. Two further
    # content-level no-ops round out the baseline's fact-level ones: an add whose
    # line the file already carries (an unparseable ``src`` repeats cleanly)
    # and a remove of a repository configured in some *other* sources file.
    # ``**kwargs`` absorbs any further global arguments (``op()`` already
    # strips ``name``/``sudo``/``su``).
    path = f"/etc/apt/sources.list.d/{filename}.list" if filename else "/etc/apt/sources.list"
    sources = await ctx.facts.get(AptSources)
    parsed = AptSources._parse_list_line(src)
    is_present = parsed is not None and parsed in sources

    if present:
        if is_present:
            ctx.reason = f'apt repo "{src}" exists'
            return
    elif not is_present:
        ctx.reason = f'apt repo "{src}" does not exist'
        return

    current = await ctx.facts.get(AptSourcesFile, path)
    new = _add_repo_line(current, src) if present else _remove_repo_line(current, src)
    if new == (current or ""):
        # The fact wanted an edit the file's own text says is unnecessary.
        ctx.reason = (
            f'apt repo "{src}" is already listed in {path}'
            if present
            else f'apt repo "{src}" is not configured in {path}'
        )
        return

    ctx.changed = True
    spool = _repo_spool_path(path)
    ctx.reason = (
        f'apt repo "{src}" missing, will be added to {path}'
        if present
        else f'apt repo "{src}" present, will be removed from {path}'
    )
    yield AptRepoUploadCommand(content=new, dest=spool)
    yield AptRepoInstallCommand(spool=spool, dest=path)


# --- apt.sources_file ----------------------------------------------------------


def _sources_spool_path(dest: str) -> str:
    """The deterministic ``/tmp`` spool *file* path ``apt.sources_file`` writes to.

    Derived from the managed ``.sources`` file's sha1 so two sources files
    written in one deploy never clobber each other's spool, while the same
    ``dest`` always yields the same name in both phases -- the request
    sequence stays deterministic. The generated deb822 text is spooled to a
    fixed ``sources`` file inside that directory rather than at the top level
    of ``/tmp``, and the elevated install ``rm -rf``s the whole directory
    afterwards, so no leftover lands in ``/tmp``. The name avoids
    *collisions*, not a hostile local user: it is predictable, so on a
    multi-tenant host the ``/tmp`` spool is still a symlink-race exposure
    (the trade-off every spool-and-install operation here makes).
    """
    digest = hashlib.sha1(dest.encode()).hexdigest()[:16]
    return f"/tmp/reemote-apt-sources-file-{digest}/sources"


def _as_list(val: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """Normalize a multi-value ``apt.sources_file`` field to a list.

    The baseline's inline ``_as_list``: None reads as ``[]``, a bare string
    splits on whitespace (so ``"deb deb-src"`` becomes two types) and an
    iterable is copied into a list.
    """
    if val is None:
        return []
    if isinstance(val, str):
        return val.split()
    return list(val)


@dataclass(frozen=True)
class AptSourcesFileUploadCommand(TransferCommand):
    """Spool the generated deb822 content to a ``/tmp`` path on the target.

    The first half of ``apt.sources_file``'s write direction and a
    *non-shell* descriptor: it copies the content over the connection's scp
    channel via ``Host.upload_file`` rather than running shell text, so it
    has no ``shell()`` and its loggable form is the ``__str__`` rendering
    ``command_shell`` falls back to. Unlike ``AptKeyUploadCommand`` the
    content is not a caller-supplied stream but the deb822 text the
    operation itself generates, so the field is a plain ``str`` -- hashable
    and identical across phases -- wrapped in a fresh ``io.BytesIO`` only
    at transfer time. The copy rides as the login user (transfers are
    unprivileged, so the request's ``sudo`` does not apply to it), which is
    why the spool path is world-writable ``/tmp`` rather than the
    root-owned ``sources.list.d`` directory.
    """

    content: str  # the deb822 sources text the operation generated
    dest: str  # spool path on the target host

    def __str__(self) -> str:
        return f"scp <deb822 sources> -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        # scp does not create parent directories, and the spool now lives
        # inside a sha1-derived /tmp directory rather than at the top level,
        # so mkdir -p it first. Unprivileged: /tmp is world-writable, so the
        # login user can create the directory, and a transfer never elevates.
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        await host.run_shell(f"mkdir -p {parent}")
        await host.upload_file(io.BytesIO(self.content.encode()), self.dest)


@dataclass(frozen=True)
class AptSourcesFileInstallCommand(Command):
    """Install the spooled deb822 file ``0644``, then remove the spool file.

    The second half of ``apt.sources_file``'s write direction and the
    elevated one: writing under ``/etc/apt/sources.list.d/`` needs root, so
    the request is flagged ``sudo=True`` and the whole sequence is wrapped
    in ``sh -c`` -- a bare ``sudo`` prefix would elevate only the first
    command of the ``&&`` chain and leave the cleanup running as the login
    user, whereas ``sudo sh -c '...'`` runs every step as root, which can
    always remove the login-user spool file. The sequence creates the
    destination directory, copies the spooled content over the target
    file, chmods it ``0644`` (the baseline's fixed ``mode`` for the sources
    file) and removes the spool file -- the ``AptKeyInstallCommand``
    pattern without the key-content sniff. All paths are shell-quoted.
    """

    spool: str  # where AptSourcesFileUploadCommand wrote the content
    dest: str  # the /etc/apt/sources.list.d/<filename>.sources path

    def shell(self) -> str:
        spool = shlex.quote(self.spool)
        # The spool file lives inside a sha1-derived /tmp directory; remove
        # the whole directory so no leftover lands in /tmp.
        spool_dir = shlex.quote(str(PurePosixPath(self.spool).parent))
        dest = shlex.quote(self.dest)
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        inner = (
            f"mkdir -p {parent} && "
            f"cp {spool} {dest} && "
            f"chmod 0644 {dest} && rm -rf {spool_dir}"
        )
        return f"sh -c {shlex.quote(inner)}"


@dataclass(frozen=True)
class AptSourcesFileRemoveCommand(Command):
    """``rm -f <path>``: delete a deb822 sources file.

    The removal mutation ``apt.sources_file(present=False)`` yields, only
    when the ``AptSourcesFile`` fact reports the file present -- the same
    shell the baseline's ``files.file(present=False)`` delegation renders.
    ``rm -f`` tolerates an absent path, and deleting under
    ``sources.list.d`` needs root, so flag the request ``sudo=True`` (or
    ``su=True``).
    """

    path: str

    def shell(self) -> str:
        return f"rm -f {shlex.quote(self.path)}"


async def sources_file(
    ctx: OpContext,
    filename: str,
    types: str | list[str] | tuple[str, ...] = "deb",
    uris: str | list[str] | tuple[str, ...] | None = None,
    suites: str | list[str] | tuple[str, ...] | None = None,
    components: str | list[str] | tuple[str, ...] | None = None,
    architectures: str | list[str] | tuple[str, ...] | None = None,
    signed_by: str | None = None,
    present: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Manage a deb822 ``.sources`` file.

    Args:
        ctx (OpContext): Execution context for the operation.
        filename (str): Base name for the file (without extension); the
            ``.sources`` extension is added automatically under
            ``/etc/apt/sources.list.d/``.
        types (str | list[str] | tuple[str, ...], optional): Repository
            type(s) -- ``"deb"``, ``"deb-src"``, or both. Defaults to "deb".
        uris (str | list[str] | tuple[str, ...] | None, optional): One or more
            repository URLs. Defaults to None.
        suites (str | list[str] | tuple[str, ...] | None, optional): One or
            more suite/distribution names (e.g. ``"bookworm"``). Defaults to
            None.
        components (str | list[str] | tuple[str, ...] | None, optional): One
            or more components (e.g. ``["main", "contrib"]``). Defaults to
            None.
        architectures (str | list[str] | tuple[str, ...] | None, optional):
            Restrict to specific architectures (e.g. ``"amd64"``). Defaults
            to None.
        signed_by (str | None, optional): Absolute path to the keyring file
            used for signature verification. Defaults to None.
        present (bool, optional): Whether the sources file should exist.
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated apt deb822 sources file commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/apt/apt_sources_file.py``.
    """

    # baseline ``apt.sources_file`` parity.
    #
    # Each multi-value field accepts a list, a tuple or a space-separated
    # string (the baseline's normalization). ``present=True`` requires at least
    # one URI and one suite -- otherwise ``ValueError`` is raised with
    # The baseline's message (the plain ``ValueError`` the sibling operations
    # raise for argument validation) -- and is **idempotent** on
    # ``AptSources``: the
    # fields expand into one desired ``AptSource`` dict per type/URI/suite
    # combination (list-valued ``arch``/``signed-by`` options, the fact's
    # shape), and when every combination is already configured the
    # operation is a no-op. Otherwise it yields *two* descriptors from the
    # one request -- an ``AptSourcesFileUploadCommand`` transfer spooling
    # the generated deb822 text to a ``/tmp`` path over scp, then an
    # ``AptSourcesFileInstallCommand`` running the elevated install: create
    # the directory, copy the file, chmod it ``0644`` and remove the spool.
    # Writing the apt sources needs root, so flag the request ``sudo=True``
    # (or ``su=True``); the transfer stays unprivileged regardless.
    #
    # ``present=False`` is **idempotent** on the in-namespace
    # ``AptSourcesFile`` content fact (the ``DnfRepoFile`` pattern): an
    # absent file is a no-op, an existing one -- empty or not -- yields one
    # ``AptSourcesFileRemoveCommand`` (``rm -f``). The baseline checks its
    # ``files.File`` fact for the same guard; the in-namespace content fact
    # provides it without a cross-module fact import.
    #
    # Deliberate deviations from the baseline: the write rides the spool-and-install
    # descriptor pair instead of delegating to ``files.put`` (reemote's
    # transfers are unprivileged scp and namespaces never reuse each other's
    # operations -- the ``apt.key`` pattern), with the ``0644`` mode kept as
    # The baseline hard-codes it. ``**kwargs`` absorbs any further global arguments
    # (``op()`` already strips ``name``/``sudo``/``su``).
    dest = f"/etc/apt/sources.list.d/{filename}.sources"

    # Removal path -- idempotent on the file's existence (DnfRepoFile pattern).
    if not present:
        current = await ctx.facts.get(AptSourcesFile, dest)
        if current is None:
            ctx.reason = f'apt sources file "{dest}" already absent'
        else:
            ctx.changed = True
            ctx.reason = f'apt sources file "{dest}" will be removed'
            yield AptSourcesFileRemoveCommand(path=dest)
        return

    types_list = _as_list(types) or ["deb"]
    uris_list = _as_list(uris)
    suites_list = _as_list(suites)
    components_list = _as_list(components)
    architectures_list = _as_list(architectures)

    if not uris_list or not suites_list:
        raise ValueError("apt.sources_file requires at least one URI and one suite")

    # Expand into the AptSources fact's shape (list-valued options) so the
    # idempotence check is plain dict membership -- the baseline's
    # AptSourcesFile.expand_to_repos() in this namespace's entry shape.
    options: dict[str, list[str]] = {}
    if architectures_list:
        options["arch"] = architectures_list
    if signed_by:
        options["signed-by"] = [signed_by]
    desired: list[AptSource] = [
        {
            "type": repo_type,
            "url": uri,
            "distribution": suite,
            "components": list(components_list),
            "options": {key: list(value) for key, value in options.items()},
        }
        for repo_type in types_list
        for uri in uris_list
        for suite in suites_list
    ]
    sources = await ctx.facts.get(AptSources)
    if all(repo in sources for repo in desired):
        ctx.reason = f'apt sources file "{dest}" is already configured'
        return

    # Build the deb822 content, the baseline's field order and spacing.
    lines = [
        f"Types: {' '.join(types_list)}",
        f"URIs: {' '.join(uris_list)}",
        f"Suites: {' '.join(suites_list)}",
    ]
    if components_list:
        lines.append(f"Components: {' '.join(components_list)}")
    if architectures_list:
        lines.append(f"Architectures: {' '.join(architectures_list)}")
    if signed_by:
        lines.append(f"Signed-By: {signed_by}")
    content = "\n".join(lines) + "\n"

    ctx.changed = True
    ctx.reason = f'apt sources file "{dest}" missing or outdated, will be written'
    spool = _sources_spool_path(dest)
    yield AptSourcesFileUploadCommand(content=content, dest=spool)
    yield AptSourcesFileInstallCommand(spool=spool, dest=dest)


__all__ = [
    "AptAutoRemoveCommand",
    "AptAutoremovable",
    "AptDistUpgradeCommand",
    "AptHeldPackages",
    "AptInstallCommand",
    "AptInstallFixCommand",
    "AptKey",
    "AptKeyInstallCommand",
    "AptKeyUploadCommand",
    "AptKeys",
    "AptMarkHoldCommand",
    "AptMarkUnholdCommand",
    "AptPackageInfo",
    "AptPackagePolicy",
    "AptPackageUpgrade",
    "AptPackages",
    "AptPolicy",
    "AptPolicyVersion",
    "AptPpaCommand",
    "AptRemoveCommand",
    "AptRepoInstallCommand",
    "AptRepoUploadCommand",
    "AptSimulationDict",
    "AptSource",
    "AptSources",
    "AptSourcesFile",
    "AptSourcesFileInstallCommand",
    "AptSourcesFileRemoveCommand",
    "AptSourcesFileUploadCommand",
    "AptUpdateCommand",
    "AptUpgradable",
    "AptUpgradeCommand",
    "DebInstallCommand",
    "DebPackage",
    "DebPackageInfo",
    "SimulateOperationWillChange",
    "auto_remove",
    "deb",
    "dist_upgrade",
    "key",
    "mark",
    "packages",
    "ppa",
    "repo",
    "sources_file",
    "update",
    "upgrade",
]