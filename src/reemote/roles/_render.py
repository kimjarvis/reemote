# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The single jinja2 / PyYAML import site for the template-rendering roles (§17.4).

Twelve roles render a control-side template while building their deploys: eleven
use Jinja2 (``apache``, ``elasticsearch``, ``firewall``, ``memcached``,
``minio``, ``nginx``, ``ntp``, ``postgresql``, ``redis``, ``systemd``,
``zabbix_agent``) and ``datadog`` dumps YAML with PyYAML. As scripts they
each did a lazy
``try: import jinja2`` with an ``except ImportError`` branch printing
``uv run --with jinja2 python ...``. That advice is a script's privilege, not a
library's: a consumer who installed the base ``reemote`` cannot be told to
re-run the interpreter with a different environment. So both third-party imports
happen once here, at module scope, guarded by the ``reemote[templates]`` extra::

    uv pip install "reemote[templates]"    # or: pip install "reemote[templates]"

A role imports the module it needs *through* this seam and keeps its own call
sites verbatim -- only the ``import`` line changes::

    from reemote.roles._render import jinja2, yaml

Nothing is re-templated by moving here. datadog's four YAML renderers and php's
config renderers are hand-written Python that already emit what the Ansible
``.j2`` / ``to_nice_yaml`` emitted; they stay that Python, and only their
``import yaml`` relocates to this module (§17.4). Converting them to real Jinja2
would be a rewrite dressed up as a relocation, so this module deliberately
exposes the third-party modules themselves rather than a render helper, and
stays out of every role body's control flow.

The one exception is :func:`jinja2_environment`, which is a settings object
rather than a helper: the whitespace and ``StrictUndefined`` choices every
Ansible-rendered template reproduced have to be identical across the roles that
render one, so the construction lives here once (§18.2, §18.3). A role keeps its
own template text, its own context and its own render call.

Roles import this module *inside* the render function that needs it -- never
``reemote/roles/__init__.py``, which re-exports the roles eagerly -- so
``import reemote.roles`` and every non-template role stay clean without the
extra. The :class:`ImportError` arrives when a template actually renders, which
is the moment the consumer needs :command:`pip install "reemote[templates]"`.
"""

from typing import Any

import jinja2
import yaml


def jinja2_environment(**overrides: Any) -> jinja2.Environment:
    """The ``Environment`` an Ansible-rendered template needs.

    Ansible's whitespace defaults -- ``trim_blocks=True`` (the newline after a
    ``{% %}`` block tag is dropped), ``lstrip_blocks=False``,
    ``keep_trailing_newline=True`` -- plus ``StrictUndefined``, so a variable the
    template reads but the role never set fails loudly instead of rendering
    empty. Keyword arguments in ``overrides`` replace these, for a role whose
    bundled template was written against different settings.

    Args:
        **overrides: Keyword arguments that override the defaults above.

    Returns:
        jinja2.Environment: A bare environment; callers use ``from_string`` on
            template text they own, never a loader over a filesystem directory.
    """
    settings: dict[str, Any] = {
        "trim_blocks": True,
        "lstrip_blocks": False,
        "keep_trailing_newline": True,
        "undefined": jinja2.StrictUndefined,
    }
    settings.update(overrides)
    return jinja2.Environment(**settings)


__all__ = ["jinja2", "jinja2_environment", "yaml"]
