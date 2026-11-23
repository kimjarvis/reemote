# Task environments

Scripts under `examples/demonstrations/environment/` — scenarios that cross
module boundaries to show the [`op`][reemote.core.op] meta-parameter
`env`, the operation counterpart of the `env` keyword the
[actions](../../actions/actions.md) take and the equivalent of Ansible's
task-level `environment:`. Where the
[privilege demonstrations](privilege.md) control *who* runs a command, these
control *what the command sees*: variables set in the environment of the
process the operation starts on the target.

The value is normalized to a sorted tuple of `(key, value)` pairs on the
request — so a plain dict stays hashable and `compose(dedupe=True)` can key on
it — and rendered by the same helper the actions use, so the quoting cannot
drift between the two paths. It is placed *innermost*, inside the
`doas`/`sudo`/`su` wrappers, because `sudo` resets the caller's environment: a
prefix applied outside the escalation would be dropped before the command ran.
Only the two non-shell descriptor kinds ignore it — a
[`TransferCommand`][reemote.commands.TransferCommand] rides the scp channel and
a [`WaitCommand`][reemote.commands.WaitCommand] polls a fact, so neither starts
a process — and the values appear in the log, since an environment entry is
command text rather than a credential. See
[Core: `op`](../../core.md) for the meta-parameter and
[Drivers](../../drivers.md) for the rendering.

## Installing a package through a proxy

A Zabbix agent that has to come from a third-party repository on a host that
reaches the internet only through a proxy: `apt.repo` adds the sources entry
and refreshes the index, `apt.packages` installs the agent, and each request
carries the proxy variables. The scenario shows all three shapes of the rule at
once — the request's spool upload takes no prefix (it is a transfer), its
install and refresh do (they are shells), and the following unguarded request
installs `curl` with no environment at all.

The script runs **entirely in memory**: it injects the test framework's
`SimulatedHost` through `run`'s `host_factory`, so it needs no reachable host,
and it asserts the rendered command text — which is the same `command_shell`
output the real transport sends — rather than any package state. Its `is_ubuntu`
(`host.family == "Ubuntu"`) guard keeps the `apt` work on the single Ubuntu host
of the shared `inventory.json`, which is still read (and validated up front) for
the host list. See the [Examples overview](../../examples.md) for the shared
error-reporting contract every script follows.

::: environment_apt_proxy
    options:
      members: []
