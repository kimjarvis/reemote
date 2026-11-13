# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: download a URL to a root-owned path with files.download.

This is a *usage* example, not part of the library. ``files.download`` is the
general download-to-path operation every package-repository role keeps needing:
it takes an ``http(s)`` URL and lands its body at a path the login user cannot
write directly (anything under ``/etc``), in ONE ``sudo=True`` request. It
retires the hand-rolled ``urllib.request`` + ``io.BytesIO`` workaround that
``dnf.key``/``apt.key``/``gpg.key`` force -- each of those *rejects* a URL
``src`` and pushes the fetch onto the caller -- and it is the operation
``dnf.repo`` names in its "reemote has no download operation" refusal.

The Docker CE role is the canonical case, and it hits the gap *twice*: it pulls
both the ``.repo`` stanza and the ASCII-armored GPG key from
``download.docker.com``. Ansible spells that as two ``get_url`` tasks (plus an
``rpm_key`` import); here both artifacts are text, so two ``files.download``
requests land them root-owned and converged:

1. ``docker-ce.repo`` -> ``/etc/yum.repos.d/docker-ce.repo``
2. the GPG key -> ``/etc/pki/rpm-gpg/RPM-GPG-KEY-DOCKER`` (the file the
   downloaded ``.repo``'s ``gpgkey=`` line points dnf at)

How it works -- and why it is one request, not two runs:

* **Control-side fetch.** The *control* machine opens the connection
  (``urllib`` in a worker thread), not the target host, so the download works
  when only the control side has internet egress and the host never needs
  ``curl``/``wget``. The cost is that the operation body runs once per phase
  (prepare, then apply), so the URL is fetched twice per host -- harmless for
  these small text artifacts.
* **Delegated write.** The body decodes the fetched bytes to text and hands the
  string to ``files.install``, so a download inherits install's content-only
  idempotence (a converged file yields zero commands), its dest-derived ``/tmp``
  spool, its owner/mode handling, its optional ``validate`` hook and its
  two-privilege-level signature: an *unprivileged* scp transfer of the spool
  plus an *elevated* ``sh -c`` install (``mkdir -p``/``cp``/optional
  ``chown``/``chmod``/``rm -rf``). ``sudo=True`` elevates only the install half.
* **Text only.** ``install`` takes ``content: str`` and ``FileContents`` compares
  text, so a binary body (a ``.rpm``, a tarball) raises ``ValueError`` rather
  than corrupting the write. A binary download stays ``files.put``'s job: fetch
  it control-side into an ``io.BytesIO`` and put it into a login-writable path.

Reproducibility: pass ``checksum="sha256:<hexdigest>"`` (Ansible's ``get_url``
spelling) to verify the fetched bytes before anything is written. Pinning it is
also what makes the two-phase fetch deterministic -- both phases verify the same
digest, so the plan and the apply install byte-identical content. This example
omits it so it stays runnable against the live Docker URLs without a hardcoded
digest that would rot; add it in production from the vendor's published value.

The script downloads both artifacts, then runs the very same deploy a second
time: the first run writes, the second reports no change on every host (the
fetched text now equals what ``FileContents`` reads back).

Every request carries ``when=is_centos``, the declarative eligibility guard: the
predicate reads only **static** inventory data -- ``host.group``, set by the
``"group"`` key of the inventory entry -- and is a stable module-level function,
so the prepare and apply phases reach the same skip decision. On hosts outside
the ``"centos"`` group neither the fetch nor the install runs, and plan and log
render the step as ``[skipped: when guard is_centos]`` in its lockstep slot.

The path is resolved from ``__file__``, so the example works from any working
directory; point ``INVENTORY_PATH`` at your own file (or edit ``inventory.json``)
to target your machines.

Run it with::

    uv run python examples/modules/files/files_download.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    files,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# The two Docker CE artifacts the role pulls from download.docker.com. Both are
# text -- the .repo stanza and the ASCII-armored GPG key -- so both install
# through files.download. The URLs are plain hashable strs, equal across phases.
REPO_URL = "https://download.docker.com/linux/centos/docker-ce.repo"
REPO_DEST = "/etc/yum.repos.d/docker-ce.repo"
KEY_URL = "https://download.docker.com/linux/centos/gpg"
KEY_DEST = "/etc/pki/rpm-gpg/RPM-GPG-KEY-DOCKER"

# examples/modules/files/files_download.py -> the project root, so the inventory
# is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to materialize into hosts; raises ``InventoryError`` (via
    ``validate_inventory``) when the document is not an array or an entry
    does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_centos(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"centos"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.group == "centos"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Download the Docker CE repo stanza and GPG key to their root-owned paths.

    ``sudo=True`` elevates only the install half -- the scp spool transfer
    always rides as the login user, and the fetch happens control-side before
    either. The mode defaults to "0644", the standard for both a yum repo file
    and a world-readable GPG key. Add ``checksum="sha256:<hex>"`` to pin each
    artifact in production. Importing the key into the rpm keyring is a
    separate ``dnf.key`` request, which takes the key *content* as a
    control-side IO stream (``src=io.BytesIO(...)``) -- not this downloaded
    path, since ``dnf.key`` rejects a filename/URL ``src``.
    """
    yield op(
        files.download,
        name="Download Docker CE repo",
        url=REPO_URL,
        dest=REPO_DEST,
        sudo=True,
        when=is_centos,
    )
    yield op(
        files.download,
        name="Download Docker CE GPG key",
        url=KEY_URL,
        dest=KEY_DEST,
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Dry run: the plan materializes against pre-deployment state. The
        # fetch still happens (prepare runs the body) but nothing is sent.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan: download Docker CE repo + key (--dry) ==")
        print(format_plan(plans))

        # First real run: fetch control-side, spool + install on every host
        # whose destination content differs.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Log: download Docker CE repo + key (first run) ==")
        assert report is not None
        print(format_log(report))

        # Second run of the SAME deploy: FileContents now reads back what the
        # first run installed, so every request reports no change.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Log: download Docker CE repo + key (second run, no-op) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing/unreadable/not valid JSON, or the
        # control-side fetch failed (an unreachable URL or a non-2xx response
        # raises a URLError/HTTPError, both OSError subclasses). Python's own
        # message already names the file or URL and the reason, so print just
        # that -- no traceback worth showing.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema. The
        # message names the offending entry by its list index, which is the
        # whole report -- and it fails before any host is materialized.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
