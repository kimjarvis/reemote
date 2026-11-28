# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: manage htpasswd entries via files.htpasswd.

This is a *usage* example, not part of the library. ``files.htpasswd`` is the
counterpart of Ansible's ``community.general.htpasswd`` module: it adds, updates
or removes **one** ``name:hash`` line of an Apache/Nginx htpasswd file, leaving
every other line of that file alone.

Like ``files.transform``, the write goes the spool-and-install way, which is
what lets one request converge the usual protected file:

1. Read: the operation reads the file's current text with the ``FileContents``
   fact (``None`` when absent). A ``0640`` file the login user cannot read is
   re-read elevated, so a restrictive mode still converges.
2. Edit: ``htpasswd_entry`` produces the desired text control-side. A managed
   line whose stored hash already *verifies* the wanted password is reproduced
   byte-for-byte; only a new or stale line is re-hashed with a fresh salt.
3. Install: the write is delegated to ``files.install`` -- an unprivileged scp
   spools the text to a dest-derived ``/tmp`` path, then one elevated
   ``sh -c`` installs it with ``mkdir -p``/``chown``/``chmod``/``rm -rf``.

Two properties are worth knowing before using it:

* **The hashing happens on the control machine**, in pure Python: ``$apr1$``
  (Apache's MD5-crypt), ``$1$``, ``{SHA}``, ``$2y$`` bcrypt and traditional DES.
  The target needs no ``htpasswd(1)``, no passlib and no ``crypt()`` (removed in
  Python 3.13), and because the hash -- not the password -- is what lands in the
  file, and the file rides a spool descriptor, **no password or hash ever
  appears in a plan or log line**.
* **Idempotence is per entry, and content-only.** The file converges because the
  stored hash is verified, not compared, so the random salt never churns. A
  ``hash_scheme`` change alone does *not* rewrite an entry (a stored ``$apr1$``
  line still verifies), and an owner/group/mode-only drift does not re-install --
  ``files.file`` converges metadata, as the ``htpasswd`` role's companion step
  shows.

The parameter is ``username``, not Ansible's ``name``: ``op()`` already reads
``name`` as the request label, so no operation can take it (the ``crontab``
module's ``cron_name`` set the precedent).

The script runs the same deploy twice: the first creates the file and the two
entries, the second reads the result back and reports no change on any host.
Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example uses
the *default* factory (``Host.from_inventory``), so each plain ``Host`` lazily
opens an asyncssh connection and really reads and writes the file.

Every request carries ``when=is_ubuntu``, the declarative eligibility guard: the
predicate reads only **static** inventory data -- ``host.family``, set by the
``"family"`` key of the inventory entry -- and is a stable module-level function,
so the prepare and apply phases reach the same skip decision. On hosts outside the
``"Ubuntu"`` family the steps render as ``[skipped: when guard is_ubuntu]``.

The inventory is read from ``inventory.json`` in the current working directory; edit it
there to target your machines.

Run it with::

    uv run python examples/modules/files/files_htpasswd.py
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

# The file to manage: a demo htpasswd file under /etc, in a directory the
# install creates for it. Point this at a real one (eg
# /etc/apache2/.htpasswd) on your own hosts.
HTPASSWD_PATH = "/etc/reemote-htpasswd-demo/.htpasswd"

# The two users to publish, in the two schemes worth demonstrating. No real
# secrets: this is a demo file, and the passwords never leave the control
# machine anyway -- only their hashes are written.
USERS = (
    ("alice", "alice-password-1", "apr_md5_crypt"),
    ("bob", "bob-password-2", "bcrypt"),
)

# A user that is *not* wanted: managing its absence is the other half of the
# operation, and needs no password.
RETIRED_USER = "carol"


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.family == "Ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure both users, then ensure the retired one is gone.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases (a branch on a mutable fact would raise DeterminismError). The three
    requests share one file: the driver applies each before pulling the next, so
    by the time ``bob`` is edited the read-back already holds ``alice``'s line,
    and the removal sees both. Each request is flagged ``sudo=True`` for the
    install half -- the scp spool transfer always rides as the login user.
    """
    for name, password, scheme in USERS:
        yield op(
            files.htpasswd,
            name=f"Allow {name}",
            path=HTPASSWD_PATH,
            username=name,
            password=password,
            hash_scheme=scheme,
            # The default 0640 keeps the stored hashes off every local account;
            # pass owner/group to hand the file to a service user instead.
            mode="0640",
            sudo=True,
            when=is_ubuntu,
        )
    yield op(
        files.htpasswd,
        name=f"Drop {RETIRED_USER}",
        path=HTPASSWD_PATH,
        username=RETIRED_USER,
        present=False,
        sudo=True,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The file is still read over SSH
        # (read-only) so the plan reflects each host's pre-deployment state;
        # nothing is spooled or installed.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # First real run: spool + install the entries on every host.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Log: manage htpasswd entries (first run) ==")
        assert report is not None
        print(format_log(report))

        # Second run of the SAME deploy: FileContents now reads back the hashes
        # the first run drew. They verify, so no line is rewritten despite the
        # fresh salts and every request reports no change.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Log: manage htpasswd entries (second run, no-op) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON. Python's
        # own message already names the file and the reason, so print just that
        # -- no host was contacted and there is no traceback worth showing.
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
