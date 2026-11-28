# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``certbot`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.certbot`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``certbot`` role mapping (``admin_email``, ``certs``, ``create_method``,
   ``auto_renew_options`` and the rest of the
   :class:`~reemote.roles.certbot.CertbotVars` fields) is bound under the bare
   field names, and a bad value raises before any host is contacted. This
   script passes call-site ``overrides`` so the certificate-creation half is
   demonstrated; the library default for ``create_if_missing`` is the role's
   own ``false`` (install and renewal cron only).
3. Build the deploy -- ``certbot(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs: the ``include_vars`` release dispatch, the RedHat
   ``crb`` enable, the certificate ``stat`` gate and the domain-probe /
   cert-issuing actions are barriers *inside* the body, not separate ``run()``
   calls. ``run_waves`` owns the host connections and closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.
The factory itself is a plain (non-``async``) function, so ``certbot(vars_set)``
is *called*, not awaited.

Installing and running
----------------------

The role needs no extra: both hook templates render as plain f-strings frozen
to ``systemctl``, so there is no Jinja2 and no PyYAML. Run it with::

    uv run python examples/roles/certbot.py

**This program issues real certificates**: with the demonstrated overrides the
apply phase runs ``certbot certonly`` against the Let's Encrypt production CA
on every host whose configured certificate is missing or whose domains
changed, so point ``inventory.json`` at machines you own and edit the
``certs`` override to your domains (or set ``testmode=True`` for staging
certs first). The target needs network egress to the ACME endpoint; ``certbot
certonly --standalone`` binds port 80, so a web server on the host must be
stopnable by the pre/post hooks (that is what the configured
``create_standalone_stop_services`` list is for -- switch ``create_method`` to
``webroot`` to leave it serving; on RHEL-family hosts set it to ``httpd`` if
that is what serves). SSH access to each Ubuntu or RedHat host with
passwordless sudo (or a ``privilege`` section in the inventory): every
elevated request carries ``sudo=True``, because reemote has no play-level
``become``. On RedHat hosts the role first runs the upstream
``setup-RedHat.yml`` pair (install ``dnf-plugins-core``, enable the ``crb``
repo on Rocky/AlmaLinux); the ``certbot`` RPM itself comes from EPEL, so
compose with the ``repo_epel`` role or enable the repo beforehand.

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass is one wave deep
-- and because the role's very first item (on both families) is the
``LinuxDistribution`` fact that answers the ``include_vars`` release dispatch,
it parks there (``barrier: LinuxDistribution -> unknown until run``), reading
nothing and applying nothing. A converged rerun is the honest preview: run the
whole program a second time and it reports no change, because the package is
installed, the hooks match, every configured certificate exists with the
domains it was issued with (the probe re-runs but is a read), and the cron
entry is in the table.

Hosts outside the ``"Ubuntu"`` and ``"RedHat"`` families emit nothing at all --
the role's family guard is its body's opening ``if``, so such a host returns
before its first ``yield`` and appears in no wave.
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
from reemote.roles import CertbotVars, certbot


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's certbot role mapping over the model defaults. A rejected
        # value -- a scoped-out install method, an unknown create method, a
        # cert without domains -- or a key that names no field raises here,
        # not mid-run. The overrides demonstrate the certificate half; the
        # library default for create_if_missing is the role's own false.
        vars_set = resolve_vars(
            CertbotVars,
            "certbot",
            inventory,
            overrides={
                "create_if_missing": True,
                "certs": [{"domains": ["example.com", "www.example.com"]}],
            },
        )
        # The role's whole task list: one per-host generator, barriers and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = certbot(vars_set)

        # Dry preview: one wave deep. The role's first item is the
        # LinuxDistribution fact behind the include_vars dispatch, so the run
        # parks there before reading any state or applying any request.
        # run_waves owns the hosts it materializes and closes them itself.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts
        # over SSH, each barrier closing its own wave. The wave count grows
        # with the configured certificate list -- a stat gate and at least one
        # action per cert.
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
        # A rejected role variable (pydantic's ValidationError and a RoleVarsError
        # for an unknown key in the certbot mapping are both a ValueError), or
        # the role body's own hard error: a gathered distribution that does not
        # match the host's declared Ubuntu/RedHat family, an unreadable Ubuntu
        # release, or an auto_renew_user naming a third user's crontab. The
        # first kind is raised before any request runs, so nothing is
        # half-applied.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command -- including a
        # ``dnf config-manager`` enable step, a domain probe or a certonly
        # action that exited non-zero, which the role raises as a CommandError
        # from the barrier's own Result. Print that message -- no traceback --
        # and exit non-zero so the failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
