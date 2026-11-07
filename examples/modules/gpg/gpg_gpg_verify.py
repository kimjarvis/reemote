# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``gpg.GpgVerify`` fact on two real hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/gpg/gpg_gpg_key.py``, it reads a single **fact**
directly over the *real SSH transport* (asyncssh) instead of driving the
two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``GpgVerify`` for one signature/file pair on every host at once
   with the driver's ``gather_fact`` helper -- it fans the fact's
   ``process`` coroutine out across the inventory with ``asyncio.gather``,
   forwarding both paths, and returns a host-name-keyed mapping -- then
   print each host's verification dict as JSON.

``GpgVerify`` is a reemote5 original with no pyinfra counterpart:
verification is modeled as a read-only fact (``gpg --verify`` changes no
state), running ``gpg --batch --status-fd 1 --verify <signature> <file>``
and parsing the machine-readable ``[GNUPG:]`` status lines into one
``GpgVerification`` dict -- ``valid`` (True only for GOODSIG), ``status``
(``goodsig``, ``badsig``, ``no_pubkey``, ``errsig`` or ``error``), plus
the signing ``keyid`` and, for good/bad signatures, the ``signer`` user
id. Both paths are paths on the *target* host: a detached signature and
the file it signs must already be there (transfer them with
``files.put`` first). Without the file argument the signature must be
attached or cleartext. ``valid`` is signature-correctness only -- a good
signature from an untrusted key is still valid; trust is the separate
``GpgTrust`` fact's question. The verification uses the *executing
user's* keychain, so the signing key must be imported there for anything
other than ``no_pubkey`` to come back.

Every host's connection is closed with ``aclose`` once the facts are
gathered. ``gather_fact`` and ``process`` are coroutines, so ``main`` is
async and driven by ``asyncio.run``.

Run it with::

    uv run python examples/modules/gpg/gpg_gpg_verify.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    GpgVerify,
    Host,
    HostConnectionError,
    gather_fact,
)

# The detached signature and the file it signs, both already present on
# the target hosts (transfer them with files.put before verifying).
SIGNATURE = "/tmp/report.txt.sig"
FILE = "/tmp/report.txt"

# The inventory: real hosts and their SSH connection information, supplied by
# the application to the library. Host.from_inventory materializes each entry
# into a plain Host that connects over asyncssh.
INVENTORY = [
    {
        "host": "web1",
        "connection": {
            "address": "192.168.0.240",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
    {
        "host": "web2",
        "connection": {
            "address": "192.168.0.242",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
]


async def main() -> None:
    # Materialize every inventory entry up front so the fact can be gathered
    # on all hosts at once.
    hosts = [Host.from_inventory(entry) for entry in INVENTORY]
    try:
        # Gather the fact across the whole inventory concurrently:
        # gather_fact fans GpgVerify.process out with asyncio.gather,
        # forwarding SIGNATURE and FILE to every host, and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead
        # of running one host at a time.
        verifications = await gather_fact(hosts, GpgVerify, SIGNATURE, FILE)
    except (CommandError, HostConnectionError) as exc:
        # The library reports an unreachable host or a failed fact command
        # as a typed error whose message already names the host. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible. A failed *verification* is not an exception: it
        # reads as a dict with valid=False.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: gpg.GpgVerify({SIGNATURE!r}, {FILE!r}) ==")
        print(json.dumps(verifications[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())