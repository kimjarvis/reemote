# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: HTTPS + production -- a custom fact and operation, then the fleet.

Step 4 of the website tutorial (``docs/articles/website.md``), the capstone: the
same generator functions from ``website_1``-``website_3`` composed into one
top-level deploy, now with HTTPS from Let's Encrypt and pointed at two production
servers over SSH instead of the local ``beta`` container.

The new idea is that reemote's smaller built-in catalogue is offset by how little
code a **custom fact and operation** take. Reemote ships no Certbot module, so:

* ``Certificates`` is a *fact* -- any class with an async ``process`` classmethod
  that takes a host and returns a value. It reads ``certbot certificates`` and
  parses the names certbot already has.
* ``certificate`` is an *operation* -- any async generator taking an ``OpContext``.
  It reads that fact and yields a ``certbot certonly`` command only when the
  domain is missing, so it is idempotent like any built-in.

``nginx_site`` gains one conditional step from this: request the cert when
``https`` -- a value from ``host.extra`` (static inventory data), so the branch is
deterministic. The finished ``deploy`` composes the shared ``nginx_base`` with two
conditional sub-deploys selected by ``host.extra``, so the *identical* generator
deploys the static site to ``web1`` and the app to ``web2``, differing only in the
inventory it is handed. A config change is finished with the **two-deploy split**:
``changed_hosts`` freezes the hosts whose named operations changed, and
``restart_handler`` builds a second deploy that restarts the app only on those.

This script targets **production**, not ``beta``: Certbot needs a *resolvable*
domain for the HTTP-01 challenge, ``systemctl`` needs a real init, and the
inventory is two SSH hosts. It therefore cannot run against the stock container --
like the SSH examples under ``examples/modules/``, it is written to be correct and
to build its requests offline (which the test suite checks), and you point ``PROD``
at your own servers to run it. ``sudo=True`` is the whole elevation story on
Ubuntu: reemote renders ``sudo <command>`` and never prompts -- use passwordless
sudoers, with any run-as user and password coming from the inventory's
``privilege`` block, never from the operation.

There is no ``--yes`` flag, because there is no prompt: preview the whole fleet
with ``dry=True`` (a real prepare pass against live facts, changing nothing), then
run it for real.

Run it with::

    uv run python examples/tutorials/website/website_4.py
"""

import asyncio
import sys

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OpContext,
    PrivilegeError,
    StringCommand,
    apt,
    changed_hosts,
    files,
    format_log,
    format_plan,
    op,
    restart_handler,
    run,
    server,
    sub_deploy,
    systemd,
)
from reemote.modules.files.files import FileExists

# Production is the same inventory shape over the default ssh connector (the
# "connector" key is omitted, since ssh is the default). The keys a Host does not
# recognise -- home_domain/app_domain, stage, nginx_https -- are preserved
# verbatim in host.extra, which is how per-host data reaches the deploy. Point
# these at your own servers; the placeholders will not resolve.
PROD = [
    {
        "host": "web1",
        "connection": {
            "address": "web1.example.com",
            "username": "deploy",
            "key_path": "~/.ssh/id_ed25519",
        },
        "home_domain": "www.example.com",
        "stage": "prod",
        "nginx_https": True,
    },
    {
        "host": "web2",
        "connection": {
            "address": "web2.example.com",
            "username": "deploy",
            "key_path": "~/.ssh/id_ed25519",
        },
        "app_domain": "app.example.com",
        "stage": "prod",
        "nginx_https": True,
    },
]

# In production, read secrets from an encrypted store; a literal keeps the
# example self-contained. The value is rendered into a file's text control-side,
# so it never becomes a request argument that must hash.
SECRETS = {
    "beta": {"app_secret_key": "beta-dev-only-change-me"},
    "prod": {"app_secret_key": "prod-key-from-an-encrypted-store"},
}

# The landing page, installed as data (there is no files.sync).
INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <head><meta charset="utf-8"><title>Deployed by reemote</title></head>
  <body><h1>It works!</h1></body>
</html>
"""

# nginx site configs, rendered control-side by plain str.replace -- there is no
# files.template and no Jinja dependency. The placeholders avoid str.format,
# which would collide with the config's own { } blocks.
HOME_TEMPLATE = """\
server {
    listen __LISTEN__;
    server_name __DOMAIN__;
    root /var/www/__DOMAIN__;
    location / {
        try_files $uri $uri/ =404;
    }
}
"""

APP_TEMPLATE = """\
server {
    listen __LISTEN__;
    server_name __DOMAIN__;
    location / {
        proxy_pass http://unix:/run/app/app.sock;
        proxy_set_header Host $host;
    }
}
"""

# The systemd unit, rendered control-side (one str.format placeholder; a unit has
# no braces of its own to collide with).
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
    """Look up a named secret for a stage, read control-side."""
    if secret := SECRETS.get(stage, {}).get(name):
        return secret
    raise RuntimeError(f"secret {name!r} for stage {stage!r} not found")


def render_unit(host: Host) -> str:
    """Render the systemd unit control-side -- plain ``str.format``, no Jinja."""
    return UNIT_TEMPLATE.format(host=host.name)


def render_site(template: str, domain: str, https: bool) -> str:
    """Render an nginx site control-side -- plain ``str.replace``, no Jinja."""
    listen = "443 ssl" if https else "80"
    return template.replace("__LISTEN__", listen).replace("__DOMAIN__", domain)


def _parse_certbot_names(output: str) -> frozenset[str]:
    """The certificate names in ``certbot certificates`` output.

    Certbot prints a ``Certificate Name: <name>`` line per certificate; collect
    them into a frozen set. Kept minimal -- the real output carries more structure
    than this needs.
    """
    marker = "Certificate Name: "
    names = [
        line.split(marker, 1)[1].strip() for line in output.splitlines() if marker in line
    ]
    return frozenset(names)


class Certificates:
    """The domains certbot already has certificates for -- a custom fact.

    A fact is any class with an async ``process`` classmethod taking a host and
    returning a value; read it inside an operation with ``ctx.facts.get(...)``, or
    across hosts with ``gather_fact``. This one shells out to certbot and parses
    the names, so the ``certificate`` operation below can stay idempotent.
    """

    @classmethod
    async def process(cls, host: Host) -> frozenset[str]:
        output = await host.run_shell("certbot certificates")
        return _parse_certbot_names(output)


async def certificate(ctx: OpContext, domain: str):
    """Request a certificate for ``domain`` if certbot does not have one.

    A custom operation: an async generator taking an ``OpContext``, reading a fact
    to decide whether anything must change, and yielding a command only when it
    does -- the same shape as every built-in. ``certbot certonly`` needs a
    resolvable domain for the HTTP-01 challenge, so this runs in production.
    """
    if domain in await ctx.facts.get(Certificates):
        ctx.reason = f"certificate for {domain} already present"
        return
    ctx.changed = True
    ctx.reason = f"certificate for {domain} missing, will request"
    yield StringCommand(f"certbot certonly -d {domain} -n --nginx --agree-tos")


async def venv(ctx: OpContext, path: str, python: str = "python3"):
    """Ensure a virtualenv exists at ``path`` (custom operation, website_3.py)."""
    if await ctx.facts.get(FileExists, f"{path}/bin/python"):
        ctx.reason = f"venv {path} already exists"
        return
    ctx.changed = True
    ctx.reason = f"venv {path} missing, will create"
    yield StringCommand(f"{python} -m venv {path}")


async def pip_install(ctx: OpContext, venv_path: str, packages: tuple[str, ...]):
    """Install packages into a venv (custom operation, website_3.py)."""
    ctx.changed = True
    ctx.reason = f"install {len(packages)} package(s) into {venv_path}"
    yield StringCommand(f"{venv_path}/bin/pip install {' '.join(packages)}")


def nginx_base(host: Host):
    """The shared base every web host gets (website_1.py), with prod sudo."""
    yield op(apt.update, name="apt update", sudo=True)
    yield op(apt.packages, name="install nginx", packages=("nginx",), sudo=True)
    yield op(
        files.link,
        name="disable default site",
        path="/etc/nginx/sites-enabled/default",
        present=False,
        sudo=True,
    )


def nginx_site(host: Host, template: str, domain: str, https: bool = False):
    """Write and enable one nginx site, requesting a cert first when https.

    ``https`` comes from static inventory data, so the conditional cert step is
    deterministic across both phases. Three ordered requests: the certificate
    (only when https), the site config, then the enable symlink.
    """
    if https:
        yield op(certificate, name="request certificate", domain=domain, sudo=True)
    yield op(
        files.install,
        name="write site",
        dest=f"/etc/nginx/sites-available/{domain}",
        content=render_site(template, domain, https),
        sudo=True,
    )
    yield op(
        files.link,
        name="enable site",
        path=f"/etc/nginx/sites-enabled/{domain}",
        target=f"/etc/nginx/sites-available/{domain}",
        sudo=True,
    )


def home_site(host: Host):
    """The static landing page: an nginx site plus the page itself (website_2)."""
    domain = host.extra.get("home_domain", "www.example.com")
    https = bool(host.extra.get("nginx_https", False))
    yield from nginx_site(host, HOME_TEMPLATE, domain, https)
    yield op(
        files.install,
        name="install landing page",
        dest=f"/var/www/{domain}/index.html",
        content=INDEX_HTML,
        sudo=True,
    )


def app_users(host: Host):
    """The app and socket accounts (website_3.py), with prod sudo."""
    yield op(server.group, name="create app group", group="app", sudo=True)
    yield op(server.group, name="create socket group", group="app-socket", sudo=True)
    yield op(
        server.user,
        name="create app user",
        user="app",
        group="app",
        groups=("app-socket",),
        home="/var/lib/app",
        shell="/usr/sbin/nologin",
        system=True,
        sudo=True,
    )


def app_venv(host: Host):
    """Create the venv and install dependencies (website_3.py), with prod sudo."""
    yield op(venv, name="create venv", path="/opt/app", sudo=True)
    yield op(
        pip_install,
        name="install app deps",
        venv_path="/opt/app",
        packages=("flask", "gunicorn"),
        sudo=True,
    )


def app_config(host: Host):
    """Render the secret config control-side and install it (website_3.py).

    Note the documented trade-off: files.install decides whether to write by
    reading the destination back *unprivileged*, so a 0640 file the login user
    cannot read re-installs on every run -- which also queues the restart handler
    below each time. Grant the login user read membership, or use a
    login-readable mode, to make it converge.
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
        sudo=True,
    )


def app_service(host: Host):
    """Write the unit and run the service (website_3.py); needs a real init."""
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


def app_site(host: Host):
    """The Flask app behind nginx: accounts, venv, config, service, and site."""
    domain = host.extra.get("app_domain", "app.example.com")
    https = bool(host.extra.get("nginx_https", False))
    yield from app_users(host)
    yield from app_venv(host)
    yield from app_config(host)
    yield from app_service(host)
    yield from nginx_site(host, APP_TEMPLATE, domain, https)


def deploy(host: Host):
    """The finished top-level deploy: a shared base plus two conditional sites.

    The ``if``s read ``host.extra`` -- static inventory data -- so both phases
    resolve them identically and the determinism guard holds. ``sub_deploy(...,
    name_prefix=...)`` labels each site's requests so the composed plan stays
    readable. The same generator deploys web1 and web2; only the inventory differs.
    """
    yield from nginx_base(host)
    if host.extra.get("home_domain"):
        yield from sub_deploy(home_site, name_prefix="home")(host)
    if host.extra.get("app_domain"):
        yield from sub_deploy(app_site, name_prefix="app")(host)


async def main() -> None:
    try:
        # Look: preview the whole fleet with dry=True -- a real prepare pass
        # against live facts on both servers, changing nothing (report is None).
        plans, _, _ = await run(PROD, deploy, dry=True)
        print("== dry run: plan (prepare only, nothing applied) ==")
        print(format_plan(plans))

        # Leap: apply for real -- the same generator, the same two phases.
        plans, report, _ = await run(PROD, deploy)
        print("\n== apply: plan ==")
        print(format_plan(plans))
        assert report is not None
        print("== apply: log ==")
        print(format_log(report))

        # Two-deploy split: restart the app only where its config or unit
        # changed. The names carry the "app: " prefix sub_deploy applied, and
        # `changed` is frozen before the handler deploy is built, so its branch
        # consults a snapshot -- never a live fact -- and stays deterministic.
        changed = changed_hosts(
            report, {"app: write app config", "app: write app.service"}
        )
        await run(PROD, restart_handler(changed, "app"))
    except (CommandError, HostConnectionError, InventoryError, PrivilegeError) as exc:
        # An unreachable host, a failed command, a malformed inventory, or a
        # rejected elevation -- typed, naming the cause, with no traceback.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
