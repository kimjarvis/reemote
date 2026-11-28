# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``elasticsearch`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.elasticsearch`; this script is the runnable artifact -- a
plain ``main()`` that does what every reemote program does, with no shared
runner and no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``elasticsearch`` role mapping (``version``, ``package``, ``service_state``,
   ``network_host``, ``http_port``, the two heap sizes and the rest of the
   :class:`~reemote.roles.elasticsearch.ElasticsearchVars` fields) is bound
   under the bare field names, the call-site ``ELASTICSEARCH_OVERRIDES`` below
   wins over them, and a bad value raises before any host is contacted.
3. Build the deploy -- ``elasticsearch(vars_set)`` is the role's whole task list
   and its handler, one per-host generator -- and drive it with ``run_waves``,
   the barrier-aware driver a role needs: the ``checkpoint("configure done")``
   that reads the config change and selects the ``restart elasticsearch`` hosts
   sits *inside* the body, not in a separate gather-then-run split.
   ``run_waves`` owns the host connections and closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines. The Elasticsearch signing key is fetched *control-side* (the
controller needs egress to ``artifacts.elastic.co``; the host downloads
nothing), and the Elastic apt repository brings in the ``elasticsearch``
package, so the apply pass transfers a few hundred megabytes on a fresh host.
Elasticsearch also needs a Java runtime on the target -- the README's
``geerlingguy.java`` prerequisite; compose the shipped ``java`` role or install
a JDK before running this.

Installing and running
----------------------

The role renders ``elasticsearch.yml.j2`` and the heap templates with Jinja2,
which reemote does not ship: install the ``reemote[templates]`` extra (or run
inside this repo, whose ``dev`` group declares jinja2). Run it with::

    uv run python examples/roles/elasticsearch.py

The call-site overrides channel
-------------------------------

``ELASTICSEARCH_OVERRIDES`` binds the node to ``127.0.0.1`` instead of the
role's ``localhost`` and raises the heap floor to ``2g``, so the demonstration
leaves the node on loopback *and* writes both config files visibly different
from the packaged defaults -- which is what gives the ``restart elasticsearch``
handler something to fire on through the checkpoint. Overrides and the
inventory mapping both use the model's bare field names -- there is no key
prefix anywhere -- and the overrides win over the inventory's
``elasticsearch`` role mapping for every host. Everything else stays default:
the ``7.x`` repository channel (so the ``jvm.options.d/heap.options`` drop-in is
the managed JVM file), the ``elasticsearch`` package, the started-and-enabled
service and port ``9200``.

``run_waves`` is a coroutine, so ``main`` is async and driven by
``asyncio.run``. The factory itself is a plain (non-``async``) function -- the
role reads no facts, its only barrier is the checkpoint -- so
``elasticsearch(vars_set)`` is *called*, not awaited.

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass plans the request
wave and parks on the ``checkpoint`` barrier (``run_waves`` stops there: the
notification read needs a real apply report), so nothing is applied and the
service/wait tail stays unplanned. Convergence is what the offline pytest suite
pins: on a rerun against a converged host every *state* request reports no
change -- the dependencies and the package are installed, the key and the
repository line are in place (so the change-gated ``apt-get update`` never
runs), both config files are byte-identical with root:elasticsearch ``0660``
metadata, the service is running and enabled, and the notification set is
empty so the restart handler never appears. (The port wait is the one line
that still reports changed: it is a poll, not a state gate -- see deviation D6
on the role page.)

Hosts outside the ``"Ubuntu"`` family emit nothing at all -- the role's family
guard is its body's opening ``if``, so such a host returns before its first
``yield`` and appears in no wave. The role's RedHat branch is out of scope.
"""

import asyncio
import json
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
from reemote.roles import ElasticsearchVars, elasticsearch

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# Binding to the loopback instead of ``localhost`` keeps the demonstration's
# node off any external interface *and* makes the rendered config differ from
# the packaged default, while the ``2g`` heap floor differs from the heap
# template's ``1g`` -- together they give the first apply a config write and a
# demonstrable handler restart through the checkpoint. Overrides use the
# model's bare field names -- the same names the inventory's ``elasticsearch``
# role mapping uses -- and win over it for every host.
ELASTICSEARCH_OVERRIDES = {
    "network_host": "127.0.0.1",
    "heap_size_min": "2g",
}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's elasticsearch role mapping, overlaid with the call-site
        # overrides. A rejected value raises here -- a pydantic ValidationError,
        # which is a ValueError -- not mid-run.
        vars_set = resolve_vars(
            ElasticsearchVars, "elasticsearch", inventory, overrides=ELASTICSEARCH_OVERRIDES
        )
        # The role's whole task list and handler: one per-host generator,
        # barrier and all. A plain (non-async) factory, so it is called, not
        # awaited.
        deploy = elasticsearch(vars_set)

        # Dry preview: the request wave is planned, then the run parks on the
        # checkpoint barrier -- the notification read needs a real apply report,
        # so the handler, service and wait stay unplanned (like Ansible's
        # --check skipping handlers). Nothing is applied. run_waves owns the
        # hosts and closes them.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, parked at the checkpoint) ==")
        print(report.tree())

        # Real run: the configure wave (dependency install, key download,
        # repository plus gated refresh, package, rendered config installs and
        # their metadata companions), the checkpoint, then the handler wave on
        # the notified hosts only, followed by the service task and the port
        # wait.
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
        # A rejected role variable: pydantic's ValidationError is a ValueError,
        # raised by resolve_vars before any host is contacted. An ImportError
        # here would mean the reemote[templates] extra is missing -- the render
        # happens when the first configure wave is built.
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
