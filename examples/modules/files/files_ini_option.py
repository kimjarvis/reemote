# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: set one INI option in a root-owned file with files.ini_option.

This is a *usage* example, not part of the library. ``files.ini_option`` is
Ansible ``ini_file`` parity: it sets one option in one section of an INI file,
preserving every other section, option, comment and blank line. That is the
shape a repository role's "enable/disable the repo" task needs -- the
RPM-shipped ``/etc/yum.repos.d/epel.repo`` carries three sections (``[epel]``,
``[epel-debuginfo]``, ``[epel-source]``), each with its own ``enabled=`` line,
and only the ``[epel]`` one may flip.

Why not the neighbours? ``dnf.repo`` constructs a whole *single-section*
``.repo`` file from its arguments, so it would clobber the other sections;
``files.edit`` is line-scoped, so a bare ``^enabled=`` replace would also hit
``[epel-debuginfo]``/``[epel-source]``. ``files.ini_option`` is
``files.transform`` with the section-scoped edit built in:

1. Read + edit: the operation reads the target's current text with the
   ``FileContents`` fact (``None`` when the file is absent) and applies a
   pure, section-scoped edit -- rewrite the one active option line (``=`` and
   ``:`` separators both recognized), insert a missing option after the
   section's last non-blank line, or append a missing section at EOF.
2. Compare: the edited text is compared against the file's content; when they
   already match, nothing is yielded and the request reports no change.
3. Spool + install: when they differ, the write is delegated to
   ``files.install`` -- an ``InstallSpoolCommand`` transfer copies the text
   over scp to a dest-derived ``/tmp`` spool (unprivileged regardless of the
   request's ``sudo``), then an ``InstallCommand`` shell runs
   ``mkdir -p``/``cp``/optional ``chown``/``chmod``/``rm -rf`` wrapped in
   ``sh -c`` so ``sudo=True`` elevates every step.

Unlike a ``files.transform`` request there is no transform to write: the
section, option and value are plain hashable strings, so the request is
deterministic across the prepare and apply phases by construction. The edit
is a fixpoint -- a line already rendered exactly as requested is returned
verbatim -- so a second identical request is a guaranteed no-op.

``no_extra_spaces=True`` renders ``enabled=1`` (the repo-file style) instead
of Ansible's default ``enabled = 1``; ``create=False`` would mirror
``ini_file``'s ``create: no`` (an absent file raises instead of being
created). The optional ``mode``/``user``/``group``/``validate`` parameters are
forwarded to the install, so the same request that lands the edit can also
set the file's permissions and owner; idempotence stays content-only, so a
mode- or owner-only drift does not re-install (``files.file`` converges
metadata).

The script sets ``enabled=1`` in the ``[demo]`` section of a demo INI file
under ``/etc`` twice with the very same deploy: the first run writes, the
second reads the content back and reports no change on every host. Unlike the
pytest suite -- which injects the test framework's ``SimulatedHost`` via
``run``'s ``host_factory`` and never touches the network -- this example uses
the *default* factory (``Host.from_inventory``): each plain ``Host`` lazily
opens an asyncssh connection, ``cat``s the file for real, and spools +
installs the edited content when it differs.

The inventory is read from ``inventory.json`` in the current working directory; edit it
there to target your machines.

Run it with::

    uv run python examples/modules/files/files_ini_option.py
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
    files,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# The root-owned file to edit: a demo INI under /etc, which the login user
# cannot write directly -- only the elevated install half can. Point this at
# a real INI config (eg /etc/yum.repos.d/epel.repo) on your own hosts.
REMOTE_PATH = "/etc/reemote-ini-demo.ini"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Set the same INI option twice: the second proves idempotence.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases (a branch on a mutable fact would raise DeterminismError). Every
    argument is a plain hashable str/bool -- no transform callable, no IO
    stream -- which is what keeps the two ``OperationRequest`` values equal.
    Each request is flagged ``sudo=True``: the scp spool transfer always rides
    as the login user, and only the install half elevates to overlay the
    ``/etc`` file.
    """
    yield op(
        files.ini_option,
        name="Ensure demo section is enabled",
        path=REMOTE_PATH,
        section="demo",
        option="enabled",
        value="1",
        no_extra_spaces=True,
        mode="0644",
        sudo=True,
    )
    yield op(
        files.ini_option,
        name="Re-ensure demo section is enabled",
        path=REMOTE_PATH,
        section="demo",
        option="enabled",
        value="1",
        no_extra_spaces=True,
        mode="0644",
        sudo=True,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The file is still ``cat``'d over SSH
        # (read-only) so the plan reflects each host's real pre-deployment
        # state; no content is spooled or installed.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # First real run: spool + install on every host whose content differs.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Log: set INI option (first run) ==")
        assert report is not None
        print(format_log(report))

        # Second run of the SAME deploy: FileContents now reads back what the
        # first run installed, so the edit is a fixpoint and every request
        # reports no change.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Log: set INI option (second run, no-op) ==")
        assert report is not None
        print(format_log(report))
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
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())