# Action examples

Scripts under `examples/actions/` — runnable demonstrations of
[`action.Shell`][reemote5.actions.Shell],
[`Script`][reemote5.actions.Script] (also reachable as `server.script`),
[`WaitForConnection`][reemote5.actions.WaitForConnection] and
`gather_action`, the escape hatch for acting
on servers outside the deploy model. Each reads the shared `inventory.json`
with `load_inventory` and guards the `centos` group with `is_centos`
(`host.group == "centos"`) — composed via `sub_deploy(..., when=...)` on
deploys, or as a host-list filter before the fan-out, since an action has no
`when` meta-parameter. See the [Examples overview](../examples.md)
for the shared "connects for real" warning and the error-reporting contract,
and the [Actions reference](../actions/actions.md) for the feature itself.

## Running a bespoke command

`action_shell` fans a command out across the `centos` hosts with
`gather_action` and inspects the [`Result`][reemote5.actions.Result] dicts —
`exit_status`, `stdout`, `stderr`. The `is_centos` guard filters the host list
before the fan-out, so the `alpine` and `ubuntu` entries are reported as
skipped and never contacted. It runs a command that succeeds
(`uname -r`) and one that fails (`cat` of an absent file, exit 1) to show that a
non-zero exit is **data in the Result, not an exception**: the caller decides
what a bespoke command's failure means.

::: action_shell
    options:
      members: []

## Elevating with a sudo password

`action_shell_sudo` is the action analogue of the
[privilege demonstration](demonstrations/privilege.md): the same `privilege`
section — attached to the loaded `centos` entries, since the shared inventory
carries none — supplies the run-as user and password, and the call only
asks with `sudo=True`. The `is_centos` guard filters the host list before the
fan-out. It runs `id` unprivileged (the login user) and elevated
(root), showing that the elevated path rides the raw-result privileged seam —
the command's own exit is data, but a *rejected credential* raises
[`PrivilegeError`][reemote5.hosts.PrivilegeError].

::: action_shell_sudo
    options:
      members: []

## Running a staged script

`action_script` demonstrates `server.script` — the namespace alias of the
[`Script`][reemote5.actions.Script] action — end to end: a deploy stages the
script with `files.put` (the action never transfers anything itself), then
`gather_action` runs it. The staged file has no executable bit, so the first
run falls back to `sh <path> <args>`; a `Shell` action adds `chmod +x` and the
same call then executes the file directly via its shebang. A final call
against an absent path shows that a missing file is an **error Result**
(exit 127 with a `server.script: no such file` message on stderr), not an
exception. The `is_centos` guard filters the host list before each fan-out.

::: action_script
    options:
      members: []

## Waiting for connections after a reboot

`action_wait_for_connection` demonstrates
[`WaitForConnection`][reemote5.actions.WaitForConnection]: the wait that
re-establishes SSH connections after a reboot, so the next deploy starts only
once every selected host is reachable. The `is_centos` guard filters the host
list before the fan-out, and the wait then selects with the fan-out's own
`group="centos"`/`tags` keywords, shows the `timeout`/`retry` parameters, and
demonstrates the fatal
`ConnectionTimeoutError` against a
deliberately unreachable TEST-NET address. The full **deploy → reboot → wait →
deploy** recipe is in the script's docstring; the script itself never reboots
the shared demo hosts.

::: action_wait_for_connection
    options:
      members: []

## The in-deploy guard

`action_in_deploy_guard` demonstrates the rule that an
action cannot run inside a deploy: a generator that calls `gather_action` raises
`ActionInDeployError` at the call site. It reads the shared `inventory.json`
and composes the `is_centos` guard like every other example, but the in-deploy
guard fires while the generator is drained, before any `when` skip is
considered.
This script needs **no real host** — the guard fires during prepare, before any
fact is gathered or connection opened — so it runs offline and treats the error
as the expected outcome.

::: action_in_deploy_guard
    options:
      members: []

## Deploy, action, deploy

`deploy_action_deploy` is the capstone, mirroring
[`two_deploy_split`](demonstrations/composition.md#splitting-a-fact-dependent-deploy-in-two): install
nginx (deploy 1), probe it with the bespoke action `nginx -v` (between deploys),
then configure it only where the probe succeeded (deploy 2). The install
deploy carries the `is_centos` guard composed via `sub_deploy`, the probe
filters the host list by the same predicate before the fan-out, and the staged
put carries `when=is_centos`. The second deploy
is built by a factory closing over the frozen `Result` dict, so its branch is a
pure function of static input and the determinism guard holds — the action
analogue of the two-deploy fact split.

::: deploy_action_deploy
    options:
      members: []