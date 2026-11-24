# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``php`` role against an inventory.

The role lives in the library (:mod:`reemote.roles.php`); this is the runnable
artifact (§17.3) -- import the role and its vars model, hand them to the shared
``role_main`` with the ``php_`` prefix, and it loads the inventory, resolves the
variables, runs one wave pass and prints the wave tree.

The role's variables are the inventory entry's ``php_``-prefixed keys
(``php_memory_limit``, ``php_enable_php_fpm``, ``php_fpm_pools``,
``php_opcache_enable``, ``php_default_version_debian`` and the rest of
:class:`~reemote.roles.php.PhpVars`); with none present the role defaults apply.
Hosts outside the ``"Ubuntu"`` family emit nothing at all.

No extra is needed to install the role: its four templates (``php.ini``,
``apc.ini``, ``opcache.ini``, ``www.conf``) are pure variable substitution,
rendered control-side as plain f-strings.

Run it with::

    uv run python examples/roles/php.py

against the repository's own ``inventory.json``, or point it elsewhere and
preview it without touching a host::

    uv run python examples/roles/php.py --inventory other.json --dry

``--dry`` is a one-barrier preview: the role's first step is the distribution
read that selects the PHP version, so the dry pass parks there and plans nothing
else. Run it twice instead -- the converged second pass is the honest preview,
and on a host the role has already applied it reports no change and queues no
restart.

PHP-FPM is off by default (``php_enable_php_fpm``); turning it on changes no wave
count -- the pool-directory read runs either way, exactly as the role's unguarded
``stat`` does -- but the configure wave gains ``Create fpm pools`` and the FPM
service task, and the handler wave gains the chained ``restart php-fpm``.
"""

import asyncio
import sys
from pathlib import Path

from reemote.roles import PhpVars, php

# The shared runner sits beside this script; add its directory to sys.path so
# ``import role_runner`` resolves whether the example is run by path or
# imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from role_runner import role_main  # examples/roles/role_runner.py

if __name__ == "__main__":
    asyncio.run(role_main(php, PhpVars, "php_", Path("inventory.json")))
