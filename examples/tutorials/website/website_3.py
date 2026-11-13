# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: the web app + secrets -- accounts, a custom operation, rendered keys.

Step 3 of the website tutorial (``docs/articles/website.md``). The landing page
is up (``website_2.py``); this adds the Python web app that runs behind nginx on
a Unix socket, and the three ideas it needs:

* **Service accounts.** ``server.group``/``server.user`` create the ``app`` user
  and the ``app-socket`` group nginx and gunicorn share the socket through.
* **A custom operation.** Reemote ships no ``pip`` module, so the virtualenv is
  an *async generator that reads a fact and yields a command* -- ``venv`` and
  ``pip_install`` below -- used exactly like a built-in through ``op()``. Writing
  one is this little code, which is the offset reemote offers for a smaller
  built-in catalogue.
* **Secrets as control-side data.** A deploy is Python, so the ``SECRET_KEY`` is
  read on the control machine (here from a module literal; in production from an
  encrypted store) and rendered into the config text handed to ``files.install``.
  The stage comes from ``host.extra`` -- static inventory data -- so the choice is
  deterministic. An unhashable secret context never reaches the request.

Only the beta-safe pieces are applied here. ``main`` drives ``app_users`` then
``app_config`` against the persistent ``beta`` container (start it once -- see
``website_0.py``), each twice to show idempotence. The other two building blocks
are defined and build their requests offline, but a stock ``ubuntu:24.04``
container cannot run them, so ``main`` does not apply them:

* ``app_venv`` needs ``python3`` (``python3 -m venv``), which the base image lacks;
* ``app_service`` needs ``systemd`` as PID 1 (``systemctl``), which a plain
  container does not run.

``website_4.py`` composes every piece into the full production deploy, where a
real host has python3 and systemd. Commands run as the container's root user, so
nothing here needs ``sudo`` (a stock container has no ``sudo`` binary); a real SSH
target would pass ``sudo=True`` on each request -- the article's prod snippets do.

Run it with::

    uv run python examples/tutorials/website/website_3.py
"""

import asyncio
import sys

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OpContext,
    StringCommand,
    files,
    format_log,
    format_plan,
    op,
    run,
    server,
    systemd,
)
from reemote.modules.files.files import FileExists

# The persistent local target from website_0.py. No ``stage`` key, so app_config
# defaults it to "beta" below; production hosts set ``stage`` per entry.
INVENTORY = [
    {
        "host": "beta",
        "connection": {
            "connector": "docker",
            "identifier": "beta",
            "docker_cmd": "podman",
        },
    },
]

# In production, read secrets from an encrypted store -- a password manager, an
# encrypted file, a hardware key. A plain literal keeps the example
# self-contained; the article reads the same shape from secrets.json control-side.
# Reading an unencrypted file is the simple-and-wrong baseline, exactly as
# hardcoding the key would be.
SECRETS = {
    "beta": {"app_secret_key": "beta-dev-only-change-me"},
    "prod": {"app_secret_key": "prod-key-from-an-encrypted-store"},
}

# The systemd unit, kept as control-side text. ``render_unit`` fills the one
# placeholder with plain ``str.format`` -- there is no files.template and no
# Jinja dependency; rendering happens in ordinary Python before files.install.
UNIT_TEMPLATE = """\
# Managed by reemote -- rendered control-side for {host}.
[Unit]
Description=Example Flask app served by gunicorn
After=network.target

[Service]
User=app
Group=app
WorkingDirectory=/opt/app
ExecStart=/opt/app/bin/gunicorn --bind unix:/run/app/app.sock app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""


def get_secret(name: str, stage: str) -> str:
    """Look up a named secret for a stage, read control-side.

    The value is rendered into a file's text, so it never becomes a request
    argument that must hash -- which is why an unhashable secret source is fine.
    """
    if secret := SECRETS.get(stage, {}).get(name):
        return secret
    raise RuntimeError(f"secret {name!r} for stage {stage!r} not found")


def render_unit(host: Host) -> str:
    """Render the systemd unit control-side -- plain ``str.format``, no Jinja."""
    return UNIT_TEMPLATE.format(host=host.name)


async def venv(ctx: OpContext, path: str, python: str = "python3"):
    """Ensure a virtualenv exists at ``path`` -- a custom operation.

    The shape of every built-in: read a fact, diff it against the desired state,
    set ``ctx.changed``/``ctx.reason``, and yield a command only when something
    must change. Yield nothing and the operation reports "no change".
    """
    if await ctx.facts.get(FileExists, f"{path}/bin/python"):
        ctx.reason = f"venv {path} already exists"
        return
    ctx.changed = True
    ctx.reason = f"venv {path} missing, will create"
    yield StringCommand(f"{python} -m venv {path}")


async def pip_install(ctx: OpContext, venv_path: str, packages: tuple[str, ...]):
    """Install packages into a venv -- kept simple: it always runs.

    A production version would read ``pip freeze`` as a fact and skip work when
    the requirement is already satisfied; this is deliberately minimal.
    """
    ctx.changed = True
    ctx.reason = f"install {len(packages)} package(s) into {venv_path}"
    yield StringCommand(f"{venv_path}/bin/pip install {' '.join(packages)}")


def app_users(host: Host):
    """The service accounts: an app group, a socket group, and the app user.

    nginx and gunicorn share the Unix socket through the ``app-socket`` group, so
    the accounts come first. ``groups`` is a tuple, not a list, for the same
    hashability reason as ``packages``.
    """
    yield op(server.group, name="create app group", group="app")
    yield op(server.group, name="create socket group", group="app-socket")
    yield op(
        server.user,
        name="create app user",
        user="app",
        group="app",
        groups=("app-socket",),
        home="/var/lib/app",
        shell="/usr/sbin/nologin",
        system=True,
    )


def app_venv(host: Host):
    """Create the venv and install the app's dependencies (custom operations).

    Needs ``python3`` on the target, which a stock ``ubuntu:24.04`` container does
    not have, so ``main`` builds these requests offline but does not apply them to
    beta; a real host runs them like any built-in.
    """
    yield op(venv, name="create venv", path="/opt/app")
    yield op(
        pip_install,
        name="install app deps",
        venv_path="/opt/app",
        packages=("flask", "gunicorn"),
    )


def app_config(host: Host):
    """Render the secret config control-side and install it, group-owned 0640.

    The stage comes from ``host.extra`` (static), so the branch is deterministic.
    ``sudo`` is omitted for the root container; a real SSH target passes
    ``sudo=True``. Note the documented trade-off: ``files.install`` decides
    whether to write by reading the destination back *unprivileged*, so a 0640
    file a non-root login user cannot read re-installs on every run. Root reads it
    fine, so idempotence holds here on beta.
    """
    stage = host.extra.get("stage", "beta")
    secret = get_secret("app_secret_key", stage)
    content = f"SECRET_KEY = {secret!r}\n"
    yield op(
        files.install,
        name="write app config",
        dest="/etc/opt/app/config.py",
        content=content,
        group="app",
        mode="0640",
    )


def app_service(host: Host):
    """Write the unit and run the service -- the systemd piece (needs a real init).

    ``systemctl`` refuses in a stock container ("System has not been booted with
    systemd as init system"), so ``main`` does not apply this to beta. On a
    systemd-enabled host a first run starts then enables the unit, and re-runs
    converge to "already running; already enabled".
    """
    yield op(
        files.install,
        name="write app.service",
        dest="/etc/systemd/system/app.service",
        content=render_unit(host),
        sudo=True,
    )
    yield op(
        systemd.service,
        name="run app",
        service="app",
        running=True,
        enabled=True,
        daemon_reload=True,
        sudo=True,
    )


async def main() -> None:
    try:
        # The beta-safe web-app pieces, each driven twice for idempotence.
        # app_config installs a file group-owned by "app", so app_users -- which
        # creates that group -- runs first.
        for label, dep in (("app_users", app_users), ("app_config", app_config)):
            for attempt in (1, 2):
                plans, report, _ = await run(INVENTORY, dep)
                print(f"\n== {label} run {attempt}: plan (prepare) ==")
                print(format_plan(plans))
                if report is not None:
                    print(f"== {label} run {attempt}: log (apply) ==")
                    print(format_log(report))

        # The remaining pieces are defined above and build their requests
        # offline, but a stock container cannot run them -- website_4.py applies
        # them on a real host as part of the full production deploy.
        print("\n== prod-only steps (defined above, not applied to beta) ==")
        print("app_venv:    python3 -m venv + pip install (base image has no python3)")
        print("app_service: files.install app.service + systemd.service (no systemd)")
    except (CommandError, HostConnectionError, InventoryError) as exc:
        # An unreachable host (is beta running?), a failed command, or a
        # malformed inventory -- typed, naming the cause, with no traceback.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
