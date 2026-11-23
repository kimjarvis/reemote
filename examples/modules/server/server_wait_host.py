# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: start a web server, then wait until its network address answers.

This is a *usage* example, not part of the library. It shows the third routing
of reemote's **waiting** design -- the ``host`` option that gives ``server.wait``
Ansible's ``wait_for`` ``host``/``port`` parity (issue #56). ``server.wait`` on
its own asks the target whether *its kernel's socket table* has a listener;
``server.wait_service`` asks systemd for a unit state and ``server.wait_url``
asks an HTTP endpoint for a page. None of the three answers the question a role
with more than one machine asks before it talks to a service somewhere else:
**can this host reach that endpoint over the network?** ``geerlingguy.elasticsearch``
is exactly that role -- it binds ``network.host`` to a LAN address and then
``wait_for``s the host/port pair, because a node whose socket is bound to
loopback, or whose port is open but firewalled, still *looks* started.

``server.wait`` gains ``host`` for that, and the option only picks **which fact
the same descriptor polls**:

* omitted, or ``"localhost"``, keeps polling ``Port`` -- the socket-table read is
  the cheaper, dependency-free answer for the target's own port, so every
  existing plan renders exactly as it did before; and
* any other address polls ``TcpEndpoint``, which runs the TCP connect **on the
  target** (``bash``'s ``/dev/tcp/<address>/<port>`` redirection, or ``nc -z``
  where there is no bash) and answers reachable/not reachable through the
  ``is_reachable`` predicate. Because the probe runs there, the address keeps the
  role's meaning -- a LAN address, a hostname, a peer node -- and an endpoint the
  controller has no route to is still probeable.

Such a probe is a TCP connect, so a non-local ``host`` pins ``protocol`` to
``tcp`` (the socket-table family switch has no meaning for it and a UDP
"connect" proves nothing), and a value that is not a hostname or IP literal is
rejected by ``ValueError`` at plan time, like a bad port. A host with neither
``bash`` nor ``nc`` cannot answer the question at all, so that is reported as
``CommandNotFoundError`` rather than polled as an endpoint that never opens.

Here each host starts its web server and then probes the address a *client*
would use for it -- the ``address`` of its own inventory entry, not
``localhost`` -- so the wait asks whether the service answers across the LAN
rather than merely that something holds the port open. The port and unit follow
the demo inventory (``httpd`` on the RedHat hosts, ``nginx`` on the Ubuntu one),
so nothing has to be provisioned before the example can run; ``peer_address``
and ``web_server_port`` read only static inventory data, which is what keeps the
deploy deterministic.

Every request carries ``when=is_systemd``, which reads only ``host.family``: the
OpenRC ``alpine`` host has no ``systemctl``, so it is skipped rather than failed,
while the systemd hosts run the deploy for real.

The wait reads no facts and is always planned as a change -- whether the
endpoint is *already* reachable is discovered by the driver's poll loop, never at
plan time, and a zero ``timeout`` still polls once so an open endpoint always
succeeds. ``server.service`` is the idempotent half: it starts and enables the
unit only where the state differs, so this run plans no service change on a
converged host while the wait is planned again. The descriptor stays hashable --
the address is a ``str`` and the port an ``int`` -- so the request is identical
in prepare and apply.

Stop the server by hand (``sudo systemctl stop httpd``) and the same deploy shows
the pair working in sequence: the service request starts the unit, the wait
probes ``TcpEndpoint`` until the LAN address accepts a connection. Unlike the
pytest suite -- which injects ``SimulatedHost``, seeds its ``tcp_endpoints`` seam
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` opens an asyncssh connection, runs
``systemctl`` under sudo, and asks the target itself whether the endpoint is
reachable.

The inventory is read from ``inventory.json`` in the current working directory; edit it
there to target your machines.

Run it with::

    uv run python examples/modules/server/server_wait_host.py
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

# The port each web server listens on. Every demo host binds it to all
# interfaces, so the LAN address below really does answer.
WEB_PORT = 80


def is_systemd(host: Host) -> bool:
    """``when`` guard: the host runs systemd rather than OpenRC.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function, so both deploy phases reach the same skip decision --
    the OpenRC ``alpine`` host, which has no ``systemctl``, is skipped.
    """
    return host.family != "Alpine"


def web_server_unit(host: Host) -> str:
    """The installed web-server unit, by OS family (static data only).

    ``httpd`` on the RHEL-family hosts, ``nginx`` on the Ubuntu one -- the two
    units the demo inventory really runs. ``host.family`` is inventory data, so
    the name is the same value in both deploy phases.
    """
    return "httpd" if host.family == "RedHat" else "nginx"


def peer_address(host: Host) -> str:
    """The address the host probes -- its own routable one, not loopback.

    This is the value a role parameterises: the ``network.host`` its service
    binds, which the Ansible ``wait_for`` task then names. Falling back to the
    host *name* keeps the example runnable with an inventory entry that carries
    no explicit ``address`` (a name is a hostname, and ``TcpEndpoint`` accepts
    either). Both phases read the same inventory mapping, so the request is
    identical in prepare and apply.
    """
    return host.connection.get("address") or host.name


def deploy(host: Host) -> Generator[OperationRequest]:
    """Converge the web server, then wait for its network address to answer.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases; ``web_server_unit``/``peer_address`` read only static inventory
    data. Only the service request is elevated -- the probe is a plain outgoing
    TCP connect, which needs no privilege -- and both are guarded with
    ``when=is_systemd`` so the guard expresses eligibility rather than an ``if``
    that would diverge between the phases.
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
    # ``host`` is the only difference from the documented default wait: instead
    # of polling the target's own socket table for port 80, this asks the target
    # to *connect* to 80 on its LAN address, which is the reachability question
    # a multi-machine role asks. An omitted or ``"localhost"`` value would keep
    # the ``Port`` poll; a non-local one pins ``protocol`` to ``tcp``.
    yield op(
        server.wait,
        name="Wait for the web server on its network address",
        host=peer_address(host),
        port=WEB_PORT,
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
        # state; nothing is started and nothing is probed -- the reachability
        # wait is simply planned, always as a change, and its loggable form names
        # the fact and predicate the apply phase will poll.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. Step 1 starts
        # and enables the unit where it differs; step 2 probes each host's
        # endpoint until its LAN address accepts a TCP connection (or raises
        # WaitTimeoutError after 60s). The OpenRC alpine host is skipped by the
        # ``when`` guard in both steps.
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
        # Either a ``systemctl`` invocation exited non-zero, or the probe found
        # neither ``bash`` nor ``nc`` on the target (``CommandNotFoundError``,
        # the subclass): an absent tool is a host that cannot answer the
        # question, not an endpoint that stays closed. The typed message already
        # names the host, the credential-free command and its stderr.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except WaitTimeoutError as exc:
        # The endpoint never accepted a connection within the timeout. The
        # message carries the host, the wait descriptor and the last polled
        # ``TcpEndpointInfo`` record -- the ``reachable`` flag that stayed false
        # -- which is everything worth printing.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
