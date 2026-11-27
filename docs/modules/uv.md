# uv module

::: reemote.modules.uv.uv
    options:
      show_root_heading: false
      members:
        - PackageInfo
        - Packages
        - InstallInfo
        - Info
        - ToolInfo
        - Tools
        - PythonInfo
        - Pythons
        - packages
        - pythons
        - tools
        - tools_upgrade_all
        - tools_update_shell
        - venv



<!---

`reemote.modules.uv.uv` holds the uv facts. `Packages` is the port of
pyinfra's `uv.UvPipPackages`: it runs pyinfra's command verbatim —
`uv pip list --format json`, whose JSON array is the whole stdout (uv's
"Using Python ... environment at: ..." notice rides on stderr) — and reports
the Python packages of the environment uv resolves as sorted, unique
`PackageInfo` entries of the printed name and version. Which environment that
is — an active or discovered `.venv`, or the interpreter uv falls back to —
is uv's own resolution rule, exactly as pyinfra leaves it to the tool, unless
the fact's optional `venv` argument names one: it is passed to `command()` and
issued as `uv pip list --format json --python <venv>`.

Three deliberate deviations from pyinfra. pyinfra's `process_json` returns a
`dict[str, set[str]]` keyed by name and warns on a failed JSON load; reemote
reports the entry-list shape every package-list fact here uses (the
`PipPackageInfo`/`NpmPackageInfo` precedent — deterministic and
JSON-printable) and parses total: unparsable output, a non-list document, a
non-mapping row or a row without string `name`/`version` values contribute
nothing, the `NpmPackages` rule, where pyinfra's key-first indexing would
raise. And pyinfra gates the fact on its `requires_command = "uv"` (no uv, no
command, silently an empty dict); reemote runs the command bare, so a host
without uv exits 127 and the gather raises `CommandNotFoundError` — an
environment without uv is an error the deployer hears about, not a silently
empty fact — while a host with uv but an empty environment honestly answers
`[]`, the counterpart of pyinfra's `default = dict`.

Names are kept verbatim: uv prints the canonical name of each distribution
and pyinfra performs no normalization either, so — unlike the pip fact, which
normalizes PEP 503 — there is no shared helper and simulated state keys its
store by the printed spelling.

`Info` is the design's consolidation of pyinfra's scattered uv scalars —
`UvVersion`, `UvToolDir`, `UvPythonDir` and the executable lookup — into one
low-overhead record: a single `&&`-chained shell asks uv six questions
(`uv --version`, `uv tool dir`, `uv tool dir --bin`, `uv python dir`, `uv
cache dir`, `command -v uv`), each printing exactly one stdout line, so the
parse maps the lines onto the `InstallInfo` fields positionally in one round
trip. Because every directory comes from uv itself, the record reflects the
*effective* paths after the target shell's `UV_*` and `XDG_*` overrides — the
same environment a later uv operation runs in. The version keeps only its
number: uv's leading `uv ` token and the build parenthetical its release and
packager binaries append are both dropped, so the snap's
`uv 0.12.21 (7af826859 2026-09-29 x86_64-unknown-linux-gnu)` reads
`"0.12.21"` where pyinfra's `UvVersion` returned the whole line; a chain that
stops early, because an older uv lacks a dir subcommand, leaves the fields past
that point `None` where pyinfra's separate facts answered their `default = ""`.
`uv self version` exists on self-managed installs but prints the same text, so
the bare `--version` is the one probe.

`Tools` reports the isolated CLI tools uv installed — which ones, at what
version, and which executables each linked onto the PATH — from pyinfra's
`uv tool list`, which has no JSON output, so the plain-text grammar is
parsed: a header line names the tool and its `v`-prefixed version, the
bullets beneath it are the entrypoints, and the entries come back sorted by
name with sorted, deduplicated entrypoints. Two pyinfra behaviours are
corrected: its parser skips every bullet, so nothing it returns says which
binaries landed on the PATH, and it warns a versionless header away entirely
(`["pyinfra"]` answers `{}`) — here such a tool, the usual shape for a local
path or git source, keeps its record with `version=None`. pyinfra also files
uv's empty-state hint — real uv prints `No tools installed` and exits 0 — as
a tool named `No` at version `tools`; a row that is neither a header nor a
bullet is noise, so an untouched host honestly answers `[]`.

`Pythons` answers which interpreters are *present* on the host, and which of
them uv owns, with one chained shell: `uv python dir`, then `pwd`, then `uv
python list --only-installed --output-format=json`. `--only-installed` is
pyinfra's own filter and is kept — without it uv prints its downloadable
catalogue, so a bare machine would appear to hold dozens of Pythons. pyinfra's
`--managed-python` default is deliberately dropped: verified against uv, that
flag hides every system interpreter, so the managed-versus-system split the
fact reports cannot be read from an answer that already excludes one side.
Since uv's JSON carries no managed flag either, the directory is asked for in
the same shell and an interpreter under it is `managed=True`, one at
`/usr/bin/python3` `managed=False`; and because uv prints a managed download's
path relative to the shell's working directory whenever that directory is an
ancestor of it, `pwd` is captured too and every relative path is anchored on it
before the comparison — without that anchoring a uv-managed interpreter reads as
a system one. And where pyinfra's `process_json` keys a `dict[str, set[str]]`
by uv's `key`, that key repeats for one interpreter discovered at several paths,
so a keyed dict silently overwrites; reemote returns `PythonInfo` entries unique
on `(key, path)` and sorted by that pair.

The six operations are the ports of pyinfra's `uv` operations, each reading
the fact above it for idempotence and yielding `uv ...` shells through the
module's frozen command descriptors (rendered byte-exact to pyinfra's own
command builders, all keeping its `--no-progress` so uv never repaints a
progress bar into captured stdout).

`uv.packages` is the `uv pip` counterpart of `pip.packages` and reads
`Packages`: it installs, upgrades or removes bare names or `<pkg>==<version>`
pins, or applies a `requirements` file. A pinned package is present only when
that *exact* version is installed (the `ensure_packages` rule apt, apk and pip
follow), an unpinned one when the name is installed at any version, and other
PEP 508 specifiers (`>=`, `~=`) are not interpreted — reemote carries no
`packaging` dependency — so they degrade to that name-only presence. Unlike
`pip.packages` (which normalizes PEP 503), a request and a fact entry meet at
the *lowercased* name alone: uv already prints each distribution's canonical
name, so `-`/`_`/`.` folding would be wrong. `latest=True` upgrades every
installed, unpinned package with `--upgrade`, pyinfra's legacy behavior. The
`requirements` direction is stateless — the file's command is yielded on every
run, before any package command. Removal feeds `uv pip uninstall` *bare names*,
since a pinned requirement token is rejected there, and `extra_args` (a single
`str`, not pyinfra's `list`) rides unquoted on install, upgrade and uninstall
alike. Installing into the environment uv resolves is per-user and
unprivileged; only a system interpreter's site-packages needs `sudo=True`.

Which environment that is becomes explicit with `venv=`: a virtual
environment's root directory (uv accepts that as readily as an interpreter path
or a version request), quoted onto uv's `--python`. It threads through the
`Packages` read as well as onto every command — exactly as `pip.packages`
threads its `pip` executable into `PipPackages` — because the fact has to report
the environment the commands mutate: scoping the commands alone would check one
environment while installing into another, and the operation would never
converge. An empty or blank `venv` raises `ValueError` before the fact read, the
`uv.venv` validation precedent, and that guard is not decoration: verified
against the CLI, `uv pip list --python ''` exits 0 and silently lists whatever
ambient environment it resolves, so a typo would retarget a deploy instead of
failing it. An `op()` `env` prefix (`VIRTUAL_ENV`, `UV_PYTHON`) still works for
callers who prefer it, but a named target needs no PATH trick and no
`extra_args` fragment. Naming one also needs it to exist: `uv pip list --python
<missing>` exits 2, so the venv is created by an earlier request — and
`examples/modules/uv/uv_packages_venv.py` runs that whole sequence for real,
inside a container.

`uv.pythons` installs or removes uv-managed interpreter versions and reads
`Pythons`, one `uv python install`/`uv python uninstall` descriptor per version
(uv takes one request at a time). Where pyinfra keys its comparison on the
exact version string — so a `"3.13"` request never matches an installed
`"3.13.4"` and reinstalls forever — reemote compares dotted components, so a
request is satisfied when its components are a prefix of some installed version
and the operation converges (uv resolves the same partial request). pyinfra's
`managed` flag fed `--managed-python` to both the commands and the fact; the
fact dropped that flag (it hides every system interpreter), so here `managed`
is purely the idempotence filter — `True` counts only the `managed` entries,
`False` counts every interpreter present.

`uv.tools` installs, upgrades or removes isolated CLI tools and reads `Tools`,
one descriptor per tool. Presence is dict membership, not a truthy version: a
tool installed from a local path or git URL carries `version=None` yet is
installed, so a bare name matching it converges (pyinfra discarded such a
versionless header and reinstalled forever). `latest=True` upgrades an
installed, unpinned tool via `uv tool install --upgrade`; removal feeds the
bare name to `uv tool uninstall`.

`python=` and `with_packages=` declare the tool's isolated environment — uv's
`--python` interpreter and its repeatable `--with` extra runtime requirements —
and ride on the install and the upgrade descriptor, the two verbs uv accepts
them on; `uv tool uninstall` takes neither, so the removal direction ignores
them. Idempotence deliberately stays keyed on name and version alone: the
gathered fact is the plain `uv tool list`, which reports neither flag (uv can
print them with `--show-python`/`--show-with`, but that is not the gathered
command), so a tool already installed at the requested version is not
reinstalled merely because its declared environment changed. Converge on such
drift with `latest=True`, whose upgrade re-creates the environment and carries
both flags, or by removing the tool first.

`uv.tools_upgrade_all` is the stateless, fact-free `uv tool upgrade --all` — it
always yields and reports a change — and `uv.tools_update_shell` resolves the
tool bin directory from `Info` and the
`PATH` from `server.Env("PATH")` (colon-split by the operation), yielding `uv
tool update-shell` only when the directory is missing from the PATH; with uv
uninstalled the directory is unknown and nothing is re-yielded.

`uv.venv` creates or removes a `uv venv` virtualenv, idempotent on a private
existence probe (`_VenvExists`, a guarded `test -e <path>/bin/activate`).
pyinfra keyed its check on `<path>/.venv/bin/activate` yet created the venv *at*
`<path>` — an upstream path mismatch — so reemote treats `path` as the venv
directory throughout. `allow_existing`/`clear_existing` are mutually exclusive
and, with an empty `path`, rejected before the fact read so a bad request fails
without an SSH round trip; removal is the whole-tree `rm -rf` pyinfra delegates
to `files.directory(present=False)`, kept uv-local.

--->
