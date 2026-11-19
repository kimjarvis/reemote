# pip module

::: reemote.modules.pip.pip
    options:
      show_root_heading: false
      members:
        - PipPackageInfo
        - PipPackages
        - packages
        - venv
        - virtualenv



<!---

`reemote.modules.pip.pip` holds the pip fact and the `pip.packages`,
`pip.virtualenv` and `pip.venv` operations. `PipPackages` reports the
installed pip packages as
`PipPackageInfo` entries — the PEP 503-normalized `name` plus the
`version` exactly as `pip freeze` printed it — parsed from the
`<name>==<version>` rows of `<pip> freeze --all`; it mirrors the baseline's
`pip.PipPackages` fact and keeps its `PIP_REGEX` row shape, so editable
`-e ...` installs, `name @ url` direct references and any other unparsable
line contribute nothing. The `--all` is deliberate: without it `pip freeze`
hides pip, setuptools and wheel, and an operation managing those names would
never see them installed. Unlike the baseline, whose `requires_command`
silently answers an empty dict on a host without pip, reemote runs the
command bare — a missing pip exits 127 and surfaces as
`CommandNotFoundError`. The answer is the sorted, unique entry list every
package-list fact here uses (the `ApkInstalledPackages` precedent) rather
than the baseline's `dict[str, list[str]]`, and names normalize at parse
time (`Zope.Interface`, `zope_interface` and `ZOPE-INTERFACE` collapse to
the single `zope-interface` entry seen last) because the comparison is
normalized. The `pip` argument selects the environment — a bare `pip`/`pip3`
on the PATH or a virtualenv's `bin/pip` — and defaults to `"pip"`.

`pip.packages` is the operation counterpart of the baseline's
`pip.packages`: it installs, upgrades or removes packages given as bare
names or `<pkg>==<version>` pins, or applies a `requirements` file. Its
idempotence reads `PipPackages` once, so a pinned package is present only
when that *exact* version is installed (the `ensure_packages` rule
`apt.packages` and `apk.packages` also follow) and an unpinned one when the
name is installed at any version; other PEP 508 specifiers (`>=`, `~=`) are
not interpreted — reemote carries no `packaging` dependency — and degrade to
that name-only presence. `latest=True` follows the baseline's legacy
behavior and upgrades every installed, unpinned package with `pip install
--upgrade`. The `requirements` direction is stateless: the file's command is
yielded on every run, before any package command, as in the baseline. The
four commands render through the `PipInstallCommand`/`PipUpgradeCommand`/
`PipRemoveCommand`/`PipRequirementsCommand` descriptors in the baseline's
own verb order (`pip install [extra] <packages>`, `pip install [extra]
--upgrade <packages>`, `pip uninstall --yes <names>`, `... -r <file>`);
removal is version-aware but fed to pip as *bare names*, since `pip
uninstall` rejects `==` requirement tokens. The baseline's `virtualenv` and
`virtualenv_kwargs` options are deliberately dropped (a dict kwarg would
break the hashable-request rule `op()` enforces, and venv creation belongs
to the sibling `pip.virtualenv`/`pip.venv` operations) — a virtualenv is
created by request and targeted here by passing its interpreter as
`pip=<venv>/bin/pip` instead. Installing into a system
interpreter's site-packages writes system state, so those requests are
flagged `sudo=True` (a per-user or virtualenv pip is not); the fact read is
unprivileged.

`pip.virtualenv` and `pip.venv` are the two faces of the baseline's shared
`_virtualenv` builder: the first drives the `virtualenv` tool
(`virtualenv [-p <python>] [--system-site-packages] [--always-copy] <path>`),
the second the stdlib (`<python or "python"> -m venv
[--system-site-packages] [--copies] <path>`), through the
`PipVenvCreateCommand`/`PipVenvRemoveCommand` descriptors. Idempotence goes
beyond the baseline, whose existence-only check (a `files.File` fact on
`bin/activate`) makes a later `python=` change a silent no-op — upstream
bug #2028. A private, module-local probe reads the existing tree's own
interpreter (`{ test -x <path>/bin/python && ... ; } || true` — runnable
`bin/python`, not just an activate file, and no exception when nothing is
there), and when `python=` is given the requested interpreter is resolved
through `server.PythonVersion`; the two meet at `major.minor`, so a micro
bump of the base interpreter leaves the tree alone while a real mismatch
yields remove-then-create — the recreate is destructive to packages
installed inside, the chosen price of a deploy that converges the
interpreter it declares. With no `python=` requested, a runnable virtualenv
is simply converged. Removal (`present=False`) is the same whole-tree
`rm -rf` the baseline performs through `files.directory(present=False)`,
kept pip-local. Creating under a root-owned parent needs `sudo=True`; a
path in the connected user's home does not.

--->
