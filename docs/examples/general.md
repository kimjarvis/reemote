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
