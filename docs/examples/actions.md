# Action examples

Scripts under `examples/actions/` — runnable demonstrations of
[`action.Shell`][reemote5.actions.Shell] and
[`gather_action`][reemote5.actions.gather_action], the escape hatch for bespoke
shell commands. See the [Examples overview](../examples.md) for the shared
"connects for real" warning and the error-reporting contract, and the
[Actions reference](../actions.md) for the feature itself.

## Running a bespoke command

`action_shell` fans a command out across two hosts with
`gather_action` and inspects the [`Result`][reemote5.actions.Result] objects —
`exit_status`, `stdout`, `stderr`, `.ok`. It runs a command that succeeds
(`uname -r`) and one that fails (`cat` of an absent file, exit 1) to show that a
non-zero exit is **data in the Result, not an exception**: the caller decides
what a bespoke command's failure means.

::: action_shell
    options:
      members: []

## Elevating with a sudo password

`action_shell_sudo` is the action analogue of the
[privilege demonstration](demonstrations/privilege.md): the same inventory
`privilege` section supplies the run-as user and password, and the call only
asks with `sudo=True`. It runs `id` unprivileged (the login user) and elevated
(root), showing that the elevated path rides the raw-result privileged seam —
the command's own exit is data, but a *rejected credential* raises
[`PrivilegeError`][reemote5.hosts.PrivilegeError].

::: action_shell_sudo
    options:
      members: []

## The in-deploy guard

`action_in_deploy_guard` demonstrates the rule that an
action cannot run inside a deploy: a generator that calls `gather_action` raises
[`ActionInDeployError`][reemote5.actions.ActionInDeployError] at the call site.
This script needs **no real host** — the guard fires during prepare, before any
fact is gathered or connection opened — so it runs offline and treats the error
as the expected outcome.

::: action_in_deploy_guard
    options:
      members: []

## Deploy, action, deploy

`deploy_action_deploy` is the capstone, mirroring
[`two_deploy_split`](general.md#splitting-a-fact-dependent-deploy-in-two): install
nginx (deploy 1), probe it with the bespoke action `nginx -v` (between deploys),
then configure it only where the probe succeeded (deploy 2). The second deploy
is built by a factory closing over the frozen `Result` dict, so its branch is a
pure function of static input and the determinism guard holds — the action
analogue of the two-deploy fact split.

::: deploy_action_deploy
    options:
      members: []