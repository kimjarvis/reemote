# The docker connector

The docker connector runs a deploy inside a **local Docker or Podman
container** instead of over SSH, mirroring the baseline's `@docker` connector. It
is selected per inventory entry with `connection.connector`:

```python
inventory = [
    {
        "host": "build1",
        "connection": {
            "connector": "docker",
            "identifier": "alpine:3.20",
            "docker_cmd": "docker",
        },
    },
]
```

<!---

[`Host.from_inventory`][reemote5.hosts.Host.from_inventory] then materializes
a [`DockerHost`][reemote5.connectors.docker.DockerHost] instead of a plain
[`Host`][reemote5.hosts.Host]. `DockerHost` overrides exactly the transport
seams the SSH host uses — `connect`, the shell seam, `upload_file`,
`download_file` and `aclose` — so the drivers, every fact, every operation
and the privilege machinery work unchanged: a deploy script is written once
and runs against either kind of target.

## Controller prerequisites

Every call shells out to the CLI **on the control machine** (an argv list
through `asyncio.create_subprocess_exec` — no local shell, no docker SDK
dependency):

- `docker_cmd` is **required** and names the binary explicitly — there is
  no default and no auto-detection. Set it to `"docker"` (binary on `PATH`,
  reachable daemon — usually the local socket, or whatever `DOCKER_HOST`
  names);
- or set `"docker_cmd": "podman"` and use Podman instead — rootless podman
  needs no daemon at all. One class covers both, mirroring the baseline's
  `PodmanConnector` (which only swaps the binary).

A missing binary or an unreachable daemon surfaces as
[`HostConnectionError`][reemote5.hosts.HostConnectionError] labelled
`docker`, aggregated across the inventory by
[`run_prepare`][reemote5.drivers.run_prepare] exactly like unreachable SSH
hosts. Containers on a *remote* SSH host (the baseline's `@dockerssh`) are not
covered by this connector.

--->

## Connection options

The docker `connection` mapping is a *closed* set — an unknown or SSH-only
key (say `port`) raises [`InventoryError`][reemote5.hosts.InventoryError]
rather than being silently ignored, the same fail-fast policy as the
forbidden SSH options.

| Option | Purpose |
|---|---|
| `connector` | Must be `"docker"` to select this connector |
| `identifier` | Required: an image name or an existing container ID (the baseline's `docker_identifier`) |
| `user` | The `docker exec --user` identity for every command, facts included (the baseline's `docker_user`) |
| `platform` | `docker run --platform`, e.g. `linux/amd64` (the baseline's `docker_platform`) |
| `docker_cmd` | Required: the CLI binary to invoke, `docker` or `podman` — no default |

<!---

the baseline's `docker_architecture` is deliberately dropped: `--arch` is not a
`docker run` flag, and `platform` covers the same need.

## Lifecycle

`identifier` decides which of two workflows runs:

- **Image name** — `docker run -d <image> tail -f /dev/null` starts a
  throwaway container; the deploy executes against it; when
  [`run`][reemote5.drivers.run] closes the host, the container is
  **committed into a new image** and removed. The short image ID is exposed
  as [`DockerHost.image_id`][reemote5.connectors.docker.DockerHost.image_id]
  on the hosts `run` returns. Teardown is best-effort (it must not mask an
  in-flight run error); a failed commit leaves `image_id` as `None` and the
  container is still removed.
- **Existing container ID** — inspected, started if stopped, and **left
  running** on close: never committed, never removed. (the baseline removes a
  stopped container it started itself; reemote5 deliberately does not touch
  containers it did not create.)

!!! note "Dry runs still start (and commit) a container"

    Facts are gathered for real inside the container during prepare, so a
    `dry=True` run of an image target starts its own throwaway container —
    and closing an owned container always commits, leaving an *unchanged*
    image behind.

## Execution, transfers and privilege

Commands run through `docker exec -i <container> sh -c <shell>` (no `-t`:
the transport is non-interactive, exactly like asyncssh's channels).
Container-side exit codes propagate verbatim, so exit 127 still raises
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError] and the apply
path still fails fast with [`CommandError`][reemote5.hosts.CommandError].
Docker daemon-level failures (`exec` exit 125) surface as ordinary non-zero
exits — the transport cannot distinguish them from a container-side failure
without parsing stderr prose.

[`files.put`][reemote5.modules.files.files.put] and
[`files.get`][reemote5.modules.files.files.get] ride `docker cp` through the
same temporary-file dance the scp seams use; a failed copy raises
`CommandError` with the CLI's real exit status. Transfers are unprivileged,
exactly as over SSH.

`sudo`/`su` elevation is inherited unchanged and wraps the shell text
**inside** the container — the escalation binary must exist in the image
(stock alpine has no `sudo`). The `user` connection key is orthogonal: it is
the exec-level identity applied to *every* command, including fact
gathering.

::: reemote5.connectors.docker
    options:
      members:
        - DockerHost
        - validate_docker_connection

-->