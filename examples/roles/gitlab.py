# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``gitlab`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.gitlab`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory and validate it up front with ``validate_inventory``, so a
   malformed file fails with an ``InventoryError`` naming the offending entry
   before any host is contacted.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``gitlab`` role mapping (``domain``, ``version``, the optional LDAP/SMTP/
   registry/letsencrypt blocks, ``extra_settings`` ...) is bound under bare
   field names, and a bad value raises before any host is contacted.
3. Build the deploy -- ``gitlab(vars_set)`` is the role's whole task list and
   its handler, one per-host generator -- and drive it with ``run_waves``, the
   barrier-aware driver a role needs. The install probe and the certificate
   ``creates=`` read are ``fact`` barriers, the vendor-script and reconfigure
   runs are ``act`` barriers, and the ``restart gitlab`` notification read is
   a ``checkpoint``, all *inside* the body rather than in a gather-then-run
   split. ``run_waves`` owns the host connections and closes them itself.

Installing and running
----------------------

Needs ``reemote[templates]``: ``templates/gitlab.rb.j2`` renders control-side
through Jinja2, so install the extra first (``pip install "reemote[templates]"``
or, in a checkout, ``uv sync``). The install itself is *heavy* -- the omnibus
package is multiple gigabytes and the first ``gitlab-ctl reconfigure`` runs for
minutes -- and it claims port 80/443 via its bundled nginx, so stop any
existing web server first. Run it with::

    uv run python examples/roles/gitlab.py

The role variable channel
-------------------------

Every knob is the upstream ``gitlab_*`` default with the prefix stripped, in
the inventory entry's ``roles`` mapping -- reemote's ``host_vars`` mechanism::

    [
      {
        "host": "ubuntu",
        "family": "Ubuntu",
        "connection": {"address": "192.168.0.181",
                       "username": "supervisor",
                       "key_path": "~/.ssh/id_ed25519"},
        "group": "ubuntu",
        "roles": {
          "gitlab": {
            "domain": "gitlab.example.com",
            "version": "",
            "git_data_dir": "/srv/gitlab/data/git-data"
          }
        }
      }
    ]

This script passes no call-site ``overrides``, so the role's own defaults
apply: ``domain: gitlab`` derives ``external_url`` ``https://gitlab/``, the
certificate paths ``/etc/gitlab/ssl/gitlab.crt/.key`` and the subject CN;
``create_self_signed_cert`` is on, so the cert is generated; ``version`` is
empty, so the *candidate* installs (pin it with e.g.
``"version": "17.5.1-ce.0"``). With no variables at all the role still
installs and configures GitLab for the default ``gitlab`` host name.

``run_waves`` is a coroutine, so ``main`` is async and driven by
``asyncio.run``. The factory itself is a plain (non-``async``) function, so
``gitlab(vars_set)`` is *called*, not awaited.

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass plans the one
request that precedes the first barrier -- the dependency install -- and
parks on the ``/usr/bin/gitlab-ctl`` probe (``run_waves`` never runs a barrier
in dry mode; the probe's answer decides the rest of the stream), so nothing is
applied. A converged rerun is the honest preview: run the whole program a
second time and every request reports no change -- the probe finds the
binary, the bootstrap marker exists, the certificate is in place, and
``/etc/gitlab/gitlab.rb`` is byte-identical at root:root ``0600``, so the
checkpoint notifies nobody and the handler wave never appears.

Hosts outside the ``"Ubuntu"`` family emit nothing at all -- the role's family
guard is its body's opening ``if``, so such a host returns before its first
``yield`` and appears in no wave.
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
from reemote.roles import GitlabVars, gitlab


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's gitlab role mapping over the model defaults. A rejected
        # value raises here -- a pydantic ValidationError, which is a ValueError
        # -- not mid-play, where Ansible would fail on the first template read.
        vars_set = resolve_vars(GitlabVars, "gitlab", inventory)
        # The role's whole task list and handler: one per-host generator,
        # barriers and all. A plain (non-async) factory, so it is called, not
        # awaited.
        deploy = gitlab(vars_set)

        # Dry preview: the dependency install is planned, then the run parks
        # on the install-probe barrier -- whether the repo and package tasks
        # join the stream depends on the host, so nothing past the probe is
        # planned and nothing is applied. run_waves owns the hosts and closes
        # them.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, parked at the install probe) ==")
        print(report.tree())

        # Real run: the dependency install and the probe, the vendor repo
        # (script + bash run) and the gitlab-ce package when absent, the
        # first-run reconfigure, the optional self-signed certificate, the
        # rendered gitlab.rb plus its metadata companion, the checkpoint --
        # and on a changed config, ``gitlab-ctl reconfigure`` again.
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
        # raised by resolve_vars before any host is contacted. An empty domain
        # or edition -- which feed the derived paths and the repo URL -- lands
        # here too, not in the render.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports every host-level failure as a typed error naming
        # the host and the failing command -- which includes the repository
        # script and the openssl certificate command, whose non-zero exits the
        # role raises itself (the first-run reconfigure stays tolerated, as in
        # the role). Print that message -- no traceback -- and exit non-zero so
        # the failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
