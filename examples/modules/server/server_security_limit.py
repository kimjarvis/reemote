# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure a resource limit (pyinfra server.security_limit parity).

This is a *usage* example, not part of the library. It is reemote5's
answer to pyinfra's bespoke ``server.security_limit`` operation: no
per-format editor is ported, because the generic ``files.edit``
content-transform primitive plus an elevated install step cover the task
-- the same spool-and-install shape as ``server_op_locale.py`` and
``server_op_sysctl.py``, adjusted for a stock system file (see the
install vehicle below).

pyinfra's ``security_limit(domain, limit_type, item, value)`` delegates
to ``files.line`` on ``/etc/security/limits.conf``: it searches the
anchored line ``^{domain}[[:space:]]+{limit_type}[[:space:]]+{item}.*$``
(the value is NOT part of the pattern) and replaces every matching line
wholesale with the tab-formatted ``domain\\tlimit_type\\titem\\tvalue``,
or appends that line when nothing matches. This recipe mirrors all of
that with pure Python over a content snapshot:

1. A fact SNAPSHOT: ``gather_fact`` reads ``FileContents`` of
   ``/etc/security/limits.conf`` from every host (a read-only SSH
   ``cat``). The pure ``security_limit_edit`` function performs pyinfra's
   ``files.line`` lookup with a Python regex over the content --
   reemote5 ships no grep-style ``FindInFile`` fact, and ``FileContents``
   plus local parsing is its content-read convention.
2. One skip gate on the frozen snapshot: the transform's result compared
   against the file's current text (pyinfra's ``files.line`` no-ops when
   a matching line already reads exactly the replacement). Hosts that
   already carry the limit are skipped entirely -- the script-level
   idempotence pyinfra gets from its fact short-circuit lives here, in
   the gap between runs.
3. One deploy, two steps: ``files.edit`` spools the desired content to
   ``/tmp/limits.conf`` (login-user-writable) through a ``make_transform``
   closure frozen between the snapshot and the run, then the demo-side
   stateless ``limits_install`` operation runs the elevated
   ``sh -c 'mv ... && chown root:root ... && chmod 0644 ...'`` install as
   a raw ``StringCommand`` (the library ships no generic shell operation).
   The lockstep barrier completes every host's spool write before any
   host installs.

Why ``files.move`` is deliberately NOT the install vehicle here: ``mv``
preserves the spool's owner and mode, so an elevated move would leave
this stock system file ``-rw------- kjarvis:kjarvis`` instead of
``root:root 0644`` -- ``pam_limits`` reads ``/etc/security/limits.conf``
as every user, so the limits would silently stop applying. The
``sh -c`` install resets ownership and mode inside the same elevated
step, the convention the ``systemd.dropin`` and ``sysctl.conf``
operations follow internally. A side benefit: that step is stateless (no
source validation in the prepare phase), so -- unlike the
``files.move``-based ``files_edit_etc_hosts.py`` recipe -- spool and
install can share one deploy.

Deliberate deviations from pyinfra, decided at migration time:

* The ``files.line`` sed/echo edit is replaced by the ``files.edit``
  transform: reemote5 ports no per-format file editors, and its
  transfers are unprivileged, so root-owned ``/etc/security/limits.conf``
  is written through the spool-and-install vehicle. pyinfra's
  timestamped sed backup dance goes with it.
* The match regex escapes domain, type and item literally
  (``re.escape``); pyinfra interpolates them raw into a BRE, where they
  are regex metacharacters. The POSIX ``[[:space:]]`` class is
  translated verbatim (minus the newline -- matching is per line), and
  the trailing ``.*$`` pyinfra's ``adjust_regex`` appends is kept: like
  pyinfra, an item prefix matches (``* soft nofilex 5`` is replaced) and
  a commented ``# * soft nofile`` line does NOT (the pattern is anchored).
* Because the value is not part of the pattern -- pyinfra parity -- a
  stale ``* soft nofile 65535`` line MATCHES and is normalized in place,
  unlike ``server.sysctl``'s keyed ``key = value`` match.
* The desired content always ends in one trailing newline (the recipe's
  ``"\\n".join(lines) + "\\n"`` house style), so a file missing its final
  newline is rewritten once even when the line itself is correct.
* The pyinfra operation's ``value`` is typed ``int`` but its own test
  fixture passes ``"unlimited"``; this recipe accepts both.

Re-running the whole script is a no-op once every host's limits.conf
carries the line: the snapshot gate skips the run entirely.

**This recipe rewrites the root-owned ``/etc/security/limits.conf`` on
every pending host.** It is compile-checked and exercised by the offline
example harness rather than run against shared hosts; point
``INVENTORY`` at your own throwaway machines to execute it for real.

Run it with::

    uv run python examples/modules/server/server_security_limit.py
"""

import asyncio
import re
import shlex
import sys
from collections.abc import AsyncGenerator, Callable, Generator
from typing import Any

from reemote5 import (
    Command,
    CommandError,
    FileContents,
    Host,
    HostConnectionError,
    OpContext,
    OperationRequest,
    PrivilegeError,
    StringCommand,
    files,
    format_log,
    format_plan,
    gather_fact,
    op,
    run,
)

# The limit to ensure: pyinfra's documented example ("Set nofile limit
# for all users"), and the file it edits.
DOMAIN = "*"
LIMIT_TYPE = "soft"
ITEM = "nofile"
VALUE: int | str = 1024
LIMITS_FILE = "/etc/security/limits.conf"

# The spool path: login-user-writable, and its basename ("limits.conf")
# is what the install step names inside the target directory.
SPOOL_PATH = "/tmp/limits.conf"

# Per-host spool transforms, filled ONCE in main between the snapshot and
# the run: each is a stable callable closing over frozen desired content,
# so both phases pass the identical object (OperationRequest compares the
# transform by value; a fresh closure per phase would raise
# DeterminismError).
SPOOL_TRANSFORMS: dict[str, Callable[[str | None], str]] = {}

# The inventory: real hosts and their SSH connection information. No
# ``privilege`` entry: the elevated install assumes passwordless sudo
# for the login user; a password would come from ``privilege`` here,
# never the op.
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


def limit_line(domain: str, limit_type: str, item: str, value: int | str) -> str:
    """The tab-separated limits.conf line pyinfra's operation formats."""
    return f"{domain}\t{limit_type}\t{item}\t{value}"


def security_limit_edit(
    content: str | None,
    domain: str,
    limit_type: str,
    item: str,
    value: int | str,
) -> str:
    """Pure limits.conf edit: ensure the ``domain type item value`` line.

    Mirrors the ``files.line`` call ``server.security_limit`` makes:
    search ``^{domain}[[:space:]]+{limit_type}[[:space:]]+{item}.*$``
    (anchored, value NOT included), replacement the tab-formatted
    ``limit_line``. Every matching line is normalized in place -- a
    stale value line MATCHES and is corrected, and an item prefix like
    ``nofilex`` matches too (pyinfra's ``.*$``); a commented-out line
    does not (the anchor). With no match the line is appended (an absent
    file, ``None`` content, reads as empty, pyinfra's echo creating it).
    The fields are escaped literally (``re.escape``), the documented
    hardening over pyinfra's raw BRE interpolation. The result always
    ends in one trailing newline and the edit is a fixpoint.
    """
    pattern = re.compile(
        rf"^{re.escape(domain)}[ \t\r\f\v]+{re.escape(limit_type)}"
        rf"[ \t\r\f\v]+{re.escape(item)}.*$"
    )
    replace = limit_line(domain, limit_type, item, value)
    lines = (content or "").splitlines()
    matches = [i for i, line in enumerate(lines) if pattern.search(line)]
    if not matches:
        lines.append(replace)
    else:
        for i in matches:
            lines[i] = replace
    return "\n".join(lines) + "\n"


def make_transform(content: str) -> Callable[[str | None], str]:
    """Build a spool transform returning the frozen desired ``content``.

    Called once per host between the snapshot and the run; the returned
    callable ignores the spool file's current text (the desired content
    was already computed from the real limits.conf) and is trivially a
    fixpoint.
    """

    def transform(current: str | None) -> str:
        return content

    return transform


async def limits_install(
    ctx: OpContext,
    spool_path: str,
    target_path: str,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install the spooled file as root-owned ``0644`` (demo-side step).

    pyinfra's operation needs no equivalent step -- its sed edits in
    place -- but the unprivileged spool cannot: ``mv`` alone would
    preserve the spool's ``kjarvis`` owner and mode, and a
    ``pam_limits``-read system file must land ``root:root 0644``. So
    this example defines the elevated one-liner itself over the public
    raw ``StringCommand`` descriptor, the ``systemd.dropin``/
    ``sysctl.conf`` install shape. Like those steps it is stateless: it
    reads no fact and always reports a change, and the snapshot gate
    upstream decides whether the deploy runs at all. The command needs
    root, so flag the request ``sudo=True``. ``**kwargs`` absorbs
    pyinfra's global arguments (``op()`` already strips
    ``name``/``sudo``/``su``).
    """
    script = (
        f"mv -f {shlex.quote(spool_path)} {shlex.quote(target_path)} && "
        f"chown root:root {shlex.quote(target_path)} && "
        f"chmod 0644 {shlex.quote(target_path)}"
    )
    ctx.changed = True
    ctx.reason = f"will install {spool_path} over {target_path}"
    yield StringCommand(f"sh -c {shlex.quote(script)}")


def deploy(host: Host) -> Generator[OperationRequest]:
    """Spool the edited content, then install it elevated.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases; the transform frozen in main between the snapshot and
    this run carries the idempotence decision.
    """
    yield op(
        files.edit,
        name="Spool limits.conf",
        path=SPOOL_PATH,
        transform=SPOOL_TRANSFORMS[host.name],
    )
    yield op(
        limits_install,
        name="Install limits.conf",
        spool_path=SPOOL_PATH,
        target_path=LIMITS_FILE,
        sudo=True,
    )


async def main() -> None:
    try:
        # The snapshot: read every host's real /etc/security/limits.conf
        # (read-only cat).
        hosts = [Host.from_inventory(entry) for entry in INVENTORY]
        try:
            contents = await gather_fact(hosts, FileContents, LIMITS_FILE)
        finally:
            for host in hosts:
                await host.aclose()

        # Controller-side idempotence: compute each host's desired content
        # from the frozen snapshot and skip hosts that already match.
        pending = []
        for entry in INVENTORY:
            name = entry["host"]
            current = contents[name] or ""
            desired = security_limit_edit(current, DOMAIN, LIMIT_TYPE, ITEM, VALUE)
            if desired != current:
                SPOOL_TRANSFORMS[name] = make_transform(desired)
                pending.append(entry)
        if not pending:
            print("All hosts already carry the desired limits.conf line.")
            return

        # --dry: prepare phase only; the install's mv really needs the
        # spool, which prepare never sends, so the gate above already
        # decided which hosts run at all.
        plans, _, _hosts = await run(pending, deploy, dry=True)
        print("== Plan: spool and install limits.conf (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(pending, deploy)
        print("\n== Log: spool and install limits.conf ==")
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
