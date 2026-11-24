# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The single jinja2 / PyYAML import site for the template-rendering roles (§17.4).

Six roles render a control-side template while building their deploys: five use
Jinja2 (``apache``, ``memcached``, ``nginx``, ``ntp``, ``postgresql``) and
``datadog`` dumps YAML with PyYAML. As scripts they each did a lazy
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

This module is imported lazily by the roles that need templates, never by
``reemote/roles/__init__.py``, so importing the package without the extra does
not raise -- only importing a template role does.
"""

import jinja2
import yaml

__all__ = ["jinja2", "yaml"]
