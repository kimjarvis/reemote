# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for privilege escalation error handling (``PrivilegeError``).

The mirror image of ``test_connection_errors.py``: a rejected sudo credential is
reported the same way an unreachable host is -- one typed exception with a
generic, host-naming message, aggregated across the hosts of a concurrent
lockstep step, with the detail preserved as ``__cause__`` instead of being
dumped into the message.

The behaviour under test is the one a wrong ``sudo_password`` produces over the
real transport. ``sudo -A`` is invoked once, but sudo re-runs the askpass helper
up to its ``passwd_tries`` limit (3 by default), so a single bad password yields
three "Sorry, try again." lines and a final "sudo: 3 incorrect password
attempts" -- noise the user-facing report must not reproduce.
"""

from collections.abc import Generator
from types import SimpleNamespace

import pytest

from reemote import OperationRequest, op
from reemote.core import FactCache, HostRuntime
from reemote.drivers import run, run_apply, send_command
from reemote.hosts import CommandError, Host, PrivilegeError
from reemote.modules import dnf
from reemote.modules.dnf.dnf import DnfInstallCommand

# What sudo prints when the askpass helper keeps supplying a wrong password:
# three attempts (the ``passwd_tries`` default), then it gives up and exits 1.
SUDO_REJECTED = (
    "Sorry, try again.\n"
    "Sorry, try again.\n"
    "Sorry, try again.\n"
    "sudo: 3 incorrect password attempts\n"
)

PASSWORD = "wr0ng-p@ssword"


class RejectingSudoHost(Host):
    """A ``Host`` whose sudo credential the target rejects.

    Stands in for the real transport so the classification and the driver-level
    aggregation can be exercised without opening a socket: ``_run_shell``
    returns sudo's authentication-failure output for every command. The askpass
    helper path is pre-seeded, skipping the setup round-trip. Fact reads go
    through the same seam and return empty stdout, which the facts tolerate --
    so the install below is always a change, and a command is always sent.
    """

    def __init__(self, name: str, stderr: str = SUDO_REJECTED, exit_status: int = 1):
        super().__init__(
            name=name,
            connection={"address": "10.0.0.1"},
            privilege={"sudo_user": "root", "sudo_password": PASSWORD},
        )
        self._askpass_path = "/tmp/reemote-askpass-cached"
        self.stderr = stderr
        self.exit_status = exit_status

    async def _run_shell(self, shell: str):
        return SimpleNamespace(
            exit_status=self.exit_status, stdout="", stderr=self.stderr
        )


def rejecting_factory(entry: dict) -> RejectingSudoHost:
    return RejectingSudoHost(entry["host"])


def deploy(host: Host) -> Generator[OperationRequest]:
    """One elevated operation, enough to trigger the privilege path."""
    yield op(dnf.packages, name="Install httpd", packages=("httpd",), sudo=True)


def _runtime(host: Host) -> HostRuntime:
    """An apply-phase runtime for ``host``, as ``run_prepare`` would build."""
    return HostRuntime(host, deploy(host), FactCache(host, phase="apply"))


# --- run_shell_sudo classifies sudo's own authentication failure --------------


async def test_rejected_sudo_password_raises_privilege_error():
    host = RejectingSudoHost("web1")

    with pytest.raises(PrivilegeError) as excinfo:
        await send_command(host, DnfInstallCommand(packages=("httpd",)), sudo=True)

    err = excinfo.value
    assert err.host_names == ("web1",)
    assert str(err) == "privilege escalation failed on 1 host: web1"


async def test_privilege_error_keeps_the_detail_as_its_cause():
    """The report is generic, but nothing is lost: the ``CommandError`` that
    would have been raised survives as the cause, with exit status, stderr and
    the credential-free command text."""
    host = RejectingSudoHost("web1")

    with pytest.raises(PrivilegeError) as excinfo:
        await send_command(host, DnfInstallCommand(packages=("httpd",)), sudo=True)

    err = excinfo.value
    name, command, cause = err.failures[0]
    assert name == "web1"
    assert command == "sudo dnf install -y httpd"
    assert cause is excinfo.value.__cause__
    assert isinstance(cause, CommandError)
    assert cause.exit_status == 1
    assert "3 incorrect password attempts" in cause.stderr


async def test_privilege_error_does_not_echo_sudos_retry_noise():
    """The three "Sorry, try again." attempts stay out of the user-facing
    message -- that noise is exactly what the typed error replaces."""
    host = RejectingSudoHost("web1")

    with pytest.raises(PrivilegeError) as excinfo:
        await send_command(host, DnfInstallCommand(packages=("httpd",)), sudo=True)

    assert "Sorry, try again" not in str(excinfo.value)
    assert "incorrect password attempts" not in str(excinfo.value)


async def test_privilege_error_is_credential_free():
    """The password reaches neither the message nor the command text."""
    host = RejectingSudoHost("web1")

    with pytest.raises(PrivilegeError) as excinfo:
        await send_command(host, DnfInstallCommand(packages=("httpd",)), sudo=True)

    err = excinfo.value
    assert PASSWORD not in str(err)
    assert PASSWORD not in err.failures[0][1]
    assert PASSWORD not in str(err.__cause__)


@pytest.mark.parametrize(
    "stderr",
    [
        # Todd Miller's sudo: the final line after ``passwd_tries`` attempts...
        "sudo: 3 incorrect password attempts",
        # ...the interim retry line on its own...
        "Sorry, try again.",
        # ...a password sudo demands but the inventory does not supply...
        "sudo: a password is required",
        # ...and the no-TTY / no-helper forms.
        "sudo: a terminal is required to read the password",
        "sudo: no askpass program specified",
        # sudo-rs (Trifecta Tech), the default sudo on Ubuntu 25.10+.
        "sudo-rs: interactive authentication is required",
        "sudo-rs: 3 incorrect authentication attempts",
    ],
)
async def test_sudo_authentication_failures_are_privilege_errors(stderr: str):
    host = RejectingSudoHost("web1", stderr=stderr)

    with pytest.raises(PrivilegeError):
        await send_command(host, DnfInstallCommand(packages=("httpd",)), sudo=True)


async def test_a_command_failure_under_sudo_stays_a_command_error():
    """Once sudo has authenticated, a failure of the command it ran is a
    genuine ``CommandError`` -- not misclassified as a privilege problem."""
    host = RejectingSudoHost(
        "web1", stderr="Error: No match for argument: httpd", exit_status=1
    )

    with pytest.raises(CommandError) as excinfo:
        await send_command(host, DnfInstallCommand(packages=("httpd",)), sudo=True)

    assert not isinstance(excinfo.value, PrivilegeError)


# --- run_shell_su classifies su's own authentication failure ------------------


class RejectingSuHost(Host):
    """A ``Host`` whose su credential the target rejects.

    Mirrors ``RejectingSudoHost`` for the su path: ``_run_shell`` returns su's
    authentication-failure output for every command, and the su askpass helper
    path is pre-seeded so the setup round-trip is skipped.
    """

    def __init__(
        self,
        name: str,
        stderr: str = "su: Authentication failure",
        exit_status: int = 1,
    ):
        super().__init__(
            name=name,
            connection={"address": "10.0.0.1"},
            privilege={"su_user": "deploy", "su_password": PASSWORD},
        )
        self._su_askpass_path = "/tmp/reemote-su-askpass-cached"
        self.stderr = stderr
        self.exit_status = exit_status

    async def _run_shell(self, shell: str):
        return SimpleNamespace(
            exit_status=self.exit_status, stdout="", stderr=self.stderr
        )


async def test_rejected_su_password_raises_privilege_error():
    host = RejectingSuHost("web1")

    with pytest.raises(PrivilegeError) as excinfo:
        await send_command(host, DnfInstallCommand(packages=("httpd",)), su=True)

    err = excinfo.value
    assert err.host_names == ("web1",)
    assert str(err) == "privilege escalation failed on 1 host: web1"


async def test_su_privilege_error_is_credential_free():
    host = RejectingSuHost("web1")

    with pytest.raises(PrivilegeError) as excinfo:
        await send_command(host, DnfInstallCommand(packages=("httpd",)), su=True)

    err = excinfo.value
    name, command, cause = err.failures[0]
    assert name == "web1"
    # The credential-free rendering names the su target, never the password.
    assert command == "su deploy -c 'dnf install -y httpd'"
    assert PASSWORD not in command
    assert PASSWORD not in str(err)
    assert isinstance(cause, CommandError)


@pytest.mark.parametrize(
    "stderr",
    [
        "su: Authentication failure",
        "su: incorrect password",
        "Cannot make/remove an entry for the specified session",
    ],
)
async def test_su_authentication_failures_are_privilege_errors(stderr: str):
    host = RejectingSuHost("web1", stderr=stderr)

    with pytest.raises(PrivilegeError):
        await send_command(host, DnfInstallCommand(packages=("httpd",)), su=True)


async def test_a_command_failure_under_su_stays_a_command_error():
    """Once su has authenticated, a failure of the command it ran is a genuine
    ``CommandError`` -- not misclassified as a privilege problem."""
    host = RejectingSuHost(
        "web1", stderr="Error: No match for argument: httpd", exit_status=1
    )

    with pytest.raises(CommandError) as excinfo:
        await send_command(host, DnfInstallCommand(packages=("httpd",)), su=True)

    assert not isinstance(excinfo.value, PrivilegeError)


# --- run_shell_doas classifies doas's own authorization failure ---------------


class RejectingDoasHost(Host):
    """A ``Host`` whose doas authorization the target denies.

    Mirrors ``RejectingSudoHost`` for the passwordless doas path: ``_run_shell``
    returns doas's authorization-failure output for every command. doas is
    passwordless in reemote (``doas -n``), so there is no password and no
    askpass helper to pre-seed -- only the ``doas_user`` from the inventory
    privilege section.
    """

    def __init__(
        self,
        name: str,
        stderr: str = "doas: Operation not permitted",
        exit_status: int = 1,
    ):
        super().__init__(
            name=name,
            connection={"address": "10.0.0.1"},
            privilege={"doas_user": "root"},
        )
        self.stderr = stderr
        self.exit_status = exit_status

    async def _run_shell(self, shell: str):
        return SimpleNamespace(
            exit_status=self.exit_status, stdout="", stderr=self.stderr
        )


async def test_denied_doas_raises_privilege_error():
    host = RejectingDoasHost("web1")

    with pytest.raises(PrivilegeError) as excinfo:
        await send_command(host, DnfInstallCommand(packages=("httpd",)), doas=True)

    err = excinfo.value
    assert err.host_names == ("web1",)
    assert str(err) == "privilege escalation failed on 1 host: web1"


async def test_doas_privilege_error_is_credential_free():
    host = RejectingDoasHost("web1")

    with pytest.raises(PrivilegeError) as excinfo:
        await send_command(host, DnfInstallCommand(packages=("httpd",)), doas=True)

    err = excinfo.value
    name, command, cause = err.failures[0]
    assert name == "web1"
    # doas is passwordless: the rendering is a bare ``doas -n``, no credential.
    assert command == "doas -n dnf install -y httpd"
    assert isinstance(cause, CommandError)


@pytest.mark.parametrize(
    "stderr",
    [
        # opendoas/OpenBSD doas: a denied rule reports EPERM...
        "doas: Operation not permitted",
        # ...a rule that would require a password (under ``-n``)...
        "doas: Authentication required",
        # ...and a PAM rejection.
        "doas: Authentication failed",
    ],
)
async def test_doas_authentication_failures_are_privilege_errors(stderr: str):
    host = RejectingDoasHost("web1", stderr=stderr)

    with pytest.raises(PrivilegeError):
        await send_command(host, DnfInstallCommand(packages=("httpd",)), doas=True)


async def test_a_command_failure_under_doas_stays_a_command_error():
    """Once doas has authorized, a failure of the command it ran is a genuine
    ``CommandError`` -- not misclassified as a privilege problem."""
    host = RejectingDoasHost(
        "web1", stderr="Error: No match for argument: httpd", exit_status=1
    )

    with pytest.raises(CommandError) as excinfo:
        await send_command(host, DnfInstallCommand(packages=("httpd",)), doas=True)

    assert not isinstance(excinfo.value, PrivilegeError)


async def test_a_command_printing_operation_not_permitted_stays_a_command_error():
    """The ``doas:`` prefix on each marker is what keeps a command's *own*
    "Operation not permitted" text from being misclassified as a doas denial."""
    host = RejectingDoasHost(
        "web1", stderr="ln: /etc/shadow: Operation not permitted", exit_status=1
    )

    with pytest.raises(CommandError) as excinfo:
        await send_command(host, DnfInstallCommand(packages=("httpd",)), doas=True)

    assert not isinstance(excinfo.value, PrivilegeError)


# --- run_apply aggregates per-host failures -----------------------------------


async def test_run_apply_reports_every_host_whose_credentials_failed():
    hosts = [RejectingSudoHost("web1"), RejectingSudoHost("web2")]

    with pytest.raises(PrivilegeError) as excinfo:
        await run_apply([_runtime(host) for host in hosts])

    err = excinfo.value
    assert err.host_names == ("web1", "web2")
    assert "2 hosts" in str(err)
    assert "web1" in str(err) and "web2" in str(err)
    assert isinstance(err.__cause__, CommandError)


async def test_run_apply_reports_a_single_host_with_a_rejected_credential():
    hosts = [RejectingSudoHost("web1")]

    with pytest.raises(PrivilegeError) as excinfo:
        await run_apply([_runtime(host) for host in hosts])

    assert excinfo.value.host_names == ("web1",)
    assert "1 host:" in str(excinfo.value)


async def test_run_apply_propagates_non_privilege_errors():
    hosts = [
        RejectingSudoHost("web1", stderr="Error: No match for argument: httpd")
    ]

    with pytest.raises(CommandError) as excinfo:
        await run_apply([_runtime(host) for host in hosts])

    assert not isinstance(excinfo.value, PrivilegeError)


async def test_run_surfaces_the_privilege_error_and_still_closes_hosts():
    inventory = [
        {"host": "web1", "connection": {"address": "10.0.0.1"}},
        {"host": "web2", "connection": {"address": "10.0.0.2"}},
    ]

    with pytest.raises(PrivilegeError) as excinfo:
        await run(inventory, deploy, host_factory=rejecting_factory)

    assert excinfo.value.host_names == ("web1", "web2")
