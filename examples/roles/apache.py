# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``apache`` role against an inventory.

The role lives in the library (:mod:`reemote.roles.apache`); this is the
runnable artifact (§17.3) -- import the role and its vars model, hand them to
the shared ``role_main`` with the ``apache_`` prefix, and it loads the inventory,
resolves the variables, runs one wave pass and prints the wave tree.

The role's variables are the inventory entry's ``apache_``-prefixed keys
(``apache_listen_port``, ``apache_vhosts``, ``apache_mods_enabled``,
``apache_state``, ``apache_restart_state`` and the rest of
:class:`~reemote.roles.apache.ApacheVars`); with none present the role defaults
apply. Hosts outside the ``"Ubuntu"`` family emit nothing at all.

This runner also demonstrates the call-site ``overrides`` channel: it hands
``role_main`` an ``APACHE_OVERRIDES`` mapping (``listen_port=8080`` and one
``example.com`` vhost) that wins over the inventory's ``apache_``-prefixed keys
for every host. Overrides use the model's UNPREFIXED field names -- the
``apache_`` prefix is an inventory-key binding, stripped before validation -- so
``apache_listen_port`` in the inventory and ``listen_port`` here set the same
field. Precedence is model defaults < inventory extra < these overrides.

Installing the role needs the template extra, because the vhosts file is
rendered control-side with Jinja2::

    pip install "reemote[templates]"

Run it with::

    uv run python examples/roles/apache.py

against the repository's own ``inventory.json``, or point it elsewhere and
preview it without touching a host::

    uv run python examples/roles/apache.py --inventory other.json --dry

``--dry`` is a one-wave preview: the run stops at the version-probe barrier, so
the install requests are planned but nothing is read, rendered or applied. A
converged rerun is the real preview -- the second pass reports no change and
queues no restart.
"""

import asyncio
import sys
from pathlib import Path

from reemote.roles import ApacheVars, apache

# The shared runner sits beside this script; add its directory to sys.path so
# ``import role_runner`` resolves whether the example is run by path or
# imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from role_runner import role_main  # examples/roles/role_runner.py

# Common role variables handed to ``role_main`` as call-site ``overrides``.
# They use the model's UNPREFIXED field names (``listen_port``, ``vhosts``) --
# the ``apache_`` prefix only binds inventory keys, and is stripped before
# validation -- and win over any ``apache_``-prefixed key in the inventory for
# every host. A vhost is the same mapping the inventory's ``apache_vhosts``
# carries; ``resolve_vars`` validates it into a frozen ``VHost``, so the tuple
# stays hashable.
APACHE_OVERRIDES = {
    "listen_port": 8080,
    "vhosts": (
        {"servername": "example.com", "documentroot": "/var/www/vhosts/example_com"},
    ),
}

if __name__ == "__main__":
    asyncio.run(
        role_main(
            apache,
            ApacheVars,
            "apache_",
            Path("inventory.json"),
            overrides=APACHE_OVERRIDES,
        )
    )
