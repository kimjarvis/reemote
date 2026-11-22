# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: converge a web server on real hosts, then wait until it serves a page.

This is a *usage* example, not part of the library. It shows the HTTP half of
reemote's **waiting** design -- the primitive that replaces an Ansible ``uri``
+ ``until`` readiness task (issue #43). ``server.wait`` and
``server.wait_service`` only ask whether a *port* is listening or a *unit*
reports ``active``; neither reads an HTTP response. A daemon can hold its port
open long before it can serve requests -- a socket-activated unit owns the
listen socket while its workers come up, and an application server such as
Jenkins keeps answering ``"Please wait while"`` *while it upgrades, port already
listening*. To gate on the application rather than the socket, ``server.wait_url``
polls a readiness check built on two primitives:

* the ``HttpEndpoint`` fact, which runs ``curl -s -w '\\n%{http_code}' <url>``
  **on the host** and returns the ``{status, body}`` record, so a ``localhost``
  URL means the target's own loopback -- the daemon this deploy is waiting on,
  which the controller has no route to; and
* the ``EndpointReady`` predicate, which decides readiness from that record.

The waited-for service is the web server the demo inventory already ships --
``httpd`` on the RedHat hosts, ``nginx`` on the Ubuntu one -- so nothing has to
be provisioned before the example can run. The readiness check asks for the page
each one actually serves: ``EndpointReady(statuses=(200,), contains="Index of /")``
on the autoindex-answering CentOS host, the same predicate with
``contains="It works"`` on the Ubuntu default site. The ``geerlingguy.jenkins``
task this primitive was built for is the same shape with other arguments: it GETs
``http://localhost:8080/cli/``, accepts ``200`` *or* ``403`` (the CLI answers
``403`` until you are signed in, which is fine for a readiness check) and repeats
until the body no longer carries ``"Please wait while"`` -- i.e.
``EndpointReady(statuses=(200, 403), absent="Please wait while")``. Either way it
is one named in-deploy request, polled by the driver every ``interval`` seconds
up to ``timeout`` (raising ``WaitTimeoutError`` and aborting the run otherwise).

``server.wait_url`` is the third specialised wait -- the HTTP counterpart to
``server.wait`` (a port) and ``server.wait_service`` (a unit): a thin
composition of the very same ``WaitForFactCommand`` descriptor those two yield,
over the ``HttpEndpoint`` fact and an ``EndpointReady`` predicate it builds from
its arguments. Like every wait it reads no facts and is always planned as a
change -- whether the endpoint is *already* ready is discovered at apply time by
the driver's poll loop, never at plan time, and a zero ``timeout`` still polls
once so an already-ready endpoint always succeeds. ``server.service`` is the
idempotent half: it starts and enables the unit only where the state differs,
so this run plans no service change on a converged host while the wait is
planned again.

Both requests carry ``when=is_systemd``, which reads only static inventory data
(``host.family``): the OpenRC ``alpine`` host has no ``systemctl``, so it is
skipped rather than failed, while the systemd hosts run the deploy for real.

The request stays deterministic because ``wait_url`` rebuilds its
``EndpointReady`` predicate from its arguments in both phases, and a frozen
dataclass compares by value -- so the descriptor is identical in prepare and
apply (a freshly built lambda would not be). The accepted ``status`` codes are a
tuple, never a list, because the request must stay hashable, and the unit name
and body marker come from ``web_server_unit``/``ready_marker``, static
``host.family`` branches of the kind the ``systemd`` examples make.

Both web-server units are active and enabled on the demo hosts, so a plain run
converges the service (planned as no change) and passes the wait on its first
poll. Stop one by hand (``sudo systemctl stop httpd``) and the same deploy shows
the pair working in sequence: the service request starts the unit, the wait
polls the endpoint with ``curl`` until the page is served again. Unlike the
pytest suite -- which injects ``SimulatedHost``, seeds its ``http_endpoints``
seam and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` opens an asyncssh connection,
reads the unit state for real, runs ``systemctl`` under sudo, and polls the
endpoint until the web server answers.

The inventory is read from ``inventory.json`` in the current working directory; edit it
there to target your machines.

Run it with::

    uv run python examples/modules/server/server_wait_url.py
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
    WaitTimeoutError,
    format_log,
    format_plan,
    op,
    run,
    server,
    validate_inventory,
)

# The endpoint each host probes for itself (host-local, so ``localhost`` is the
# target's own loopback, where its web server listens).
WEB_URL = "http://localhost/"



def is_systemd(host: Host) -> bool:
    """``when`` guard: the host runs systemd rather than OpenRC.

    Reads only static inventory data (``host.family``) and stays a stable
    module-level function, so both deploy phases reach the same decision -- the
    OpenRC ``alpine`` host, which has no ``systemctl``, is skipped.
    """
    return host.family != "Alpine"


def web_server_unit(host: Host) -> str:
    """The installed web-server unit, by OS family (static data only).

    ``httpd`` on the RHEL-family hosts, ``nginx`` on the Ubuntu one -- the two
    units the demo inventory really runs. ``host.family`` is inventory data, so
    the name is the same value in both deploy phases.
    """
    return "httpd" if host.family == "RedHat" else "nginx"


def ready_marker(host: Host) -> str:
    """A string the unit's own site serves only once it answers over HTTP.

    The CentOS ``httpd`` has no index page and replies with its autoindex
    listing (``"Index of /"``); the Ubuntu ``nginx`` serves the default site
    (``"It works"``). Gating on the document rather than the listening socket is
    the whole point of ``server.wait_url``.
    """
    return "Index of /" if host.family == "RedHat" else "It works"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Converge the web server, then wait for it to serve a page.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases; ``web_server_unit``/``ready_marker`` read only static
    inventory data. Only the service operation is elevated; the readiness poll
    runs ``curl`` as the login user. Both are guarded with ``when=is_systemd``
    so the guard expresses eligibility rather than an ``if`` that would diverge
    between the phases.
    """
    yield op(
        server.service,
        name="Start the web server",
        service=web_server_unit(host),
        running=True,
        enabled=True,
        sudo=True,
        when=is_systemd,
    )
    # ``server.wait_url`` polls the ``HttpEndpoint`` fact through an
    # ``EndpointReady`` predicate built from these arguments. The accepted status
    # and the body marker say "the daemon answers over HTTP", not "the port is
    # open"; ``status`` is a tuple, never a list, so the request stays hashable.
    yield op(
        server.wait_url,
        name="Wait for the web server to answer over HTTP",
        url=WEB_URL,
        status=(200,),
        contains=ready_marker(host),
        timeout=60.0,
        interval=2.0,
        when=is_systemd,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
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
        # and enables the unit where it differs; step 2 polls each host's
        # endpoint with curl until its site answers 200 with the expected marker
        # (or raises WaitTimeoutError after 60s). The OpenRC alpine host is
        # skipped by the ``when`` guard in both steps.
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
        # The web server never answered the endpoint as requested within the
        # timeout. The message carries the host, the wait descriptor and the
        # last polled ``HttpEndpointInfo`` record -- the status and body that
        # kept failing the predicate -- which is everything worth printing.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
