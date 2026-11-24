# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``repo_epel`` role against an inventory.

The role lives in the library (:mod:`reemote.roles.repo_epel`); this is the
runnable artifact (§17.3) -- import the role and its vars model, hand them to
the shared ``role_main`` with the ``epel_`` prefix, and it loads the inventory,
resolves the variables, runs one wave pass and prints the wave tree.

The role's variables are the inventory entry's ``epel_``-prefixed keys
(``epel_repo_disable``, ``epel_repofile_path``, ``epel_repo_url``,
``epel_repo_gpg_key_url``); with none present the role defaults apply. Hosts
outside the ``"RedHat"`` family emit nothing at all.

Run it with::

    uv run python examples/roles/repo_epel.py

against the repository's own ``inventory.json``, or point it elsewhere and
preview it without touching a host::

    uv run python examples/roles/repo_epel.py --inventory other.json --dry

``--dry`` is a one-wave preview: the run stops at the first barrier, so nothing
is fetched and no request is applied. A converged rerun is the real preview --
the second pass reports no change.
"""

import asyncio
import sys
from pathlib import Path

from reemote.roles import EpelVars, repo_epel

# The shared runner sits beside this script; add its directory to sys.path so
# ``import role_runner`` resolves whether the example is run by path or
# imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from role_runner import role_main  # examples/roles/role_runner.py

if __name__ == "__main__":
    asyncio.run(role_main(repo_epel, EpelVars, "epel_", Path("inventory.json")))
