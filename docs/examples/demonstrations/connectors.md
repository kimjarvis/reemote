# Connector demonstrations

Scripts under `examples/demonstrations/connectors/` — scenarios that swap
the transport instead of the deploy. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning (and this page's carve-out) and the error-reporting contract every
script follows.

## Building a container image with the docker connector

The same operations as the files examples, run inside a local container
instead of over SSH: the inventory's `connection.connector` selects
[`DockerHost`][reemote5.connectors.docker.DockerHost], the deploy executes
through `docker exec`/`docker cp`, and closing the host commits the
container into a new image whose ID the script prints. Needs a local
`docker` (or `podman`) CLI — no remote host, no SSH. See the
[docker connector reference](../../connectors/docker.md) for the lifecycle
and connection options.

::: docker_files
    options:
      members: []