# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Import a GPG key from a controller-side stream into the target keychain.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``gpg.import_key`` imports key content into the *executing user's*
keychain (unlike ``gpg.key``, which installs a standalone keyring file).
The ``src`` is the key *content* as a seekable IO-like stream on the
control machine, never a filename or URL. One request yields two
commands: a ``GpgImportUploadCommand`` transfer spools the stream to a
fixed ``/tmp`` path over scp as the login user, then a
``GpgImportCommand`` runs one ``sh -c`` that imports the spool with
``gpg --batch --yes --import`` and removes it. The request is flagged
``sudo=True`` here so the key lands in root's keychain; the transfer
stays unprivileged regardless.

Without a ``keyid`` the import is deliberately non-idempotent: the
fingerprint of a raw key stream is unknowable controller-side, so the
operation always imports and always reports a change -- both requests
below stay changed even in the apply log. Pass ``keyid=...`` to compare
against the ``GpgKeys`` fact instead and skip an already-imported key.

**The stream below carries a real public key** -- the Docker CE release
signing key for CentOS/rpm (fingerprint
``060A61C51B558A7F742B77AAC52FEB6B621E9F35``, published at
``https://download.docker.com/linux/centos/gpg``) -- so the import
succeeds on the demo hosts. Swap in your own vendor key when deploying
elsewhere.

The stream is compared *by identity* across the prepare and apply
phases, so it is defined once at module level below -- building a fresh
stream inside the deploy generator would diverge between the phases and
raise ``DeterminismError``.

Run it with::

    uv run python examples/modules/gpg/gpg_import_key.py
"""

import asyncio
import io
import sys
from collections.abc import Generator

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    gpg,
    op,
    run,
)

# The key content, as ONE module-level stream: OperationRequest compares the
# IO object by identity, so both phases must pass the same object (a stream
# built inside the generator would trip the determinism guard). Real armored
# bytes: the Docker CE release signing key for CentOS.
GPG_KEY = io.BytesIO(
    b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
    b"\n"
    b"mQINBFit5IEBEADDt86QpYKz5flnCsOyZ/fk3WwBKxfDjwHf/GIflo+4GWAXS7wJ\n"
    b"1PSzPsvSDATV10J44i5WQzh99q+lZvFCVRFiNhRmlmcXG+rk1QmDh3fsCCj9Q/yP\n"
    b"w8jn3Hx0zDtz8PIB/18ReftYJzUo34COLiHn8WiY20uGCF2pjdPgfxE+K454c4G7\n"
    b"gKFqVUFYgPug2CS0quaBB5b0rpFUdzTeI5RCStd27nHCpuSDCvRYAfdv+4Y1yiVh\n"
    b"KKdoe3Smj+RnXeVMgDxtH9FJibZ3DK7WnMN2yeob6VqXox+FvKYJCCLkbQgQmE50\n"
    b"uVK0uN71A1mQDcTRKQ2q3fFGlMTqJbbzr3LwnCBE6hV0a36t+DABtZTmz5O69xdJ\n"
    b"WGdBeePCnWVqtDb/BdEYz7hPKskcZBarygCCe2Xi7sZieoFZuq6ltPoCsdfEdfbO\n"
    b"+VBVKJnExqNZCcFUTEnbH4CldWROOzMS8BGUlkGpa59Sl1t0QcmWlw1EbkeMQNrN\n"
    b"spdR8lobcdNS9bpAJQqSHRZh3cAM9mA3Yq/bssUS/P2quRXLjJ9mIv3dky9C3udM\n"
    b"+q2unvnbNpPtIUly76FJ3s8g8sHeOnmYcKqNGqHq2Q3kMdA2eIbI0MqfOIo2+Xk0\n"
    b"rNt3ctq3g+cQiorcN3rdHPsTRSAcp+NCz1QF9TwXYtH1XV24A6QMO0+CZwARAQAB\n"
    b"tCtEb2NrZXIgUmVsZWFzZSAoQ0UgcnBtKSA8ZG9ja2VyQGRvY2tlci5jb20+iQI3\n"
    b"BBMBCgAhBQJYrep4AhsvBQsJCAcDBRUKCQgLBRYCAwEAAh4BAheAAAoJEMUv62ti\n"
    b"Hp816C0P/iP+1uhSa6Qq3TIc5sIFE5JHxOO6y0R97cUdAmCbEqBiJHUPNQDQaaRG\n"
    b"VYBm0K013Q1gcJeUJvS32gthmIvhkstw7KTodwOM8Kl11CCqZ07NPFef1b2SaJ7l\n"
    b"TYpyUsT9+e343ph+O4C1oUQw6flaAJe+8ATCmI/4KxfhIjD2a/Q1voR5tUIxfexC\n"
    b"/LZTx05gyf2mAgEWlRm/cGTStNfqDN1uoKMlV+WFuB1j2oTUuO1/dr8mL+FgZAM3\n"
    b"ntWFo9gQCllNV9ahYOON2gkoZoNuPUnHsf4Bj6BQJnIXbAhMk9H2sZzwUi9bgObZ\n"
    b"XO8+OrP4D4B9kCAKqqaQqA+O46LzO2vhN74lm/Fy6PumHuviqDBdN+HgtRPMUuao\n"
    b"xnuVJSvBu9sPdgT/pR1N9u/KnfAnnLtR6g+fx4mWz+ts/riB/KRHzXd+44jGKZra\n"
    b"IhTMfniguMJNsyEOO0AN8Tqcl0eRBxcOArcri7xu8HFvvl+e+ILymu4buusbYEVL\n"
    b"GBkYP5YMmScfKn+jnDVN4mWoN1Bq2yMhMGx6PA3hOvzPNsUoYy2BwDxNZyflzuAi\n"
    b"g59mgJm2NXtzNbSRJbMamKpQ69mzLWGdFNsRd4aH7PT7uPAURaf7B5BVp3UyjERW\n"
    b"5alSGnBqsZmvlRnVH5BDUhYsWZMPRQS9rRr4iGW0l+TH+O2VJ8aQ\n"
    b"=0Zqq\n"
    b"-----END PGP PUBLIC KEY BLOCK-----\n"
)

# The inventory: real hosts and their SSH connection information, supplied
# by the application to the library. No privilege section is needed: sudo
# defaults to root, and the demo hosts grant the login user passwordless
# sudo.
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


def deploy(host: Host) -> Generator[OperationRequest]:
    """Import the key stream into root's keychain, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    Without a ``keyid`` both requests import -- gpg deduplicates the key
    material itself, but reemote5 cannot know that controller-side, so
    both stay changed in the apply log too.
    """
    yield op(
        gpg.import_key,
        name="Import demo GPG key",
        src=GPG_KEY,
        sudo=True,
    )
    yield op(
        gpg.import_key,
        name="Import demo GPG key (again)",
        src=GPG_KEY,
        sudo=True,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())