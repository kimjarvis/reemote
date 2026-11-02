# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the deploy-composition helpers (``core.sub_deploy``/``compose``).

Covers the pure stream-transform properties the two-phase model relies on:
meta-parameter inheritance (inner request wins), name prefixing, order-
preserving concatenation, deduplication by effective identity (name
excluded, first occurrence wins), per-invocation purity (so prepare and
apply flatten identically), and end-to-end ``run`` calls through the
determinism guard using the test framework's ``SimulatedHost``.
"""

from collections.abc import Generator

from reemote5.core import OperationRequest, Privilege, compose, op, sub_deploy
from reemote5.drivers import format_log, format_plan, run
from reemote5.hosts import Host
from reemote5.modules import dnf, files
from tests.simulated_host import SimulatedHost

HTTPD_WELCOME_CONF = "/etc/httpd/conf.d/welcome.conf"

INVENTORY = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]

HOST = Host.from_inventory(INVENTORY[0])


def simulated_factory(entry: dict) -> SimulatedHost:
    """``host_factory`` whose httpd install ships the welcome configuration.

    The side effect is what makes the composed end-to-end run interesting:
    the removal operation only reports a change because the *previous*
    lockstep step created the file (fresh per-step FactCache).
    """
    host = SimulatedHost.from_inventory(entry)
    host.installs_files = {"httpd": {HTTPD_WELCOME_CONF}}
    return host


def role_baseline(host: Host) -> Generator[OperationRequest]:
    yield op(
        dnf.packages, name="Install baseline tools", packages=("curl",), sudo=True
    )


def role_httpd(host: Host) -> Generator[OperationRequest]:
    """Independently written role that re-declares its baseline prerequisite."""
    yield op(
        dnf.packages, name="Install baseline tools", packages=("curl",), sudo=True
    )
    yield op(dnf.packages, name="Install httpd", packages=("httpd",), sudo=True)
    yield op(
        files.file,
        name="Remove welcome configuration",
        src=HTTPD_WELCOME_CONF,
        present=False,
    )


# --- sub_deploy: pure request-stream transform --------------------------------


def test_sub_deploy_without_options_passes_requests_through():
    wrapped = sub_deploy(role_baseline)
    assert list(wrapped(HOST)) == list(role_baseline(HOST))


def test_sub_deploy_prefixes_names_and_falls_back_to_func_name():
    def unnamed(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, packages=("curl",))

    (request,) = sub_deploy(unnamed, name_prefix="base")(HOST)
    assert request.name == "base: packages"

    (request,) = sub_deploy(role_baseline, name_prefix="base")(HOST)
    assert request.name == "base: Install baseline tools"


def test_sub_deploy_sudo_elevates_only_unflagged_requests():
    def mixed(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, name="unprivileged", packages=("curl",))
        yield op(files.file, name="privileged", src="/tmp/x", sudo=True)

    first, second = sub_deploy(mixed, sudo=True)(HOST)
    assert first.sudo is Privilege.ON
    assert second.sudo is Privilege.ON  # inner sudo=True preserved, never removed


def test_sub_deploy_sudo_default_does_not_touch_requests():
    def unprivileged(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, name="install", packages=("curl",))

    (request,) = sub_deploy(unprivileged)(HOST)
    assert request.sudo is Privilege.UNSET


def test_sub_deploy_wrapper_is_pure_across_invocations():
    wrapped = sub_deploy(role_httpd, name_prefix="httpd", sudo=True)
    assert list(wrapped(HOST)) == list(wrapped(HOST))


# --- Privilege tri-state: forced elevation with an explicit opt-out --------------


def test_privilege_coerce_maps_bools_and_none():
    assert Privilege.coerce(True) is Privilege.ON
    assert Privilege.coerce(False) is Privilege.OFF
    assert Privilege.coerce(None) is Privilege.UNSET
    assert Privilege.coerce(Privilege.ON) is Privilege.ON
    assert Privilege.coerce(Privilege.OFF) is Privilege.OFF
    assert Privilege.coerce(Privilege.UNSET) is Privilege.UNSET


def test_privilege_elevates_only_for_on():
    assert Privilege.ON.elevates() is True
    assert Privilege.OFF.elevates() is False
    assert Privilege.UNSET.elevates() is False


def test_op_default_is_unset_and_flags_coerce():
    request = op(dnf.packages, packages=("curl",))
    assert request.sudo is Privilege.UNSET
    assert request.su is Privilege.UNSET
    assert request.doas is Privilege.UNSET
    assert op(dnf.packages, packages=("curl",), sudo=True).sudo is Privilege.ON
    assert op(dnf.packages, packages=("curl",), sudo=False).sudo is Privilege.OFF
    assert op(dnf.packages, packages=("curl",), doas=True).doas is Privilege.ON
    assert op(dnf.packages, packages=("curl",), doas=False).doas is Privilege.OFF
    assert (
        op(dnf.packages, packages=("curl",), sudo=Privilege.ON).sudo
        is Privilege.ON
    )


def test_sub_deploy_forced_elevation_honours_explicit_opt_out():
    """The tri-state's reason for existing: ``sudo=False`` survives a forced policy."""

    def role(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, name="unflagged", packages=("curl",))
        yield op(dnf.packages, name="opted-out", packages=("vim",), sudo=False)
        yield op(dnf.packages, name="explicit", packages=("git",), sudo=True)

    unflagged, opted_out, explicit = sub_deploy(role, sudo=True)(HOST)
    assert unflagged.sudo is Privilege.ON  # UNSET filled by the policy
    assert opted_out.sudo is Privilege.OFF  # deliberate opt-out survives
    assert explicit.sudo is Privilege.ON  # already ON


def test_sub_deploy_off_policy_forces_unset_unprivileged_but_keeps_inner_on():
    def role(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, name="unflagged", packages=("curl",))
        yield op(dnf.packages, name="explicit", packages=("git",), sudo=True)

    unflagged, explicit = sub_deploy(role, sudo=Privilege.OFF)(HOST)
    assert unflagged.sudo is Privilege.OFF  # UNSET filled with OFF
    assert explicit.sudo is Privilege.ON  # inner ON wins over the policy


def test_sub_deploy_su_forced_elevation_honours_opt_out_and_leaves_sudo_alone():
    def role(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, name="unflagged", packages=("curl",))
        yield op(dnf.packages, name="opted-out", packages=("vim",), su=False)

    unflagged, opted_out = sub_deploy(role, su=Privilege.ON)(HOST)
    assert unflagged.su is Privilege.ON
    assert opted_out.su is Privilege.OFF
    # An su-only policy never touches the independent sudo flag.
    assert unflagged.sudo is Privilege.UNSET
    assert opted_out.sudo is Privilege.UNSET


def test_sub_deploy_doas_forced_elevation_honours_opt_out_and_leaves_others_alone():
    def role(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, name="unflagged", packages=("curl",))
        yield op(dnf.packages, name="opted-out", packages=("vim",), doas=False)

    unflagged, opted_out = sub_deploy(role, doas=Privilege.ON)(HOST)
    assert unflagged.doas is Privilege.ON
    assert opted_out.doas is Privilege.OFF
    # A doas-only policy never touches the independent sudo/su flags.
    assert unflagged.sudo is Privilege.UNSET
    assert unflagged.su is Privilege.UNSET
    assert opted_out.sudo is Privilege.UNSET
    assert opted_out.su is Privilege.UNSET


# --- compose: ordering and effective-identity deduplication --------------------


def test_compose_concatenates_in_order():
    deploy = compose(role_baseline, role_httpd)
    requests = list(deploy(HOST))
    assert len(requests) == 4
    assert requests[0] == requests[1]  # the shared baseline request
    assert requests[2].name == "Install httpd"
    assert requests[3].name == "Remove welcome configuration"


def test_compose_dedupe_collapses_value_equal_requests():
    deploy = compose(role_baseline, role_httpd, dedupe=True)
    requests = list(deploy(HOST))
    assert [r.name for r in requests] == [
        "Install baseline tools",
        "Install httpd",
        "Remove welcome configuration",
    ]


def test_compose_dedupe_collapses_name_only_differences_first_name_wins():
    def same_op_different_names(host: Host) -> Generator[OperationRequest]:
        yield op(
            dnf.packages, name="Install baseline tools", packages=("curl",), sudo=True
        )
        yield op(
            dnf.packages, name="Ensure curl is present", packages=("curl",), sudo=True
        )

    deploy = compose(same_op_different_names, dedupe=True)
    requests = list(deploy(HOST))
    assert len(requests) == 1
    assert requests[0].name == "Install baseline tools"


def test_compose_dedupe_distinguishes_requests_by_sudo():
    def same_op_different_sudo(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, name="Install curl", packages=("curl",))
        yield op(dnf.packages, name="Install curl", packages=("curl",), sudo=True)

    deploy = compose(same_op_different_sudo, dedupe=True)
    assert len(list(deploy(HOST))) == 2


def test_compose_dedupe_distinguishes_requests_by_doas():
    def same_op_different_doas(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, name="Install curl", packages=("curl",))
        yield op(dnf.packages, name="Install curl", packages=("curl",), doas=True)

    deploy = compose(same_op_different_doas, dedupe=True)
    assert len(list(deploy(HOST))) == 2


def test_compose_dedupe_collapses_off_and_unset_as_both_unprivileged():
    """Dedupe keys on the *resolved* elevation, so OFF and UNSET collapse.

    Both execute unprivileged, so they are one effective operation; ON changes
    what executes and stays a separate plan entry.
    """

    def role(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, name="unset", packages=("curl",))
        yield op(dnf.packages, name="off", packages=("curl",), sudo=False)
        yield op(dnf.packages, name="on", packages=("curl",), sudo=True)

    requests = list(compose(role, dedupe=True)(HOST))
    assert [r.name for r in requests] == ["unset", "on"]


def test_compose_dedupe_collapses_prefixed_duplicates():
    """``name`` is excluded from the dedupe key, so prefixes no longer matter."""
    deploy = compose(
        role_baseline,
        sub_deploy(role_baseline, name_prefix="httpd"),
        dedupe=True,
    )
    requests = list(deploy(HOST))
    assert [r.name for r in requests] == ["Install baseline tools"]


def test_compose_dedupe_state_does_not_leak_between_invocations():
    deploy = compose(role_baseline, role_httpd, dedupe=True)
    assert list(deploy(HOST)) == list(deploy(HOST))


# --- End to end: composed deploys pass the determinism guard ----------------------


async def test_composed_deploy_runs_end_to_end_with_dedupe():
    deploy = compose(role_baseline, role_httpd, dedupe=True)
    plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=simulated_factory
    )

    assert [p.request.name for p in plans["web1"]] == [
        "Install baseline tools",
        "Install httpd",
        "Remove welcome configuration",
    ]
    assert report is not None
    assert all(entry.changed for entry in report.log)
    # The removal saw the file the install created in the previous step.
    assert hosts[0].packages == {"curl", "httpd"}
    assert HTTPD_WELCOME_CONF not in hosts[0].existing_files


async def test_sub_deploy_meta_parameters_flow_into_plan_and_log():
    def unprivileged(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, name="Install httpd", packages=("httpd",))

    deploy = compose(sub_deploy(unprivileged, name_prefix="httpd", sudo=True))
    plans, report, _hosts = await run(
        INVENTORY, deploy, host_factory=simulated_factory
    )

    assert "httpd: Install httpd" in format_plan(plans)
    assert report is not None
    entry = report.log[0]
    assert entry.name == "httpd: Install httpd"
    assert entry.sudo is True
    assert "$ sudo dnf install -y httpd" in format_log(report)


async def test_forced_elevation_with_opt_out_flows_into_the_log():
    """End to end: a forced-elevation policy elevates all but the explicit opt-out."""

    def role(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, name="unflagged", packages=("curl",))
        yield op(dnf.packages, name="opted-out", packages=("vim",), sudo=False)
        yield op(dnf.packages, name="explicit", packages=("git",), sudo=True)

    deploy = compose(sub_deploy(role, sudo=Privilege.ON))
    _plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=simulated_factory
    )

    assert report is not None
    by_name = {entry.name: entry for entry in report.log}
    # UNSET (filled by the policy) and explicit ON both elevate; the deliberate
    # opt-out stays unprivileged even under the forced-elevation policy.
    assert by_name["unflagged"].sudo is True
    assert by_name["explicit"].sudo is True
    assert by_name["opted-out"].sudo is False

    log = format_log(report)
    assert "$ sudo dnf install -y curl" in log
    assert "$ sudo dnf install -y git" in log
    assert "$ dnf install -y vim" in log
    assert "$ sudo dnf install -y vim" not in log
    assert hosts[0].packages == {"curl", "vim", "git"}


async def test_doas_meta_parameter_flows_into_plan_and_log():
    """End to end: a ``doas=True`` request resolves to the outermost ``doas -n``
    prefix in the apply log, with no doas_user in the (empty) inventory
    privilege section so it collapses to a bare ``doas -n``."""

    def role(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, name="Install httpd", packages=("httpd",), doas=True)

    deploy = compose(role)
    _plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=simulated_factory
    )

    assert report is not None
    entry = report.log[0]
    assert entry.doas is True
    assert entry.sudo is False
    assert entry.su is False
    assert "$ doas -n dnf install -y httpd" in format_log(report)
    assert hosts[0].packages == {"httpd"}
