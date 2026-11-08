# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Dearmor an ASCII-armored key file already on the target hosts.

This is a *usage* example, not part of the library. It drives reemote5's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``gpg.dearmor`` converts an armored key file to binary keyring form with
``gpg --batch --dearmor``. Both ``src`` and ``dest`` are paths on the
*target* host -- the dearmor request itself moves no bytes over the
connection (to install a key from the control machine in one request, use
``gpg.key``, which dearmors armored streams itself). One request yields
one ``sh -c`` that creates the destination's parent directory, dearmors
``src`` into ``dest`` and chmods the result ``0644``. Like pyinfra's
``gpg.dearmor`` the operation is deliberately **stateless**: it always
converts and always reports a change, on every run, even when ``dest``
already exists.

The armored source must already exist on the target, so the deploy opens
with a ``files.put`` request that uploads a real public key -- the Docker
CE release signing key for CentOS/rpm, embedded as a module-level stream
below -- to a ``/tmp`` spool path. Transfers always ride scp as the login
user (never elevated), so the spool must be login-writable; the dearmor
request then converts it into ``/etc/apt/keyrings/`` under ``sudo=True``.
That makes the example self-contained: the dearmor always finds a valid
armored file to convert. Because the operation is stateless and ``gpg
--batch --dearmor`` refuses to overwrite an existing output, a
``files.file`` request with ``present=False`` clears any stale ``dest``
first, so the example converges on repeated sweeps too.

One host-side caveat: both the stale-``dest`` cleanup (``rm``) and the
dearmor's output write target ``/etc/apt/keyrings/`` (``etc_t``), which a
CentOS host's SELinux ``staff_t``-confined sudo can neither create nor
unlink, so the apply is denied there until the host's sudo is unconfined
(or SELinux is permissive) -- the same confinement the ``selinux``,
``sysctl`` and ``dnf.key`` examples document. The dry-run plan and the
pytest suite pass regardless; ``gpg.import_key`` (root's keychain, not
``/etc``) is unaffected and applies for real.

Run it with::

    uv run python examples/modules/gpg/gpg_dearmor.py
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
    files,
    format_log,
    format_plan,
    gpg,
    op,
    run,
)

# The login-writable spool path the armored key is uploaded to, and the
# binary keyring to write under /etc/apt/keyrings. The files.put transfer
# rides scp as the login user (never elevated), so src must be writable by
# that user; the dearmor request itself moves no bytes over the connection.
SRC = "/tmp/reemote5-demo.asc"
DEST = "/etc/apt/keyrings/reemote5-demo.gpg"

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
    """Upload the armored key, then dearmor it into a binary keyring.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases. The upload spools to login-writable ``/tmp`` (transfers
    are never elevated); the dearmor writes under ``/etc/apt/keyrings/``
    and is flagged ``sudo=True``.
    """
    yield op(
        files.put,
        name="Upload the armored demo key",
        src=GPG_KEY,
        dest=SRC,
    )
    yield op(
        files.file,
        name="Clear any stale keyring",
        src=DEST,
        present=False,
        sudo=True,
    )
    yield op(
        gpg.dearmor,
        name="Dearmor demo GPG key",
        src=SRC,
        dest=DEST,
        sudo=True,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. The operation reads no fact (it is
        # stateless), so the plan always shows the conversion as a change;
        # no command that mutates a host is sent.
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