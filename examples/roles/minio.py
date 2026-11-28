# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``minio`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.minio`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``minio`` role mapping (``cluster_mode``, ``package_path``, ``buckets``,
   ``users``, ``sync`` and the rest of the
   :class:`~reemote.roles.minio.MinioVars` fields) is bound under the bare
   field names, the call-site ``MINIO_OVERRIDES`` below win over them, and a
   bad value raises before any host is contacted.
3. Build the deploy -- ``minio(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs: the ``install done`` checkpoint, the handler it
   notifies and every ``mc`` command are barriers *inside* the body, not
   separate ``run()`` calls. ``run_waves`` owns the host connections and
   closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.

The package source and the call-site ``overrides`` channel
---------------------------------------------------------

The Ansible role shipped a 44 MB ``files/minio.tar.gz``; a library role cannot
(exactly one of ``package_url``/``package_path`` must name the tarball):

* ``package_url`` -- any HTTP(S) URL of a ``.tar.gz``/``.tgz``/``.zip`` release
  bundle holding the ``minio`` and ``mc`` binaries (a mirror of the upstream
  Linux ``minio.tar.gz``). ``files.unarchive`` fetches it control-side.
* ``package_path`` -- a controller-side path to the same bundle; the bytes are
  read once and spooled to ``/tmp/minio.tar.gz`` on each host before the
  extraction.

``MINIO_OVERRIDES`` below reads ``REEMOTE_MINIO_PACKAGE`` as that path, so::

    REEMOTE_MINIO_PACKAGE=/path/to/minio.tar.gz uv run python examples/roles/minio.py

runs the whole role. The overrides also set ``cluster_mode`` off (each host
runs standalone against its own ``data_dir``, the role's non-cluster env
branch) and name one bucket, one admin user and one ``mc mirror`` sync pair
-- the three features the role keeps behind ``tags: never``, which are
ordinary variables here (D2). ``sync`` points its target back at this same
host (``127.0.0.1``), so the example needs no second endpoint to show the
mirror unit. Precedence is model defaults < inventory role mapping < these
overrides.

Installing and running
----------------------

The role renders ``/etc/default/minio``, the systemd unit and the sync units
control-side with Jinja2, so it needs the template extra::

    pip install "reemote[templates]"

Run it with::

    uv run python examples/roles/minio.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass is one wave deep
-- ``run_waves`` plans the install and configure requests, parks on the
``install done`` checkpoint (``barrier: install done -> unknown until run``)
and stops, gathering facts over SSH but applying nothing. The ``Unpack
minio.tar.gz`` line of that preview reads ``src /tmp/minio.tar.gz absent at
plan time; extraction planned once it is staged`` -- the archive is spooled by
the request before it, which a preview never applies, so the operation plans
the extraction instead of raising on the absent file (issue #73). A converged
rerun is the honest preview: run the whole program a second time and every
request reports no change -- the binaries are unpacked, the rendered files are
byte-identical, the unit is running -- so the restart handler does not fire.
The ``mc`` shells still re-run: their results are data, not state (D9).

Hosts outside the ``"Ubuntu"`` family emit nothing at all -- the role's family
guard is its body's opening ``if``, so such a host returns before its first
``yield`` and appears in no wave.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from reemote import (
    CommandError,
    HostConnectionError,
    InventoryError,
    PrivilegeError,
    resolve_vars,
    run_waves,
    validate_inventory,
)
from reemote.roles import MinioVars, minio

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# They use the model's bare field names (``cluster_mode``, ``buckets``) -- the
# same names the inventory's ``minio`` role mapping uses -- and win over it for
# every host. A bucket/user/sync item is the same mapping shape the inventory's
# ``roles.minio`` carries; ``resolve_vars`` validates each into its frozen
# model, so the tuples stay hashable.
MINIO_OVERRIDES = {
    "cluster_mode": False,
    # D5: the release bundle holding minio + mc. Point this at a downloaded
    # tarball (any .tar.gz/.tgz/.zip with both binaries at its top level); a
    # "package_url" key instead fetches the same bundle from a mirror.
    "package_path": os.environ.get("REEMOTE_MINIO_PACKAGE", "minio.tar.gz"),
    "buckets": ({"name": "bucket01", "expire_days": 365},),
    "users": ({"username": "miniouser", "password": "miniouser1"},),
    "sync": (
        {
            "upload_limit": "10MiB",
            "source": {"area_label": "sh"},
            "target": {
                "area_label": "wx",
                "host": "127.0.0.1",
                "port": 9000,
                "access_key": "minioadmin",
                "secret_key": "minioadmin",
            },
            "bucket_mapping": ({"local": "bucket01", "remote": "bucket02"},),
        },
    ),
}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's minio role mappings, overlaid with the call-site
        # overrides. A rejected value raises here -- a pydantic ValidationError
        # or the missing package source, both ValueErrors -- not mid-run.
        vars_set = resolve_vars(
            MinioVars, "minio", inventory, overrides=MINIO_OVERRIDES
        )
        # The role's whole task list: one per-host generator, barriers and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = minio(vars_set)

        # Dry preview: one wave deep. Facts are still gathered over SSH
        # (read-only) so the plan reflects each host's real pre-deployment
        # state, but the run parks on the ``install done`` checkpoint and
        # applies nothing. run_waves owns the hosts it materializes and closes
        # them itself.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts
        # over SSH -- install, configure, the checkpoint and its handler, then
        # one wave per ``mc`` command.
        report, _hosts = await run_waves(inventory, deploy)
        print("\n== Wave tree (apply phase) ==")
        print(report.tree())
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
    except (ValueError, TypeError) as exc:
        # A rejected role variable, the missing package source (exactly one of
        # package_url/package_path must be set), a package file the controller
        # cannot read, or an ``mc`` command that failed -- the alias-set
        # non-zero exit is the role body's own hard error. Each is raised
        # before the next wave is built, so nothing is half-applied.
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
