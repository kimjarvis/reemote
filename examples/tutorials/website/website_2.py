# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: a static site, composed -- reuse nginx_base, add the landing page.

Step 2 of the website tutorial (``docs/articles/website.md``). With nginx
installed (``website_1.py``), this publishes a static landing page and shows the
two composition moves every larger deploy is built from:

* **``yield from``** includes another deploy's requests inline -- ``deploy``
  starts with ``yield from nginx_base(host)``, so the base is reused verbatim.
* **``sub_deploy(..., name_prefix=...)``** includes a sub-deploy and labels every
  request it yields (``home: install landing page``), so the plan stays readable
  as the deploy grows.

The site is conditional on ``host.extra`` -- *static* inventory data, identical in
both phases -- so ``if host.extra.get("home_domain")`` is deterministic. Branching
on a live fact instead would raise ``DeterminismError``.

Two honest adaptations from a YAML-and-Jinja workflow, both about how content
reaches the target:

* **There is no ``files.sync`` and no controller-filesystem awareness.** Reemote
  never resolves a local directory on the target's behalf; content crosses as
  *data*. The article reads a folder of static files control-side
  (``Path("site").rglob("*")`` then ``path.read_text()``) and installs each one;
  this example keeps a single landing page as a module-level string so it is
  self-contained (no ``site/`` directory to ship) while showing the same move.
* **There is no ``files.template``.** Jinja rendering happens control-side, in
  plain Python, and the resulting string goes to ``files.install`` -- which keeps
  an unhashable template context out of the request and needs no template engine.

``files.install`` writes the text and compares content, so a re-run is a no-op.
The applied log shows the **spool-and-install** pattern for root-owned files: an
unprivileged ``scp`` of the text to a ``/tmp`` spool named after the destination's
hash, then one elevated ``sh -c`` that copies it into place, sets the mode, and
removes the spool -- nothing ever runs ``sed -i`` on a protected file.

The target is the persistent ``beta`` container (start it once -- see
``website_0.py``); ``beta`` carries ``home_domain`` in its inventory so the
conditional site deploys. Commands run as root, so no ``sudo`` (a real SSH target
would pass ``sudo=True``).

Run it with::

    uv run python examples/tutorials/website/website_2.py
"""

import asyncio
import sys

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    apt,
    files,
    format_log,
    format_plan,
    op,
    run,
    sub_deploy,
)

# beta carries ``home_domain`` in its inventory, so the conditional static site
# deploys; a key Host does not recognise is preserved verbatim in host.extra.
INVENTORY = [
    {
        "host": "beta",
        "connection": {
            "connector": "docker",
            "identifier": "beta",
            "docker_cmd": "podman",
        },
        "home_domain": "beta.example.com",
    },
]

# The landing page, kept control-side as data. The article reads a folder of
# static files with Path("site").rglob("*") and hands each file's text to
# files.install; one literal keeps this example self-contained while showing the
# same "content crosses as data, never a synced path" move.
INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Deployed by reemote</title>
  </head>
  <body>
    <h1>It works!</h1>
    <p>This landing page was installed by reemote.</p>
  </body>
</html>
"""


def nginx_base(host: Host):
    """The shared base, reused verbatim from website_1.py."""
    yield op(apt.update, name="apt update")
    yield op(apt.packages, name="install nginx", packages=("nginx",))
    yield op(
        files.link,
        name="disable default site",
        path="/etc/nginx/sites-enabled/default",
        present=False,
    )


def home_site(host: Host):
    """Publish the static landing page: content crosses as data, not a path."""
    yield op(
        files.install,
        name="install landing page",
        dest="/var/www/html/index.html",
        content=INDEX_HTML,
    )


def deploy(host: Host):
    """Compose the base with the conditional static site.

    ``yield from nginx_base`` reuses the base inline; ``sub_deploy(home_site,
    name_prefix="home")`` includes home_site and labels its requests
    ``home: ...``. The ``if`` reads ``host.extra`` (static inventory data), so
    both phases resolve it identically and the determinism guard holds.
    """
    yield from nginx_base(host)
    if host.extra.get("home_domain"):
        yield from sub_deploy(home_site, name_prefix="home")(host)


async def main() -> None:
    try:
        # Drive the composed deploy twice against the persistent container: the
        # first apply installs nginx and the landing page, the second converges.
        for attempt in (1, 2):
            plans, report, _ = await run(INVENTORY, deploy)
            print(f"\n== run {attempt}: plan (prepare) ==")
            print(format_plan(plans))
            if report is not None:
                print(f"== run {attempt}: log (apply) ==")
                print(format_log(report))
    except (CommandError, HostConnectionError, InventoryError) as exc:
        # An unreachable host (is beta running?), a failed command, or a
        # malformed inventory -- typed, naming the cause, with no traceback.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
