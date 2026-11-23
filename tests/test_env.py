# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the ``env`` request meta-parameter (Ansible task-level ``environment:``).

``op(..., env=...)`` is the operation counterpart of the ``env`` keyword the
``Shell``/``Script`` actions gained: variables set for the command the operation
runs on the target (e.g. an HTTP proxy for an ``apt.packages`` install). It
covers the three joints the value passes through:

* the request model -- a mapping or a tuple of pairs normalizes to one sorted,
  hashable tuple; a non-string name or value, or an entry that is not a pair,
  is refused at the call site; the value participates in equality, hashing and
  ``compose(dedupe=True)``'s effective identity, and never reaches ``kwargs``;
* the rendering -- ``command_shell`` emits ``actions._env_prefix``'s output
  *innermost*, inside the ``doas``/``sudo``/``su`` wrappers, so the variables
  survive the escalation, and the two non-shell descriptors (``TransferCommand``,
  ``WaitCommand``) ignore it by contract;
* the wire and the log -- ``send_command`` threads the value into the plain
  descriptor shell it hands the transport (so it cannot appear only in the
  loggable rendering), and ``format_log`` renders the same prefix, values
  included: an ``env`` entry is command text, unlike a password.

The end-to-end case drives ``run`` against a real-transport fake so the executed
shell text is observed, and against ``SimulatedHost`` to show the value stays
inert on the offline path (privilege and environment have no in-memory effect).
"""

import shlex
from types import SimpleNamespace

import pytest

from reemote import (
    Host,
    OperationRequest,
    StringCommand,
    apt,
    command_shell,
    compose,
    format_log,
    format_plan,
    op,
    run,
    send_command,
    sub_deploy,
)
from reemote.commands import TransferCommand, WaitCommand
from reemote.drivers import ApplyReport, LogEntry
from tests.simulated_host import SimulatedHost

PROXY = (
    ("http_proxy", "http://proxy.example:3128"),
    ("https_proxy", "http://proxy.example:3128"),
)

INVENTORY = [
    {
        "host": "web1",
        "family": "Ubuntu",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]


async def install_httpd(ctx, packages, present=True):
    """A fact-free stand-in operation: always changes, one plain shell command.

    Defined here rather than borrowed from a namespace so the test observes the
    driver's rendering alone, with no fact read to seed. Same shape as every
    real operation: an async generator taking ``ctx`` and yielding descriptors.
    """
    ctx.changed = True
    ctx.reason = f"install {', '.join(packages)}"
    yield StringCommand(f"apt-get install -y {' '.join(packages)}")


# --- request model ------------------------------------------------------------


def test_env_mapping_normalizes_to_a_sorted_tuple_of_pairs():
    request = op(install_httpd, packages=("nginx",), env=dict(PROXY))
    # A dict is unhashable, so the stored value is the reduced pair form;
    # sorting makes it independent of the order the mapping was built in.
    assert request.env == PROXY


def test_env_tuple_of_pairs_is_stored_unchanged():
    assert op(install_httpd, packages=("nginx",), env=PROXY).env == PROXY


def test_env_absent_empty_and_none_all_normalize_to_empty():
    assert op(install_httpd, packages=("nginx",)).env == ()
    assert op(install_httpd, packages=("nginx",), env=None).env == ()
    assert op(install_httpd, packages=("nginx",), env={}).env == ()


def test_env_is_not_forwarded_to_the_operation():
    request = op(install_httpd, packages=("nginx",), env={"NO_PROXY": "localhost"})
    assert request.kwargs == {"packages": ("nginx",)}


def test_env_order_does_not_affect_equality_or_hashing():
    forward = op(install_httpd, packages=("nginx",), env=PROXY)
    reversed_ = op(
        install_httpd,
        packages=("nginx",),
        env=(("https_proxy", "http://proxy.example:3128"),) + PROXY[:1],
    )
    assert forward == reversed_
    assert hash(forward) == hash(reversed_)


def test_a_different_env_makes_a_different_request():
    proxied = op(install_httpd, packages=("nginx",), env=PROXY)
    plain = op(install_httpd, packages=("nginx",))
    other = op(install_httpd, packages=("nginx",), env={"HTTP_PROXY": "http://p:3128"})
    assert proxied != plain
    assert proxied != other


def test_an_unhashable_or_non_string_env_is_rejected_at_the_call_site():
    with pytest.raises(TypeError, match="not a string pair"):
        op(install_httpd, packages=("nginx",), env={"PORT": 8080})
    with pytest.raises(TypeError, match="not a string pair"):
        op(install_httpd, packages=("nginx",), env={"PKGS": ["nginx"]})
    with pytest.raises(TypeError, match="not a string pair"):
        op(install_httpd, packages=("nginx",), env={1: "one"})
    # An entry that would silently unpack into two characters is refused too.
    with pytest.raises(TypeError, match="got the string"):
        op(install_httpd, packages=("nginx",), env=["KV"])
    with pytest.raises(TypeError, match="sequence of \\(key, value\\) pairs"):
        op(install_httpd, packages=("nginx",), env=[("A", "b", "extra")])


def test_a_dict_env_keeps_the_request_hashable_for_dedupe():
    # ``compose(dedupe=True)`` hashes every part of a request; the normalized
    # pair tuple is what lets a plain dict be passed at the call site.
    request = OperationRequest(
        func=install_httpd, kwargs={"packages": ("nginx",)}, env={"FOO": "bar"}
    )
    assert hash(request) == hash(
        OperationRequest(
            func=install_httpd, kwargs={"packages": ("nginx",)}, env=(("FOO", "bar"),)
        )
    )


def _deploy_pair(host: Host):
    yield op(install_httpd, name="a", packages=("nginx",), env=dict(PROXY))
    yield op(install_httpd, name="b", packages=("nginx",), env=dict(PROXY))
    yield op(install_httpd, name="c", packages=("nginx",))


def _deploy_renamed_proxy(host: Host):
    yield op(install_httpd, name="a", packages=("nginx",), env=PROXY)
    # Same operation, different environment: not a duplicate.
    yield op(install_httpd, name="b", packages=("nginx",), env={"NO_PROXY": "db"})


def test_compose_dedupe_collapses_on_env_and_keeps_a_different_one():
    deduped = list(compose(_deploy_pair, dedupe=True)(Host(name="web1")))
    # The name differs but the effective identity does not, so "b" collapses
    # into "a" -- with its env intact.
    assert [request.name for request in deduped] == ["a", "c"]
    assert deduped[0].env == PROXY

    kept = list(compose(_deploy_renamed_proxy, dedupe=True)(Host(name="web1")))
    # A different environment changes what executes, so the two stay apart.
    assert [request.name for request in kept] == ["a", "b"]
    assert [request.env for request in kept] == [PROXY, (("NO_PROXY", "db"),)]


def test_sub_deploy_leaves_env_untouched():
    # Unlike sudo/su/doas there is no wrapper policy for the environment: an
    # inner request's own value passes through and an unset one stays unset.
    wrapper = sub_deploy(_deploy_pair, name_prefix="role", sudo=True)
    requests = list(wrapper(Host(name="web1")))
    assert requests[0].env == PROXY
    assert requests[0].sudo.elevates()
    assert requests[2].env == ()


# --- rendering ----------------------------------------------------------------


def test_command_shell_prefixes_the_plain_descriptor_shell():
    cmd = StringCommand("apt-get install -y nginx")
    assert command_shell(cmd, env=PROXY) == f"env http_proxy=http://proxy.example:3128 https_proxy=http://proxy.example:3128 {cmd.shell()}"
    # A mapping renders identically -- one helper, two spellings.
    assert command_shell(cmd, env=dict(PROXY)) == command_shell(cmd, env=PROXY)


def test_command_shell_quotes_names_and_values():
    shell = command_shell(StringCommand("echo"), env={"MY VAR": "it's a test"})
    # Each name and value is quoted individually, so a space or an apostrophe
    # in either stays one word on the wire.
    assert shell == f"env {shlex.quote('MY VAR')}={shlex.quote("it's a test")} echo"


def test_the_env_prefix_sits_inside_every_privilege_wrapper():
    cmd = StringCommand("apt-get update")
    env = {"http_proxy": "http://p:3128"}
    assert command_shell(cmd, sudo=True, env=env) == "sudo env http_proxy=http://p:3128 apt-get update"
    assert command_shell(cmd, sudo=True, sudo_user="ops", env=env) == (
        "sudo -u ops env http_proxy=http://p:3128 apt-get update"
    )
    assert command_shell(cmd, doas=True, env=env) == (
        "doas -n env http_proxy=http://p:3128 apt-get update"
    )
    # Under ``su`` the prefix rides inside the quoted payload, so the variables
    # belong to the command and not to the shell su starts.
    assert command_shell(cmd, su=True, su_user="deploy", env=env) == (
        f"su deploy -c {shlex.quote('env http_proxy=http://p:3128 apt-get update')}"
    )
    # All three at once keeps the documented order: doas, sudo, su, env, shell.
    assert command_shell(cmd, doas=True, sudo=True, su=True, env=env) == (
        "doas -n sudo su -c "
        + shlex.quote("env http_proxy=http://p:3128 apt-get update")
    )


def test_empty_or_none_env_leaves_the_rendering_unchanged():
    cmd = StringCommand("apt-get update")
    assert command_shell(cmd, sudo=True) == "sudo apt-get update"
    assert command_shell(cmd, sudo=True, env=None) == "sudo apt-get update"
    assert command_shell(cmd, sudo=True, env={}) == "sudo apt-get update"
    assert command_shell(cmd, sudo=True, env=()) == "sudo apt-get update"


def test_a_transfer_descriptor_ignores_env():
    class Spool(TransferCommand):
        def shell(self) -> str:
            return "scp <content> -> /tmp/spool"

    # A transfer rides the scp channel: no shell, so no environment to set.
    assert command_shell(Spool(), env=PROXY) == "scp <content> -> /tmp/spool"
    assert command_shell(Spool(), sudo=True, env=PROXY) == "scp <content> -> /tmp/spool"


def test_a_wait_descriptor_ignores_env():
    class Poll(WaitCommand):
        def shell(self) -> str:
            return "wait for Port(443) <is_listening>"

    # A wait polls a fact through its own coroutine, so nothing runs whose
    # environment could be set; the loggable line stays the poll description.
    assert command_shell(Poll(), env=PROXY) == "wait for Port(443) <is_listening>"


# --- wire and log -------------------------------------------------------------


class RecordingHost(Host):
    """A real-transport fake overriding the single ``_run_shell`` seam."""

    async def _run_shell(self, shell: str):
        self.shells.append(shell)
        return SimpleNamespace(exit_status=0, stdout="", stderr="")


def recording_factory(entry: dict) -> RecordingHost:
    host = RecordingHost.from_inventory(entry)
    host.shells = []
    return host


async def test_send_command_puts_the_prefix_on_the_wire():
    host = RecordingHost(name="web1")
    host.shells = []
    await send_command(host, StringCommand("apt-get update"), env=PROXY)
    assert host.shells == [
        "env http_proxy=http://proxy.example:3128 https_proxy=http://proxy.example:3128 apt-get update"
    ]


async def test_send_command_keeps_the_prefix_inside_the_escalation():
    host = RecordingHost.from_inventory(
        {**INVENTORY[0], "privilege": {"sudo_user": "root"}}
    )
    host.shells = []
    await send_command(host, StringCommand("apt-get update"), sudo=True, env=PROXY)
    assert host.shells == [
        "sudo env http_proxy=http://proxy.example:3128 https_proxy=http://proxy.example:3128 apt-get update"
    ]


async def test_a_deploy_threads_the_request_env_to_the_transport():
    def deploy(host: Host):
        yield op(install_httpd, name="Install nginx", packages=("nginx",), env=PROXY)

    plans, report, hosts = await run(INVENTORY, deploy, host_factory=recording_factory)
    host = hosts[0]
    # The prefix is part of the executed text, not just of the log rendering.
    assert host.shells == [
        "env http_proxy=http://proxy.example:3128 https_proxy=http://proxy.example:3128 apt-get install -y nginx"
    ]
    # The plan line names no commands at all, so it carries no env; the run log
    # shows the executed rendering, values included (they are command text).
    assert format_plan(plans) == "web1: Install nginx            [changed: install nginx]"
    assert format_log(report) == (
        "web1: Install nginx            [changed: install nginx]\n"
        "    $ env http_proxy=http://proxy.example:3128 "
        "https_proxy=http://proxy.example:3128 apt-get install -y nginx"
    )
    assert report.log[0].env == PROXY


async def test_the_log_renders_what_the_transport_ran_under_sudo():
    inventory = [{**INVENTORY[0], "privilege": {"sudo_user": "root"}}]

    def deploy(host: Host):
        yield op(
            install_httpd,
            name="Install nginx",
            packages=("nginx",),
            env=PROXY,
            sudo=True,
        )

    _plans, report, hosts = await run(inventory, deploy, host_factory=recording_factory)
    wire = hosts[0].shells[0]
    logged = format_log(report).splitlines()[1].removeprefix("    $ ")
    assert wire == logged


def test_format_log_renders_the_entry_env():
    report = ApplyReport(
        log=[
            LogEntry(
                host="web1",
                name="Install nginx",
                changed=True,
                reason="install nginx",
                commands=(StringCommand("apt-get install -y nginx"),),
                sudo=True,
                sudo_user="ops",
                env=PROXY,
            )
        ]
    )
    assert format_log(report) == (
        "web1: Install nginx            [changed: install nginx]\n"
        "    $ sudo -u ops env http_proxy=http://proxy.example:3128 "
        "https_proxy=http://proxy.example:3128 apt-get install -y nginx"
    )


async def test_env_is_inert_on_the_simulated_path():
    """``SimulatedHost`` runs no shell, so an ``env``-carrying deploy behaves
    exactly as the same deploy without one -- changed once, idempotent after.
    """

    def deploy(host: Host):
        yield op(
            apt.packages,
            name="Install zabbix-agent",
            packages=("zabbix-agent",),
            present=True,
            env=PROXY,
            sudo=True,
        )

    # The same host across both runs, so the second pass sees the install the
    # first one made -- the offline counterpart of a proxied re-run.
    host = SimulatedHost.from_inventory(
        {**INVENTORY[0], "privilege": {"sudo_user": "root"}}
    )
    _plans, report, _hosts = await run(INVENTORY, deploy, host_factory=lambda _: host)
    assert [entry.changed for entry in report.log] == [True]
    # The entry still carries the request's env, and the log renders the
    # prefix the real transport would have run.
    assert report.log[0].env == PROXY
    assert "env http_proxy=" in format_log(report)
    assert "zabbix-agent" in host.apt_packages

    # Re-running the identical deploy accounts for the env in both phases, so
    # the determinism guard holds and the second pass reports no change.
    _plans, second, _hosts = await run(INVENTORY, deploy, host_factory=lambda _: host)
    assert [entry.changed for entry in second.log] == [False]
