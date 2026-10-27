"""Tests for the deploy-composition helpers (``core.sub_deploy``/``compose``).

Covers the pure stream-transform properties the two-phase model relies on:
meta-parameter inheritance (inner request wins), name prefixing, order-
preserving concatenation, deduplication by effective identity (name
excluded, first occurrence wins), per-invocation purity (so prepare and
apply flatten identically), and end-to-end ``run`` calls through the
determinism guard using the test framework's ``SimulatedHost``.
"""

from collections.abc import Generator

from reemote5.core import OperationRequest, compose, op, sub_deploy
from reemote5.drivers import format_log, format_plan, run
from reemote5.hosts import Host
from reemote5.operations import dnf, files
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
    assert first.sudo is True
    assert second.sudo is True  # inner sudo=True preserved, never removed


def test_sub_deploy_sudo_default_does_not_touch_requests():
    def unprivileged(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, name="install", packages=("curl",))

    (request,) = sub_deploy(unprivileged)(HOST)
    assert request.sudo is False


def test_sub_deploy_wrapper_is_pure_across_invocations():
    wrapped = sub_deploy(role_httpd, name_prefix="httpd", sudo=True)
    assert list(wrapped(HOST)) == list(wrapped(HOST))


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
