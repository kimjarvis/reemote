# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Migration of the EPEL repository Ansible role (``geerlingguy.repo-epel``).

The role at ``https://github.com/geerlingguy/ansible-role-repo-epel`` installs
the EPEL (Extra Packages for Enterprise Linux) repository on RHEL-family
hosts: import the EPEL GPG key, install the ``epel-release`` RPM from a URL,
then set ``enabled=`` in the ``[epel]`` section of ``/etc/yum.repos.d/epel.repo``.
This program is that whole role expressed as two reemote deploys. Every role
variable is read from the inventory entry's extra keys (reemote's ``host_vars``
mechanism), so ``epel_repo_disable`` and friends live beside ``host``/
``connection`` in ``inventory.json``.

Target: hosts in the ``\"RedHat\"`` OS family. Every request carries
``when=is_redhat``, and a skipped request is never materialized -- the Alpine
and Ubuntu hosts in the shared inventory are not even contacted. The role's
URLs interpolate ``ansible_facts.distribution_major_version``; that fact read
moves to ``main()`` (``LinuxDistribution``), so EPEL 7/8/9/10 all resolve from
one frozen table rather than an ``include_vars`` dispatch.

The two implementation gaps this migration found are now CLOSED on main
-----------------------------------------------------------------------

**issue #24** (``files: no section-scoped INI option edit``) -- closed by
commit ``91962d1``. The role's ``ini_file: section=epel option=enabled`` task
edits ONE option inside the RPM-shipped three-section ``epel.repo``;
``dnf.repo`` would clobber the other sections and ``files.edit`` is
line-scoped. ``files.ini_option`` is now the faithful mapping: a pure
section-scoped edit over the ``FileContents`` read-back, installed through the
spool-and-install family, content-idempotent. The former workaround (a
hand-rolled ``files.transform`` factory) is gone.

**issue #25** (``dnf: key import is stateless despite the RpmGpgKeys fact``)
-- closed by commit ``17cf140``. ``dnf.key(present=True, keyid=...)`` now gates
the import on the ``RpmGpgKeys`` fact (the ``gpg.import_key`` precedent), so
Ansible's idempotent ``rpm_key: state=present`` is reproduced. The key ids are
pinned per EPEL major in ``EPEL_KEY_IDS``, verified against the published
armored keys (and EPEL 9's against a live CentOS Stream 9 host's rpm database).

Why TWO deploys
---------------

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role's
``stat:`` + ``register: epel_repofile_result`` + ``when: not …stat.exists``
construct reads mutable target state a deploy body may not branch on, so it
becomes a **gather -> snapshot -> factory** step: ``main()`` gathers
``FileExists`` on the repo file, freezes the boolean into the per-host plan,
and ``build_install`` branches on the frozen plan only. The configure deploy
is separate because ``files.ini_option`` edits a file the install deploy
creates -- on a fresh host the repo file does not exist until ``epel-release``
is installed, so the edit must run in a later barrier (and the role's own
``ini_file`` task runs unconditionally, after the guarded install tasks).

Deliberate deviations from Ansible semantics
--------------------------------------------

Recorded at the point of use and in ``docs/examples/roles/ansible-role-repo-epel.md``:

* the GPG key and the ``epel-release`` RPM are fetched **control-side**
  (``files.fetch_url`` / ``files.download``); ``rpm_key``/``yum`` download on
  the target itself. The control machine needs internet egress to
  ``dl.fedoraproject.org``; the host never downloads anything.
* ``yum: name=<URL>.rpm`` becomes ``files.download`` to a ``/tmp`` spool plus
  ``dnf.rpm(src=<spool>)`` plus a spool-cleanup request: ``dnf.rpm`` installs a
  ``.rpm`` that already exists on the host (a URL ``src`` raises), so the
  staging is the documented boundary, not a gap. The cleanup request has no
  Ansible counterpart (yum leaves no local file).
* ``until``/``retries: 5``/``delay: 10`` on the key and RPM tasks are dropped:
  reemote is fail-fast with no per-request retry wrapper. A transient fetch or
  install failure aborts the run; re-run the program to retry.
* ``ignore_errors: "{{ ansible_check_mode }}"`` is out of scope: the dry
  preview (``run(..., dry=True)``) never mutates a host, so there is nothing to
  ignore.
* the key id is **pinned** per EPEL major (``rpm_key`` derives it from the key
  material host-side). A custom ``epel_repo_gpg_key_url`` override still
  imports correctly -- the pinned id simply matches nothing and the import
  proceeds. An EPEL major outside the pinned table is rejected before any host
  is contacted.
* ``ini_option`` idempotence is content-only (the install family's contract);
  a separate ``files.file(mode="0644")`` request converges a mode-only drift,
  which the role's ``ini_file: mode=0644`` does every run.
* every request carries ``sudo=True`` explicitly (the role relies on
  play-level ``become``); run-as users/passwords come from the inventory
  ``privilege`` section, never from an operation.

reemote is the only control-side dependency (the fetches use the standard
library). The target needs ``rpm``/``dnf`` -- stock on any RHEL-family host.

Run it with::

    uv run python examples/roles/ansible-role-repo-epel/ansible-role-repo-epel.py
"""

import asyncio
import io
import json
import posixpath
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    FileExists,
    Host,
    HostConnectionError,
    InventoryError,
    LinuxDistribution,
    OperationRequest,
    PrivilegeError,
    dnf,
    files,
    format_log,
    format_plan,
    gather_fact,
    op,
    run,
    validate_inventory,
)

# --- role constants (defaults/main.yml) ---------------------------------------

#: The role defaults this migration honours (``defaults/main.yml``). The two
#: URL defaults are Jinja2 interpolations of
#: ``ansible_facts.distribution_major_version``, so they are None here and
#: ``resolved_vars`` rebuilds them from the gathered major version unless the
#: user pins an explicit URL.
ROLE_DEFAULTS: dict[str, Any] = {
    "epel_repo_url": None,
    "epel_repo_gpg_key_url": None,
    "epel_repofile_path": "/etc/yum.repos.d/epel.repo",
    "epel_repo_disable": False,
}

#: The role's URL templates, with the Jinja2 interpolation turned into a
#: ``str.format`` field resolved control-side from ``LinuxDistribution``.
RPM_URL_TEMPLATE = (
    "https://dl.fedoraproject.org/pub/epel/epel-release-latest-{major}.noarch.rpm"
)
KEY_URL_TEMPLATE = "https://dl.fedoraproject.org/pub/epel/RPM-GPG-KEY-EPEL-{major}"

#: The EPEL signing keys' rpm short ids (the last 8 hex digits of each GPG
#: fingerprint), pinned so ``dnf.key(keyid=...)`` gates the import on the
#: ``RpmGpgKeys`` fact (issue #25, closed). Verified against the published
#: armored keys at ``KEY_URL_TEMPLATE``; EPEL 9's also matches a live CentOS
#: Stream 9 host's ``gpg-pubkey-3228467c-613798eb``.
EPEL_KEY_IDS: dict[int, str] = {
    7: "352c64e5",
    8: "2f86d6a1",
    9: "3228467c",
    10: "e37ed158",
}


# --- pure helpers -------------------------------------------------------------



def is_redhat(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"RedHat"`` OS family.

    Reads only static inventory data and stays a stable module-level function,
    so both deploy phases reach the same eligibility decision. A skipped
    request is never materialized, so the Alpine and Ubuntu hosts in the shared
    inventory are not contacted at all.
    """
    return host.family == "RedHat"


def _ansible_bool(value: Any, key: str) -> bool:
    """Ansible's ``| bool`` filter: YAML 1.1 booleans, case-insensitive."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)
        raise ValueError(f"{key} must be a boolean, got {value!r}")
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"1", "true", "yes", "on"}:
            return True
        if low in {"0", "false", "no", "off"}:
            return False
    raise ValueError(
        f"{key} must be a boolean (yes/no/true/false/on/off/1/0), got {value!r}"
    )


def role_settings(host: Host) -> dict[str, Any]:
    """Resolve a host's role variables: defaults overlaid with ``host.extra``.

    Validates the flag, the path and the URL overrides before any host is
    contacted, so a bad value fails fast with a clear message rather than
    mid-deploy.
    """
    settings = {**ROLE_DEFAULTS, **host.extra}
    settings["epel_repo_disable"] = _ansible_bool(
        settings["epel_repo_disable"], "epel_repo_disable"
    )
    path = settings["epel_repofile_path"]
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(f"epel_repofile_path must be an absolute path, got {path!r}")
    for key in ("epel_repo_url", "epel_repo_gpg_key_url"):
        url = settings[key]
        if url is not None and (not isinstance(url, str) or not url):
            raise ValueError(f"{key} must be a non-empty string or null, got {url!r}")
    return settings


def resolved_vars(settings: dict[str, Any], major: int) -> dict[str, Any]:
    """The plan's static half: the resolved URLs, key id and enabled value.

    ``major`` is the gathered ``LinuxDistribution`` major version -- the
    ``ansible_facts.distribution_major_version`` the role's URL defaults
    interpolate. An explicit user URL wins over the template (Ansible's
    variable precedence). The ``epel_repo_disable | ternary(0, 1)`` filter
    becomes the ``enabled`` value text. Everything here is a hashable scalar,
    frozen control-side before the deploy.
    """
    if major not in EPEL_KEY_IDS:
        raise ValueError(
            f"no pinned EPEL key id for major version {major}; "
            f"supported: {tuple(sorted(EPEL_KEY_IDS))}"
        )
    return {
        "repofile": settings["epel_repofile_path"],
        "enabled": "0" if settings["epel_repo_disable"] else "1",
        "rpm_url": settings["epel_repo_url"] or RPM_URL_TEMPLATE.format(major=major),
        "key_url": settings["epel_repo_gpg_key_url"]
        or KEY_URL_TEMPLATE.format(major=major),
        "keyid": EPEL_KEY_IDS[major],
    }


# --- the deploy factories (close over the frozen per-host plans) --------------


def build_install(
    plans: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 1: the role's guarded key-import and RPM-install tasks.

    Each plan freezes the role's ``stat`` register (``repo_exists`` from
    ``FileExists``) beside the resolved variables, so the deploy branches on
    frozen data only -- the gather -> snapshot -> factory recipe for
    ``register`` + ``when``. A host whose repo file already exists (the role's
    ``when: not epel_repofile_result.stat.exists``) yields nothing, which is
    what makes the second program pass a no-op.

    The key stream is the ``io.BytesIO`` ``main()`` built once from the
    control-side fetch: streams are compared by identity, so it must be the
    same object in every phase. ``keyid`` gates the import on ``RpmGpgKeys``
    (issue #25, closed) -- Ansible's ``rpm_key state=present`` idempotence.
    """

    def install(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None or plan["repo_exists"]:
            return
        yield op(
            dnf.key,
            name="Import EPEL GPG key.",
            src=plan["key_stream"],
            keyid=plan["keyid"],
            sudo=True,
            when=is_redhat,
        )
        # yum downloads the URL on the target; dnf.rpm installs a .rpm that
        # already exists on the host, so stage it control-side first. The
        # 0644 spool mode keeps the file login-readable for the binary sha1
        # read-back (issue #23) and the RpmPackage fact's rpm -qp query.
        yield op(
            files.download,
            name="Stage the EPEL release RPM.",
            url=plan["rpm_url"],
            dest=plan["rpm_spool"],
            mode="0644",
            sudo=True,
            when=is_redhat,
        )
        yield op(
            dnf.rpm,
            name="Install the EPEL release RPM.",
            src=plan["rpm_spool"],
            sudo=True,
            when=is_redhat,
        )
        # No Ansible counterpart: yum leaves no local file. The spool is
        # removed so a later pass never installs from a stale staged RPM.
        yield op(
            files.file,
            name="Remove the staged EPEL release RPM.",
            path=plan["rpm_spool"],
            present=False,
            sudo=True,
            when=is_redhat,
        )

    return install


def build_configure(
    plans: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 2: the role's unconditional ``ini_file`` enabled toggle.

    ``files.ini_option`` (issue #24, closed) is the faithful ``ini_file``
    mapping: one option in one section, every other section/comment/blank
    line preserved, ``no_extra_spaces=True`` rendering ``enabled=<v>``,
    content-idempotent. ``create=False`` mirrors the role's ``ini_file``
    default (``create: no``): the file must exist -- it does, either
    pre-existing or installed by deploy 1's barrier.

    The trailing ``files.file(mode="0644")`` converges a mode-only drift:
    install-family idempotence is content-only, while the role's
    ``ini_file: mode=0644`` re-applies the mode every run.
    """

    def configure(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return
        yield op(
            files.ini_option,
            name="Ensure EPEL repo is enabled / disabled.",
            path=plan["repofile"],
            section="epel",
            option="enabled",
            value=plan["enabled"],
            no_extra_spaces=True,
            create=False,
            mode="0644",
            sudo=True,
            when=is_redhat,
        )
        yield op(
            files.file,
            name="Ensure EPEL repo file mode is 0644.",
            path=plan["repofile"],
            mode="0644",
            sudo=True,
            when=is_redhat,
        )

    return configure


async def execute(inventory: list[dict[str, Any]], deploy: Any, title: str) -> Any:
    """Dry-run then really run one deploy, printing both renderings.

    The dry run is Ansible's ``--check`` as a return value: it stops after
    prepare, so the plan shows what *would* change without touching a host.
    Both runs use the very same deploy object -- for a factory-built deploy
    that means the same frozen snapshot, which is what makes the determinism
    guard hold.
    """
    plans, _dry, _hosts = await run(inventory, deploy, dry=True)
    print(f"\n== {title} -- plan (prepare phase, --dry) ==")
    print(format_plan(plans))
    _plans, report, _hosts = await run(inventory, deploy)
    print(f"\n== {title} -- run log (apply phase) ==")
    assert report is not None
    print(format_log(report))
    return report


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        hosts = [Host.from_inventory(entry) for entry in inventory]
        targets = [host for host in hosts if is_redhat(host)]
        skipped = [host.name for host in hosts if not is_redhat(host)]
        if skipped:
            print("\n== Skipped (when guard is_redhat): " + ", ".join(skipped) + " ==")
        if not targets:
            print("\nNo host in the RedHat family; nothing to deploy.")
            return

        # The role's variable resolution, before any host is contacted.
        settings = {host.name: role_settings(host) for host in targets}

        # The role's fact reads, frozen: LinuxDistribution supplies the
        # distribution_major_version the URL defaults interpolate (and guards
        # the RHEL-family assumption); FileExists is the stat register the
        # install tasks' when guard reads. gather_fact does not close its
        # hosts, so the finally does.
        plans: dict[str, Any] = {}
        try:
            distributions = await gather_fact(targets, LinuxDistribution)
            for host in targets:
                info = distributions[host.name]
                if info.get("family") != "RedHat":
                    raise ValueError(
                        f"{host.name}: declared RedHat family but the distribution is "
                        f"{info.get('name')!r} (family {info.get('family')!r}); "
                        "the EPEL role targets the RHEL family"
                    )
                major = info.get("major")
                if not isinstance(major, int):
                    # TypeError (not ValueError) for a type check -- ruff
                    # TRY004; main()'s except tuple covers both.
                    raise TypeError(
                        f"{host.name}: cannot read the distribution major "
                        f"version from {info!r}"
                    )
                rv = resolved_vars(settings[host.name], major)
                exists = (await gather_fact([host], FileExists, rv["repofile"]))[
                    host.name
                ]
                plans[host.name] = {**rv, "major": major, "repo_exists": bool(exists)}
        finally:
            for host in targets:
                await host.aclose()

        # The control-side key fetch, only for hosts whose repo file is absent
        # (the role's when guard). Each stream is built ONCE here: op() compares
        # streams by identity, so a fresh BytesIO per phase would trip the
        # determinism guard. The RPM spool path is deterministic (the URL's
        # basename under /tmp), identical in both phases.
        for host in targets:
            plan = plans[host.name]
            plan["rpm_spool"] = "/tmp/" + posixpath.basename(plan["rpm_url"])
            if plan["repo_exists"]:
                plan["key_stream"] = None
            else:
                key_text = await files.fetch_url(plan["key_url"])
                plan["key_stream"] = io.BytesIO(key_text.encode("utf-8"))
        print("\n== Frozen EPEL plans ==")
        for host in targets:
            plan = plans[host.name]
            state = "present" if plan["repo_exists"] else "absent, will install"
            print(
                f"{host.name}: EPEL {plan['major']} -> {plan['repofile']} "
                f"({state}), enabled={plan['enabled']}"
            )

        # Deploy 1 installs (guarded); deploy 2 edits the file deploy 1 may
        # have created, so it must run in a later barrier. On a converged host
        # deploy 1 yields nothing and every deploy-2 request is a no-op.
        report_install = await execute(
            inventory,
            build_install(plans),
            "Deploy 1: import the EPEL key and install epel-release",
        )
        report_configure = await execute(
            inventory,
            build_configure(plans),
            "Deploy 2: set the EPEL repo enabled flag",
        )
        changed = sum(
            1
            for report in (report_install, report_configure)
            for entry in report.log
            if entry.changed
        )
        print(f"\n== Done: {changed} changed request(s) across 2 deploys ==")
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        # The inventory is missing/unreadable/not JSON, a control-side fetch
        # failed (URLError/HTTPError are OSError subclasses), or a role
        # variable / distribution read is invalid. The message is the report.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema; the
        # message names the offending entry by its list index.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # Every host-level failure is a typed error naming the host and the
        # failing command. Print that, no traceback, and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
