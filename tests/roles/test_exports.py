# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Invariants for the ``reemote.roles`` package skeleton (§17.5).

Three things are pinned here:

* ``reemote.roles.__all__`` is exactly the union of the public role modules' own
  ``__all__`` as they sit on disk, and every one of those names is reachable on
  the package;
* no role is star-imported -- or otherwise pulled -- into the top-level
  ``reemote`` namespace, and ``roles/__init__.py`` does not eagerly import the
  template seam, so ``import reemote.roles`` stays clean without the extra;
* ``reemote.postgresql`` remains the operation-module namespace, not a role.

The disk comparison is driven by the files present, so the suite is green now
(no role modules migrated yet) and stays green as T4-T17 land them one commit
at a time: each migration adds a module and re-exports its names, and this test
fails if either half drifts.
"""

import ast
import importlib
import pathlib

import reemote
import reemote.roles

ROLES_DIR = pathlib.Path(reemote.roles.__file__).resolve().parent
REEMOTE_INIT = pathlib.Path(reemote.__file__).resolve()


def public_role_modules() -> list[str]:
    """Role module basenames on disk, excluding the private helpers.

    ``__init__.py`` and ``_render.py`` both start with an underscore and are not
    roles; only real role modules participate in the ``__all__`` contract.
    """
    return sorted(
        path.stem for path in ROLES_DIR.glob("*.py") if not path.name.startswith("_")
    )


def test_all_is_the_public_role_modules_on_disk():
    """``reemote.roles.__all__`` matches what the role modules on disk export."""
    from_disk: list[str] = []
    for name in public_role_modules():
        module = importlib.import_module(f"reemote.roles.{name}")
        from_disk += list(getattr(module, "__all__", []))

    assert sorted(reemote.roles.__all__) == sorted(from_disk)


def test_every_exported_name_is_reachable():
    """Each name in ``__all__`` is an attribute of the package."""
    missing = [name for name in reemote.roles.__all__ if not hasattr(reemote.roles, name)]

    assert not missing, f"reemote.roles.__all__ names not exported: {missing}"


def test_roles_are_not_imported_into_reemote():
    """``reemote/__init__.py`` never pulls in the roles package.

    A role name such as ``postgresql`` would otherwise shadow the module
    namespace, so roles are reached only as ``reemote.roles.<role>`` (§17.1).
    """
    tree = ast.parse(REEMOTE_INIT.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root == "roles":
                msg = f"reemote/__init__.py imports roles at line {node.lineno}"
                raise AssertionError(msg)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "reemote.roles" or alias.name.startswith("reemote.roles."):
                    msg = f"reemote/__init__.py imports roles at line {node.lineno}"
                    raise AssertionError(msg)


def test_roles_init_does_not_import_the_template_seam():
    """``roles/__init__.py`` must not import ``_render`` eagerly.

    Otherwise importing the package would require the ``reemote[templates]``
    extra just to touch a non-template role (§17.4).
    """
    tree = ast.parse((ROLES_DIR / "__init__.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "_render":
            raise AssertionError("roles/__init__.py eagerly imports _render")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[-1] == "_render":
                    raise AssertionError("roles/__init__.py eagerly imports _render")


def test_postgresql_stays_module_namespace():
    """``reemote.postgresql`` is the operation module, not a role factory."""
    assert reemote.postgresql.__name__ == "reemote.modules.postgresql"
