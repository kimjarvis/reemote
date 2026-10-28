"""Tests for SSH connectivity error handling (``HostConnectionError``).

Two layers keep an asyncssh transport failure from leaking a raw traceback:
``Host.connect`` wraps the transport error into a generic
``HostConnectionError`` naming the host, and ``drivers.run_prepare`` merges the
per-host failures from a concurrent inventory into one aggregate naming every
unreachable host, while any other exception still propagates unchanged.
"""

import sys
import types
from collections.abc import Generator

import pytest

from reemote5 import OperationRequest, op
from reemote5.drivers import run, run_prepare
from reemote5.hosts import Host, HostConnectionError
from reemote5.modules import dnf


class UnreachableHost(Host):
    """A ``Host`` whose transport always fails with a wrapped connect error.

    Stands in for a real host that cannot be reached: ``run_shell`` raises the
    same ``HostConnectionError`` ``Host.connect`` would, so the driver-level
    aggregation can be exercised without opening a socket.
    """

    async def run_shell(self, shell: str) -> str:
        address = self.connection.get("address", "?")
        cause = ConnectionResetError(104, "Connection reset by peer")
        raise HostConnectionError([(self.name, address, cause)]) from cause


class BoomHost(Host):
    """A ``Host`` whose transport fails with an *unexpected* error."""

    async def run_shell(self, shell: str) -> str:
        raise RuntimeError("boom")


def deploy(host: Host) -> Generator[OperationRequest]:
    """A single fact-reading operation, enough to trigger the transport."""
    yield op(dnf.packages, name="Install httpd", packages=("httpd",))


def unreachable_factory(entry: dict) -> UnreachableHost:
    return UnreachableHost(name=entry["host"], connection=dict(entry["connection"]))


def _install_fake_asyncssh(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Install a stub ``asyncssh`` module and return it.

    The stub exposes an ``Error`` base (standing in for ``asyncssh.Error``) and
    a ``connect`` coroutine the test replaces to raise whatever it needs.
    """
    module = types.ModuleType("asyncssh")

    class _FakeError(Exception):
        """Stand-in for ``asyncssh.Error``."""

    module.Error = _FakeError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "asyncssh", module)
    return module


# --- Host.connect wraps the transport error -----------------------------------


async def test_connect_wraps_os_error(monkeypatch: pytest.MonkeyPatch):
    module = _install_fake_asyncssh(monkeypatch)

    async def _connect(address, **kwargs):
        raise ConnectionResetError(104, "Connection reset by peer")

    module.connect = _connect  # type: ignore[attr-defined]
    host = Host(name="web1", connection={"address": "10.0.0.1"})

    with pytest.raises(HostConnectionError) as excinfo:
        await host.connect()

    assert excinfo.value.host_names == ("web1",)
    assert isinstance(excinfo.value.__cause__, ConnectionResetError)


async def test_connect_wraps_asyncssh_error(monkeypatch: pytest.MonkeyPatch):
    # An asyncssh.Error (e.g. a host-key or authentication rejection) is not an
    # OSError, so this proves the second arm of the ``except`` clause catches it.
    module = _install_fake_asyncssh(monkeypatch)

    async def _connect(address, **kwargs):
        raise module.Error("host key not verifiable")  # type: ignore[attr-defined]

    module.connect = _connect  # type: ignore[attr-defined]
    host = Host(name="web1", connection={"address": "10.0.0.1"})

    with pytest.raises(HostConnectionError) as excinfo:
        await host.connect()

    assert excinfo.value.host_names == ("web1",)
    assert isinstance(excinfo.value.__cause__, module.Error)  # type: ignore[attr-defined]


# --- run_prepare aggregates per-host failures ---------------------------------


async def test_run_prepare_reports_every_unreachable_host():
    hosts = [
        UnreachableHost(name="web1", connection={"address": "10.0.0.1"}),
        UnreachableHost(name="web2", connection={"address": "10.0.0.2"}),
    ]

    with pytest.raises(HostConnectionError) as excinfo:
        await run_prepare(hosts, deploy)

    err = excinfo.value
    assert err.host_names == ("web1", "web2")
    assert "2 hosts" in str(err)
    assert "web1" in str(err) and "web2" in str(err)
    assert isinstance(err.__cause__, ConnectionResetError)


async def test_run_prepare_reports_a_single_unreachable_host():
    hosts = [UnreachableHost(name="web1", connection={"address": "10.0.0.1"})]

    with pytest.raises(HostConnectionError) as excinfo:
        await run_prepare(hosts, deploy)

    assert excinfo.value.host_names == ("web1",)
    assert "1 host:" in str(excinfo.value)


async def test_run_prepare_propagates_non_connection_errors():
    hosts = [BoomHost(name="web1", connection={"address": "10.0.0.1"})]

    with pytest.raises(RuntimeError) as excinfo:
        await run_prepare(hosts, deploy)

    assert not isinstance(excinfo.value, HostConnectionError)
    assert "boom" in str(excinfo.value)


async def test_run_surfaces_the_aggregate_and_still_closes_hosts():
    inventory = [
        {"host": "web1", "connection": {"address": "10.0.0.1"}},
        {"host": "web2", "connection": {"address": "10.0.0.2"}},
    ]

    with pytest.raises(HostConnectionError) as excinfo:
        await run(inventory, deploy, host_factory=unreachable_factory)

    assert excinfo.value.host_names == ("web1", "web2")
