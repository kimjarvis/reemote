# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Connectors: alternative transports for reaching a target host.

A plain ``Host`` executes over asyncssh (see ``reemote.hosts``); a connector
swaps that transport while keeping every seam the drivers, facts and
operations already speak. ``Host.from_inventory`` dispatches on the
inventory's ``connection.connector`` key (default ``"ssh"``), so a deploy
script and its operations are written once and run against either kind of
target unchanged.

The ``docker`` connector targets local Docker/Podman containers: it shells
out to the ``docker`` (or ``podman``) CLI on the control machine. Further
connectors (e.g. containers on a
remote SSH host) can live beside it.
"""

from .docker import DockerHost

__all__ = ["DockerHost"]