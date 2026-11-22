# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install and remove RubyGems packages with ``gem install``/``uninstall``.

This is a *usage* example, not part of the library. It shows how an application
drives reemote's two-phase execution model end to end over a **real SSH
transport** (asyncssh):

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information (address, username and
   client key), which is exactly what JSON expresses -- and validate it up front
   with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Build the deploy script as a generator yielding ``OperationRequest`` objects,
   each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example uses
the *default* factory (``Host.from_inventory``). Each plain ``Host`` lazily opens
an asyncssh connection from its ``connection`` data, gathers facts and runs
commands for real, and ``run`` closes those connections when it finishes. The
inventory is read from ``inventory.json`` in the current working directory; edit
it there to target your machines.

``gem.packages`` is reemote's port of the baseline's ``gem.packages``, extended
to the surface the Ansible ``gem`` module needs (issue #57): it installs, updates
or removes RubyGems packages given as bare names or ``<pkg>=<version>`` pins,
system-wide (the default) or into the invoking user's gem directory
(``user_install=True``). The operation is idempotent on the ``GemPackages`` fact
(``gem list --local``): a pinned request is present only when that *exact*
version is installed and an unpinned one when the name is installed at any
version, so re-running is a no-op. The script yields the *same* install request
twice and the *same* removal request twice to show that idempotence across the
two phases: the prepare phase plans both halves of each pair as changes (nothing
is sent yet), while the apply phase runs the first and reports the second as a
no-op, because the fresh per-step fact cache re-reads the listing against the
updated state. Installing and then removing the same gems makes the example
*self-restoring*: it leaves the host's system store as it found it (as long as
they were not installed beforehand), which matters because ``run_examples.py``
sweeps every example over the same shared hosts.

The two gems show both presence rules. ``rainbow=3.1.1`` is reemote's one
user-facing pin spelling -- shared with apt, dnf, apk and npm -- and rides
through to the install shell translated into gem's own grammar as
``rainbow:3.1.1`` (pyinfra's ``<pkg>:<version>`` input spelling is deliberately
not accepted); ``paint`` is unpinned, so it is installed once and considered
present at whatever version gem reports afterwards. Both are pure Ruby with no
native extension, so the install is a registry fetch and a file write rather than
a C build. Removal is version-aware: ``rainbow=3.1.1`` is uninstalled with
``--version 3.1.1`` (``gem uninstall`` has no ``name:version`` token, so a pinned
removal is its own command) and ``paint`` with the bare-name ``-a`` (all versions)
-- both rendered non-interactively with ``-x`` (drop executables) and ``-I``
(ignore dependents) so an unattended deploy never stops at a prompt.

Every request flags ``sudo=True``, because a system-wide install writes into
gem's root-owned ``GEM_HOME`` (``/var/lib/gems`` on Ubuntu) and the driver
elevates the rendered shell (``sudo gem install --no-user-install ...``). The
``GemPackages`` read is unprivileged: it only lists that tree. The scope flag is
rendered in *both* directions -- ``--no-user-install`` here -- so the tree written
is always the one the fact read, never gem's own writable-GEM_HOME heuristic. Two
further parameters are *not* set here, and both exist for cases this demo does not
have: ``user_install=True`` would target the login user's own gem directory (no
``sudo`` needed), and ``gem_home``/``gem_path``/``gem_bin`` would relocate the
store or name a non-default ``gem`` (an rbenv/rvm shim) -- ``user_install`` and an
explicit ``gem_home``/``gem_path`` are mutually exclusive, so the operation
refuses the ambiguous combination with a ``ValueError``. Those paths are exercised
by the pytest suite and described on the module page instead of here.

Like the other examples, every request carries ``when=is_ubuntu``: the shared
``inventory.json`` holds one host in the ``"Ubuntu"`` family alongside hosts in
other families, so each run installs and removes on the Ubuntu host and skips
every other one -- on a host outside the group the driver skips each request
without materializing it, so neither the ``gem`` commands nor the ``GemPackages``
fact behind them runs, and plan and log render ``[skipped: when guard
is_ubuntu]``. That guard is not cosmetic: the fact read is a bare ``gem``
invocation, because the baseline's ``requires_command`` gating is deliberately
dropped, so on a host without gem it would exit 127 and raise
``CommandNotFoundError``.

gem is *not* installed by default on the demo host -- Ubuntu ships no
``ruby``/``gem`` until you ask -- so this example provisions the tooling itself
before deploying: ``main`` first awaits ``provision_gem`` from the sibling
``provision_gem.py``, which installs the ``ruby-rubygems`` deb with
``apt.packages`` (idempotent, so the run is cheap on a host that already has it).
The example therefore runs even on a host where gem was never installed.

Run it with::

    uv run python examples/modules/gem/gem_packages.py
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
    format_log,
    format_plan,
    gem,
    op,
    run,
    validate_inventory,
)

# The gem provisioning helper sits beside this script; add its directory to
# sys.path so ``import provision_gem`` resolves whether the example is run by
# path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_gem import provision_gem

# The gems to install and remove again -- one ``<pkg>=<version>`` pin and one
# bare name, so the run shows both of the fact's presence rules. A module-level
# tuple, so the request kwargs stay hashable and identical in the prepare and
# apply phases.
PACKAGES = ("rainbow=3.1.1", "paint")


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows so
    both deploy phases reach the same eligibility decision and the determinism
    guard holds. The Ubuntu demo host is the one ``provision_gem.py`` installs
    gem on before this example deploys (see the module docstring), so the
    install/removal is guarded to that family; on a host outside it the bare
    ``gem`` fact read would fail before doing anything, so the driver skips them
    all instead.
    """
    return host.family == "Ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Install the gems -- twice, to show idempotence -- then remove them.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases (a branch on a mutable fact would raise DeterminismError). The first
    request installs whatever the ``GemPackages`` listing does not carry yet; the
    second carries the same arguments and is a no-op in the apply phase once the
    first install landed. The third removes the gems again (``present=False``)
    and the fourth is its no-op repeat, leaving the system store as found so the
    shared-host example sweep is not polluted.

    A system store lives under gem's root-owned ``GEM_HOME``, so every request is
    elevated; a ``user_install=True`` request into the login user's own directory
    would not need ``sudo=True``.
    """
    yield op(
        gem.packages,
        name="Install rainbow and paint system-wide",
        packages=PACKAGES,
        sudo=True,
        when=is_ubuntu,
    )
    yield op(
        gem.packages,
        name="Re-run install (no-op)",
        packages=PACKAGES,
        sudo=True,
        when=is_ubuntu,
    )
    yield op(
        gem.packages,
        name="Remove them again",
        packages=PACKAGES,
        present=False,
        sudo=True,
        when=is_ubuntu,
    )
    yield op(
        gem.packages,
        name="Re-run removal (no-op)",
        packages=PACKAGES,
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

        # Install gem on the Ubuntu hosts before anything below reads or
        # mutates gem state, so the example runs even on a host where gem was
        # never installed (see provision_gem.py).
        print("\n== Provisioning gem (provision_gem.py) ==")
        await provision_gem(inventory)

        # --dry: prepare phase only. Facts are still gathered over SSH (the
        # ``gem list`` read is read-only) so the plan reflects each host's real
        # pre-deployment state; no command that mutates a host is sent. A
        # request the ``when`` guard rejects is planned as
        # [skipped: when guard is_ubuntu] without materializing it.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema. The
        # message names the offending entry by its list index, and it fails
        # before any host is materialized.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command (a missing gem
        # binary surfaces as CommandNotFoundError, a CommandError subclass).
        # Print that message -- no traceback -- and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
