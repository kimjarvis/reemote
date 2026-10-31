# General examples

Scripts under `examples/general/` — general scenarios built on more than one
module. See the [Examples overview](../examples.md) for the shared "connects
for real" warning and the error-reporting contract every script follows.

## A health-check deploy: dnf install, put and get

The reemote5 take on a relatable pyinfra scenario — a simple web-server
health-check / log-rotation deploy — in one flat generator: `dnf.packages`
installs `htop` (elevated with `sudo=True`), `files.put` pushes an
in-memory motd stream to `/tmp/remote_motd` (unprivileged scp), and
`files.get` pulls `/etc/os-release` back into a per-host `io.BytesIO` sink
whose content the script prints. Where the pyinfra original uses local paths
(`files/motd`, `local_os_release.txt`, `add_deploy_dir=True`), reemote5's
transfers take IO-like objects only — no local file is involved in either
direction.

::: dnf_put_get
    options:
      members: []

## Splitting a fact-dependent deploy in two

A task the determinism rule forbids as a single deploy — "install nginx,
then configure it only if the install actually put it on the box" — made
legal by moving the mutable-fact read into the gap between two complete
prepare/apply cycles. Deploy 1 (`install_nginx`) is branch-free; a
`gather_fact` snapshot of `dnf.DnfPackages` taken after its apply-phase join
captures the real post-install state per host; deploy 2 is built by a
factory closing over that frozen dict, so its branch is a pure function of
static input and both phases yield the identical request sequence. The
second run's `dry=True` plan is an honest preview of the configure step,
computed against the state the install actually left behind. The config is
staged with `files.put` to `/tmp` (transfers are never elevated); the
trade-off — a frozen snapshot, no mid-run replanning — is spelled out in the
script's own docstring below.

::: two_deploy_split
    options:
      members: []
