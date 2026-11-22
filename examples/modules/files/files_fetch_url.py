# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: fetch a URL's content control-side with files.fetch_url.

This is a *usage* example, not part of the library. ``files.fetch_url`` is the
control-side GET helper -- Ansible's ``uri`` with ``return_content: true``
mapped to a plain coroutine: it fetches an ``http(s)`` URL on the *control*
machine and hands the response body back to the program as text, instead of
installing it at a remote path (that is its sibling ``files.download``'s job,
which shares the scheme guard and checksum contract through the same
verified-fetch seam -- and, unlike this helper, also installs a binary body).

The geerlingguy.composer role is the canonical case: a ``uri`` task fetches
the Composer installer's SHA-384 signature from ``composer.github.io`` and
registers its content, then a ``get_url`` task downloads the installer pinned
by ``checksum: "sha384:{{ composer_installer_signature.content }}"``. The
``get_url`` half maps to ``files.download(checksum=...)``; the ``uri`` half is
this helper:

1. ``main()`` awaits ``files.fetch_url(SIG_URL)`` -- once per program run --
   and prints the signature (the ``return_content`` payoff: the body is a
   plain ``str`` the program can branch on, print, or freeze into a plan).
2. The signature is frozen into the deploy through the ``build_installer``
   factory, which yields one ``files.download`` request whose
   ``checksum=f"sha384:{signature}"`` pins the installer bytes.

How it works -- and why the fetch lives in ``main()``, not in the deploy:

* **Not an operation.** ``fetch_url`` takes no ``OpContext`` and is never
  passed to ``op()``; it is control-side code like the inventory load. An
  operation body runs once per phase per host, so a fetch inside a deploy
  would re-run per phase -- the helper runs exactly when awaited, and the
  determinism recipe is to fetch once and freeze the text into the per-host
  plan the deploy consumes. The checksum pin then makes both phases verify
  the same digest and install byte-identical content.
* **Control-side fetch.** The *control* machine opens the connection
  (``urllib`` in a worker thread), not the target host, so the fetch works
  when only the control side has internet egress and the host never needs
  ``curl``/``wget``. Two faithful deviations from Ansible: ``uri`` runs on
  each target (here the controller fetches for all targets -- the same HTTPS
  trust model), and Ansible re-fetches per host per run (here once per run).
* **Text only.** The body is decoded as UTF-8; a binary response raises
  ``ValueError`` -- installing one at a path is ``files.download``'s or
  ``files.install(content=<bytes>)``'s job. A ``file:``/``ftp:``
  scheme raises before any fetch, and an optional Ansible-style
  ``checksum="<algorithm>:<hexdigest>"`` is verified before the caller sees
  the content.

The installer lands at ``/tmp/composer-installer.php`` with mode ``0755`` --
a login-writable path, so no ``sudo`` is needed (a root-owned destination
would carry ``sudo=True`` like the ``files_download`` sibling example). The
script runs the same deploy twice: the first run writes, the second reports
no change on every host. A real role would gate the fetch on a gathered
"composer already installed" fact (the role's ``when:
not composer_bin.stat.exists``); this usage example fetches unconditionally
to demonstrate the helper. On-target HTTP -- a POST, a webhook, anything
that must run *on the host* -- stays a ``Shell`` action outside the deploy.

The inventory is read from ``inventory.json`` in the current working directory; edit it
there to target your machines.

Run it with::

    uv run python examples/modules/files/files_fetch_url.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path

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

# The two Composer URLs the role uses: the signature (a small text file whose
# content IS the sha384 hexdigest of the installer) and the installer itself
# (a PHP script -- text). Both are plain hashable strs, equal across phases.
SIG_URL = "https://composer.github.io/installer.sig"
INSTALLER_URL = "https://getcomposer.org/installer"
INSTALLER_DEST = "/tmp/composer-installer.php"

def build_installer(signature: str):
    """Freeze the fetched signature into the deploy that downloads the installer.

    The factory closes over the *fetched* text -- a plain hashable ``str``
    baked into the checksum pin -- so the deploy is a pure function of its
    host and both phases build identical requests. This is the
    fetch-once-then-freeze recipe: ``main()`` awaits ``files.fetch_url`` once
    and hands the result here, never the other way around.
    """

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.download,
            name="Download Composer installer",
            url=INSTALLER_URL,
            dest=INSTALLER_DEST,
            mode="0755",
            checksum=f"sha384:{signature}",
        )

    return deploy


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # The uri return_content half: one control-side GET, awaited here in
        # main(), hands the signature back to the program. The trailing
        # newline the server sends is stripped before freezing the digest.
        signature = (await files.fetch_url(SIG_URL)).strip()
        print(f"\n== Composer installer signature (sha384, fetched control-side) ==\n{signature}")

        deploy = build_installer(signature)

        # Dry run: the plan materializes against pre-deployment state. The
        # download's fetch still happens (prepare runs the body) but nothing
        # is sent.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan: download the pinned Composer installer (--dry) ==")
        print(format_plan(plans))

        # First real run: the checksum-pinned installer lands at /tmp on
        # every host whose destination content differs.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Log: download the pinned Composer installer (first run) ==")
        assert report is not None
        print(format_log(report))

        # Second run of the SAME deploy: FileContents now reads back what the
        # first run installed, so every request reports no change.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Log: download the pinned Composer installer (second run, no-op) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing/unreadable/not valid JSON, or a
        # control-side fetch failed (an unreachable URL or a non-2xx response
        # raises a URLError/HTTPError, both OSError subclasses). Python's own
        # message already names the file or URL and the reason, so print just
        # that -- no traceback worth showing.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except ValueError as exc:
        # The checksum pin rejected the fetched bytes -- the publisher rotated
        # the installer between the signature fetch and the download (the same
        # race the Ansible role has), or the signature URL served something
        # unexpected. Nothing was written; re-run to fetch a fresh pair.
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