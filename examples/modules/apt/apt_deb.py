# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install a ``.deb`` file already on the host, with version idempotence.

This is a *usage* example, not part of the library. It shows how an
application drives reemote's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read the target hosts from ``inventory.json`` in the current working directory -- an
   inventory is just a list of dictionaries, each holding a host's name plus its SSH
   ``connection`` information (address, username and client key), which is exactly what
   JSON expresses -- and validate it up front with ``validate_inventory``, so a
   malformed file fails with an ``InventoryError`` naming the offending entry before any
   host is materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost`` via
``run``'s ``host_factory`` and never touches the network -- this example uses the
*default* factory (``Host.from_inventory``). Each plain ``Host`` lazily opens an
asyncssh connection from its ``connection`` data, gathers facts and runs commands for
real, and ``run`` closes those connections when it finishes. The inventory is read from
``inventory.json`` in the current working directory; edit it there to target your
machines.

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

``apt.deb`` installs (or with ``present=False`` removes) a ``.deb`` file that
already exists on the *target host* -- here ``bat``, the syntax-highlighting
``cat`` clone, the kind of small standalone ``.deb`` one commonly downloads
from a project's GitHub releases and installs by hand. The ``src`` is
therefore a **remote** path, not a controller-side one: upload the file first
with ``files.put`` (or place it there by any other means). Unlike the baseline --
whose ``apt.deb`` can also *download* a remote ``.deb`` mid-deploy -- reemote's
``apt.deb`` installs a *binary* file that must already be on the host, so a
``src`` with a URL scheme raises ``ValueError``; stage a URL with
``files.download`` (which fetches control-side and installs a binary body
intact since #23) or, as here, on the host itself. This example stages the file
with a demo-side ``curl``
one-liner (a raw ``StringCommand``, the same pattern as
``server_op_sysctl.py``'s ``sysctl_set``) yielded before the install, so the
``.deb`` really exists on the host when ``apt.deb`` reads it. ``bat`` is
deliberately small (~2.4 MB, a self-contained binary with no library
dependencies) so the demo fits the shared Ubuntu host's modest disk; point
``BAT_URL`` at any standalone ``.deb`` (e.g. Google Chrome) to install a
larger package on a host with room for it.

``apt.deb`` reads the file's ``name``/``version`` through the ``DebPackage``
fact, then reads the installed versions of that name through ``AptPackages``,
and counts the package as present only when the installed version matches the
file's. It installs only when it does not, so re-running is a no-op. The
install runs the baseline's three-pass sequence: a tolerant ``dpkg -i`` that
ignores unmet dependencies, an ``apt-get install -f`` to resolve them (a
no-op for self-contained ``bat``, but it pulls in the libraries a package
like Chrome needs), then a final ``dpkg -i`` that configures the package and
fails fast if dependencies are still missing. The script yields the *same*
install request twice to show that idempotence across the two phases: the
prepare phase plans both as changes (nothing is installed yet, so
both compare against the same pre-deployment state), while the apply phase
installs on the first and reports the second as a no-op, because the fresh
per-step fact cache now sees the package the first step installed.

Both install requests also carry ``update_cache=True`` (issue #54) -- Ansible's
pairing of a ``deb=`` task with an index refresh, which matters because the
``.deb`` a deploy installs is so often a repository-configuration package whose
purpose is to make new indexes available. The flag appends one ``apt-get
update`` *after* the three install passes, and only on a real change, so the
first request installs and refreshes while the second -- converged -- yields
neither. That is what a standalone ``apt.update`` request cannot do: it reads no
fact, always reports a change and re-fires anything keyed on ``changed_hosts``,
so a "converged" deploy would keep refreshing on every run.

The install requests also carry ``package="bat"`` -- the *identity hint* that
keeps the request converging once the artifact itself is gone (issue #55).
Without it, presence is derived solely from the file, so a ``.deb`` cleaned out
of ``/tmp`` (tmpreaper, an image-slimming step, a hand) leaves ``apt.deb``
unable to name the package: it can neither converge (it would plan a
``dpkg -i`` on a path that is not there) nor remove. With the hint the file's
silence is answered by the installed package database instead. The script
demonstrates that directly: a fourth request ``rm -f``s the staged ``.deb``, and
the fifth -- the same install, hinted -- reports no change because ``bat`` is
installed, exactly as Ansible's ``apt: deb=<url>`` converges after deleting its
temporary file. The file stays authoritative whenever it answers, so the hint is
a fallback and never an override; ``version="0.24.0"`` (a literal or a glob like
``0.24.*``) would pin the hint further, and no hint at all with the file absent
is a no-op whose reason names the missing artifact rather than a failing
install. Because the prepare phase reads the *pre-deployment* state, on a host
that has never run this script the first plan reports "staged file absent, cannot
install" -- the apply phase, where the ``curl`` step has already run, is the one
that installs.

Installing a package writes to the root-owned dpkg database, so the request is
flagged ``sudo=True`` and the driver elevates its commands. The tolerant first
pass ends in ``|| true``, which is privilege-safe -- ``true`` needs no root, so
a ``sudo`` prefix on the whole line still elevates the ``dpkg`` arm. The baseline's
``force`` option (``--force-yes``) is deliberately dropped: the flag is
deprecated in modern apt.

Like the other apt examples, every request carries ``when=is_ubuntu``: the
predicate reads only static inventory data (``host.family``) and is a stable
module-level function, so both phases reach the same skip decision. The
driver skips a rejected request without materializing it -- neither the
``dpkg``/``apt-get`` install passes nor the ``DebPackage`` and
``AptPackages`` facts behind them run on a non-Ubuntu host -- and plan and
log render ``[skipped: when guard is_ubuntu]``. The shared ``inventory.json``
holds one host in the ``"Ubuntu"`` family alongside hosts in other families,
so each run installs on the Ubuntu host and skips every other one.

Run it with::

    uv run python examples/modules/apt/apt_deb.py
"""

import asyncio
import json
import sys
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

from reemote import (
    Command,
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OpContext,
    OperationRequest,
    PrivilegeError,
    StringCommand,
    apt,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# The ``.deb`` file to install, as a path on the *target host*, and the URL
# the demo-side download step stages it from. A ``.deb`` is binary, so it is
# curled onto the host here (``files.download`` would fetch it control-side and
# install the bytes intact) before ``apt.deb`` reads it. BAT_DEB is shared by
# the install requests below so they carry identical operation kwargs (only the
# log ``name`` differs). BAT_PACKAGE is the identity hint (issue #55) the install
# requests carry, so they still converge after the last request removes the
# staged artifact.
BAT_DEB = "/tmp/bat_0.24.0_amd64.deb"
BAT_PACKAGE = "bat"
BAT_URL = (
    "https://github.com/sharkdp/bat/releases/download/v0.24.0/bat_0.24.0_amd64.deb"
)



def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision and the
    determinism guard holds. apt itself is the reason for the guard: on a
    non-Ubuntu host every request below would fail on the missing
    ``dpkg``/``apt-get`` toolchain, so the driver skips them all instead.
    """
    return host.family == "Ubuntu"


async def download_deb(ctx: OpContext, **kwargs) -> AsyncGenerator[Command]:
    """Stage the ``bat`` ``.deb`` on the host (demo-side download step).

    reemote's ``apt.deb`` takes a *remote* path and a ``.deb`` is binary, so the
    file must already exist on the
    target host. This example
    stages it with a stateless one-liner over the public raw ``StringCommand``
    descriptor -- the same demo-side pattern as ``server_op_sysctl.py``'s
    ``sysctl_set``. The ``test -f ... || curl`` guard downloads only when the
    file is absent, so a re-run is cheap; like the baseline's download step it
    reads no fact and always reports a change. Writing to ``/tmp`` and running
    ``curl`` need no root, so the request is *not* elevated (the ``apt.deb``
    install that follows is). ``**kwargs`` absorbs the baseline's global arguments
    (``op()`` already strips ``name``/``sudo``/``su``).
    """
    ctx.changed = True
    ctx.reason = "will stage the bat .deb on the host with curl"
    yield StringCommand(f"test -f {BAT_DEB} || curl -fsSL -o {BAT_DEB} {BAT_URL}")


async def remove_staged_deb(ctx: OpContext, **kwargs) -> AsyncGenerator[Command]:
    """Delete the staged ``.deb`` from the host (demo-side cleanup step).

    The point of the request is to take the artifact away *while the package
    stays installed*, which is the state a tmpreaper, an ``apt-get clean``, an
    image-slimming step or a hand eventually puts a real host in -- and the state
    the hinted ``apt.deb`` requests below have to converge from. Like the staging
    step it is a stateless raw ``StringCommand`` (``rm -f`` succeeds whether or
    not the file is there) and needs no root, since the login user created the
    file in ``/tmp``.
    """
    ctx.changed = True
    ctx.reason = "will remove the staged bat .deb from the host"
    yield StringCommand(f"rm -f {BAT_DEB}")


def deploy(host: Host) -> Generator[OperationRequest]:
    """Stage the ``bat`` ``.deb``, install it twice, then install it again with
    the artifact gone.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request stages the file on the host with a demo-side ``curl``
    (``apt.deb`` takes a remote path, and a ``.deb`` is binary so the file has
    to be put there before it is read);
    it is unprivileged, since writing ``/tmp`` and running ``curl`` need no
    root. The next two requests install the file's package: the first
    installs, the second carries the same arguments and is a guaranteed no-op
    in the apply phase, because the first install is already visible through
    the fresh per-step fact cache. All three carry the ``package`` identity
    hint, and the two install requests carry ``update_cache=True``, so the
    first also refreshes the index after the install passes while the second
    yields nothing at all. The fourth request removes the staged ``.deb``
    itself, and the fifth repeats the install: with the artifact gone the
    hinted request reads the installed package database instead of failing on
    the missing path, so it reports no change -- Ansible's convergence, without
    the permanent file in ``/tmp``.

    Installing a package writes to the root-owned dpkg database, so the three
    ``apt.deb`` requests are flagged ``sudo=True`` and the driver elevates
    their commands over SSH; the staging and cleanup steps stay unprivileged.
    """
    yield op(
        download_deb,
        name="Stage bat deb on the host",
        when=is_ubuntu,
    )
    yield op(
        apt.deb,
        name="Install bat deb",
        src=BAT_DEB,
        package=BAT_PACKAGE,
        update_cache=True,
        sudo=True,
        when=is_ubuntu,
    )
    yield op(
        apt.deb,
        name="Re-install bat deb (no-op)",
        src=BAT_DEB,
        package=BAT_PACKAGE,
        update_cache=True,
        sudo=True,
        when=is_ubuntu,
    )
    yield op(
        remove_staged_deb,
        name="Remove the staged bat deb",
        when=is_ubuntu,
    )
    yield op(
        apt.deb,
        name="Install bat deb again, file gone (no-op via package=)",
        src=BAT_DEB,
        package=BAT_PACKAGE,
        sudo=True,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent. A request the ``when`` guard rejects is
        # planned as [skipped: when guard is_ubuntu] without materializing it,
        # so its facts are not read on that host either.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
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
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
