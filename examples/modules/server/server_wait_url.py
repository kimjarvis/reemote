# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: start Jenkins on real hosts, then wait until its web app answers.

This is a *usage* example, not part of the library. It shows the HTTP half of
reemote's **waiting** design -- the primitive that replaces an Ansible ``uri``
+ ``until`` readiness task (issue #43). ``server.wait`` and
``server.wait_service`` only ask whether a *port* is listening or a *unit*
reports ``active``; neither reads an HTTP response. A daemon can hold its port
open long before it can serve requests, and Jenkins keeps answering ``"Please
wait while"`` *while it upgrades, port already listening*. To gate on the
application rather than the socket, ``server.wait_url`` polls a readiness check
built on two new primitives:

* the ``HttpEndpoint`` fact, which runs ``curl -s -w '\\n%{http_code}' <url>``
  **on the host** and returns the ``{status, body}`` record, so a ``localhost``
  URL means the target's own loopback -- exactly the ``jenkins_hostname`` the
  role checks; and
* the ``EndpointReady`` predicate, which decides readiness from that record.

The ``geerlingguy.jenkins`` task GETs ``http://localhost:8080/cli/``, accepts
status ``200`` *or* ``403`` (the CLI answers ``403`` until you are signed in,
which is fine for a readiness check) and repeats until the body no longer
carries ``"Please wait while"``. That is precisely
``EndpointReady(statuses=(200, 403), absent="Please wait while")`` -- one
named in-deploy request, polled by the driver every ``interval`` seconds up to
``timeout`` (raising ``WaitTimeoutError`` and aborting the run otherwise).

``server.wait_url`` is the third specialised wait -- the HTTP counterpart to
``server.wait`` (a port) and ``server.wait_service`` (a unit): a thin
composition of the very same ``WaitForFactCommand`` descriptor those two yield,
over the ``HttpEndpoint`` fact and an ``EndpointReady`` predicate it builds from
its arguments. Like every wait it reads no facts and is always planned as a
change -- whether the endpoint is *already* ready is discovered at apply time by
the driver's poll loop, never at plan time, and a zero ``timeout`` still polls
once so an already-ready endpoint always succeeds. ``systemd.service`` is the
idempotent half: it starts and enables Jenkins only where the unit differs, so
the second run plans no service change while the wait is planned again.

The request stays deterministic because ``wait_url`` rebuilds its
``EndpointReady`` predicate from its arguments in both phases, and a frozen
dataclass compares by value -- so the descriptor is identical in prepare and
apply (a freshly built lambda would not be). The accepted ``status`` codes are a
tuple, never a list, because the request must stay hashable.

The example assumes Jenkins is installed on the hosts (the apt base example
installs it). Unlike the pytest suite -- which injects ``SimulatedHost``, seeds
its ``http_endpoints`` seam and never touches the network -- this example uses
the *default* factory (``Host.from_inventory``): each plain ``Host`` opens an
asyncssh connection, reads the unit state for real, runs ``systemctl`` under
sudo, and polls the endpoint with ``curl`` until Jenkins answers.

The path is resolved from ``__file__``, so the example works from any working
directory; point ``INVENTORY_PATH`` at your own file (or edit ``inventory.json``)
to target your machines.

Run it with::

    uv run python examples/modules/server/server_wait_url.py
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
    WaitTimeoutError,
    format_log,
    format_plan,
    op,
    run,
    server,
    validate_inventory,
)

# examples/modules/server/server_wait_url.py -> the project root, so the inventory
# is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The CLI endpoint the role probes (host-local, so ``localhost`` is each
# target's own loopback).
JENKINS_URL = "http://localhost:8080/cli/"


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


def deploy(host: Host) -> Generator[OperationRequest]:
    """Start Jenkins, then wait for its web application to finish upgrading.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases. Only the service operation is elevated; the readiness poll
    runs ``curl`` as the login user.
    """
    yield op(
        server.service,
        name="Start Jenkins",
        service="jenkins",
        running=True,
        enabled=True,
        sudo=True,
    )
    # ``server.wait_url`` polls the ``HttpEndpoint`` fact through an
    # ``EndpointReady`` predicate built from these arguments. The accepted
    # states (200 or 403) and the upgrade-page marker mirror the role's ``uri``
    # + ``until`` task exactly; ``status`` is a tuple, never a list, so the
    # request stays hashable.
    yield op(
        server.wait_url,
        name="Wait for Jenkins to finish upgrading",
        url=JENKINS_URL,
        status=(200, 403),
        absent="Please wait while",
        timeout=300.0,
        interval=5.0,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The unit state is still gathered over SSH
        # (read-only) so the plan reflects each host's real pre-deployment
        # state; nothing is started and nothing is polled -- the readiness wait
        # is simply planned, always as a change, and its loggable form names the
        # fact and predicate the apply phase will poll.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. Step 1 starts
        # Jenkins where needed; step 2 polls each host's endpoint with curl
        # until the CLI answers 200/403 with no upgrade page (or raises
        # WaitTimeoutError after 300s).
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Run log (apply phase) ==")
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
    except HostConnectionError as exc:
        # A host could not be reached over SSH at all. Report it plainly --
        # naming every unreachable host -- instead of dumping an asyncssh
        # traceback, and exit non-zero so the failure is script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the ``sudo=True`` elevation -- a
        # wrong ``sudo_password``, or a run-as user the login user may not
        # become. One line naming the host; the detail stays on ``__cause__``.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # A ``systemctl`` invocation exited non-zero (an unknown or masked
        # unit, for instance). The typed message already names the host, the
        # credential-free command and its stderr, so print just that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except WaitTimeoutError as exc:
        # Jenkins never answered the CLI endpoint as requested within the
        # timeout. The message carries the host, the wait descriptor and the
        # last polled ``HttpEndpointInfo`` record -- the status and body that
        # kept failing the predicate -- which is everything worth printing.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
