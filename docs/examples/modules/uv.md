# Module examples: uv

Scripts under `examples/modules/uv/` — one per fact and one per operation of
the [`uv`](../../modules/uv.md) module, whose four facts and six operations
mirror pyinfra's `uv` namespace, plus a container script that puts
`uv.packages`' `venv=` argument to work. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The provisioning helper (`provision_uv.py`) is not rendered here — it is the
shared prerequisite step the scripts below await, not a per-fact
demonstration: Ubuntu's noble apt suite ships no `uv`, so the helper installs
Astral's official `astral-uv` snap (classic confinement, so the binary lands
directly on the PATH), and the module deliberately drops pyinfra's
`requires_command` gating, so — because every fact command is bare (or, for
`Info` and `Pythons`, a chain anchored on a `uv` command) — a host without uv
exits 127 and the gather raises `CommandNotFoundError` rather than answering
an empty listing. Note what the snap does and does not do: it installs the
*tool*, not any environment — `uv pip list` reports whatever environment uv
itself resolves (a discovered `.venv` or the fallback interpreter), `uv tool
list` prints its "No tools installed" hint and `uv python list
--only-installed` the system interpreters uv discovers — so a bare host
honestly answers `[]` to all three, and `uv.Info` answers the record of a
freshly snapped install.

## Listing the packages in uv's environment

The fact example: `Packages` is the port of pyinfra's `uv.UvPipPackages`. It
runs pyinfra's command verbatim — `uv pip list --format json`, whose JSON
array is the whole stdout (uv's "Using Python ... environment at: ..." notice
rides on stderr and never reaches the parse) — and reports each mapping row as
a [`PackageInfo`][reemote.modules.uv.uv.PackageInfo] entry of the printed
`name` and `version`, kept verbatim and sorted, unique per name: unlike the
pip fact there is no PEP 503 normalization, because uv already prints each
distribution's canonical name. Left unparameterized, which environment is listed
is uv's own resolution rule, exactly as pyinfra leaves it to the tool; the fact's
optional `venv` argument names one instead (a virtual environment's root
directory, an interpreter path or a version request) and issues
`uv pip list --format json --python <venv>` — this script gathers the unscoped
listing, since a scoped read of a venv that does not exist yet exits 2 on the
target. The script follows the shared flow: it reads the project-root
`inventory.json` (validated up front with `validate_inventory`), awaits
`provision_uv.py` so the bare command runs against a real `uv`, guards the
gather to the `"Ubuntu"` family by filtering the host list — `gather_fact` has
no `when` parameter of its own — so the other demo hosts are named in a
`== Skipped (when guard is_ubuntu): ... ==` line and never connected, and
prints each host's entry list with `json.dumps(..., indent=2)`.

::: uv_packages
    options:
      members: []

## Reading the installation record

The next fact example: `Info` condenses what pyinfra scatters over
`UvVersion`, `UvToolDir`, `UvPythonDir` and an executable lookup into one
[`InstallInfo`][reemote.modules.uv.uv.InstallInfo] record gathered by a single
`&&`-chained shell — `uv --version`, `uv tool dir`, `uv tool dir --bin`, `uv
python dir`, `uv cache dir` and `command -v uv` — whose one-line-per-command
stdout the parse maps onto the fields positionally. Asking uv itself for the
directories is what makes the answer the *effective* one: the target shell's
`UV_TOOL_DIR`/`UV_PYTHON_DIR`/`UV_CACHE_DIR` and `XDG_*` overrides are already
applied, so the record names the paths a later uv operation would use. uv's
leading `uv ` version token and the build parenthetical current uv appends are
both dropped, so the version reads as a clean number, and a chain that stops
early leaves the fields past that point `None`. The script awaits
`provision_uv.py` and guards the gather exactly as the one above does.

::: uv_info
    options:
      members: []

## Listing the installed tools

The fact example for `Tools`: `uv tool list` has no JSON output, so the script
gathers the plain-text listing and reports each tool as a
[`ToolInfo`][reemote.modules.uv.uv.ToolInfo] entry — the printed name, the
version with uv's `v` stripped (`None` for a tool installed from a local path
or a git URL, which pyinfra's parser discards) and the sorted `entrypoints`
bullets beneath the header, the binaries uv linked onto the PATH that pyinfra
throws away. Entries come back sorted by name and unique per name, and uv's
"No tools installed" hint on an untouched host reads as `[]` instead of the
phantom tool pyinfra files from it.

::: uv_tools
    options:
      members: []

## Listing the interpreters present on the host

The fact example for `Pythons`: one chained shell runs `uv python dir`, then
`pwd`, then `uv python list --only-installed --output-format=json`, and each row
becomes a [`PythonInfo`][reemote.modules.uv.uv.PythonInfo] entry — uv's key, the
implementation, the version and the resolved path (anchored on the `pwd` the
chain prints, since uv lists a managed download relative to the working
directory) — with `managed` derived from whether that resolved path sits under
the directory the chain printed first, because uv's JSON carries no such flag.
Only *installed* interpreters are listed, so a bare host does not report the
whole downloadable catalogue, and — unlike pyinfra — the `--managed-python`
filter stays away, so the system interpreters the fact has to tell apart from
uv's own downloads remain visible. Entries are unique on `(key, path)`, since
uv repeats one key for the same interpreter found at several paths, and sorted
by that pair.

::: uv_pythons
    options:
      members: []

## Installing and removing uv packages

The operation example for [`uv.packages`](../../modules/uv.md): the `uv pip`
counterpart of `pip.packages`, it installs, upgrades or removes packages given
as bare names or `<pkg>==<version>` pins. The script awaits `provision_uv.py`,
then yields the same install of `six==1.16.0` twice and the same removal twice,
guarded by `when=is_ubuntu`: prepare plans all four as changes, while apply runs
the first install, reports the second as a no-op against the fresh per-step
`Packages` cache, then removes the package and no-ops the repeat — a
self-restoring cycle. A pinned request is present only at that exact version, an
unpinned one at any version, and unlike `pip.packages` a request and a fact entry
meet at the lowercased name alone (uv prints canonical names, so no PEP 503
folding); removal feeds `uv pip uninstall` bare names, and every command keeps
`--no-progress`. uv installs into the environment it resolves when `venv` is
unset — a deploy can still export `VIRTUAL_ENV` through `op()`'s `env` prefix —
but `venv=` names one explicitly, onto uv's `--python` on both the `Packages`
read and every command (this script stays unscoped because a scoped demo needs
the venv to pre-exist; `uv_packages_venv.py` below creates one and passes
`venv=`); the change is unprivileged.

::: uv_packages_op
    options:
      members: []

## Managing one virtualenv inside a container

The second `uv.packages` script is the one that actually passes `venv=`, and it
runs inside a **local container**: its inline inventory entry selects the
`docker` connector with `identifier: "ubuntu:24.04"` and `docker_cmd:
"podman"`, so the prerequisite is a container CLI with a daemon rather than an
SSH host, and every command — the fact reads included — rides `docker exec`.

Why a container for the scoped demo: `run` gathers the scoped `Packages` read in
the prepare phase, before it has sent anything, and `uv pip list --format json
--python <missing>` exits 2 on the target — so on the shared SSH host the venv
would have to exist out of band, and the script would be comparing against state
it never created. Here the deploy builds that state itself: `apt.packages`
installs `python3` and `python3-pip` (a stock noble image has neither the
interpreter nor an apt index), `pip.packages` installs `uv` with
`--break-system-packages` (noble ships no `uv` package, and PEP 668 marks the
system interpreter externally managed), `uv.venv` creates `/srv/app/.venv`, and
only then does `uv.packages` name it. Each step depends on what the step before
it applied, which one `run()` cannot express — its prepare phase reads every fact
against pre-deployment state — so the script drives the deploy with
[`run_waves`](../../stages.md), which materializes and applies one request per step.
The scoped requests then issue uv's `--python` on the read *and* the command,
rendered in the log as `uv pip install --no-progress --python /srv/app/.venv
six==1.16.0`, the repeat's `[no change: all packages present]` from a fresh
scoped listing, `uv pip uninstall --no-progress --python /srv/app/.venv six`
(bare names, as always for removal) and that request's own no-op.

Two consequences of the container target show up in the output. No request
carries `sudo`: `docker exec` already runs as the image's default user (root),
and a stock ubuntu image has no `sudo` binary. And there is no `dry=True`
preview, unlike the SSH examples — a dry wave would plan the scoped request
against the pristine image and fail on the missing venv, which is the very
constraint the script's ordering exists to satisfy, so it prints one real
[`format_waves`](../../stages.md) tree instead (its lines render through the same
`format_plan`/`format_log` the SSH examples use). `run_waves` owns the host and
closes it once, which commits the throwaway container into a new image — the
short ID the script prints from
[`DockerHost.image_id`](../../connectors/docker.md) — and removes it, so nothing
lingers and a rerun starts pristine: the convergence on display is the one
*within* the run. Point `identifier` at a persistent named container instead and
the state survives across runs, since the connector never commits or removes a
container it did not create.

::: uv_packages_venv
    options:
      members: []

## Installing and removing a uv-managed interpreter

The operation example for [`uv.pythons`](../../modules/uv.md): one `uv python
install`/`uv python uninstall` command per version. The script installs `3.13`
twice and removes it twice to show idempotence, awaiting `provision_uv.py` and
guarding `when=is_ubuntu`. Where pyinfra keys its comparison on the exact version
string — so a `"3.13"` request never matches an installed `"3.13.4"` and
reinstalls forever — reemote compares dotted components, so `3.13` converges once
any `3.13.*` interpreter is present; `managed=True` (the default) counts only the
entries the `Pythons` fact flagged uv-managed. Installs write to uv's own download
directory and are unprivileged.

::: uv_pythons_op
    options:
      members: []

## Installing and removing an isolated uv tool

The operation example for [`uv.tools`](../../modules/uv.md): one `uv tool
install`/`uv tool uninstall` command per tool. The script installs `ruff==0.8.4`
twice and removes it twice (feeding uninstall the bare `ruff` name), awaiting
`provision_uv.py` and guarding `when=is_ubuntu`. Presence is dict membership,
not a truthy version: a tool installed from a local path or git URL carries
`version=None` yet is installed, so a bare name matching it converges — where
pyinfra discarded such a versionless header and reinstalled forever. `latest=True`
would upgrade an installed unpinned tool with `uv tool install --upgrade`.
`python=` and `with_packages=` declare the tool's isolated environment (uv's
`--python` and its repeatable `--with`), riding on the install and the upgrade but
never the uninstall; because the gathered fact reports neither, changing only
them does not reinstall a tool already at the requested version — `latest=True`
or a removal converges on that drift. The script installs plain `ruff` rather
than demoing the flags, which would download a pinned interpreter on the shared
host. Installing links executables into the user's uv tool bin directory and is
unprivileged.

::: uv_tools_op
    options:
      members: []

## Upgrading every installed uv tool

The operation example for [`uv.tools_upgrade_all`](../../modules/uv.md): a
deliberately stateless, non-idempotent `uv tool upgrade --all` — it reads no
fact, always reports a change and always yields, so the script sends it once
(no second no-op request) after `provision_uv.py`, guarded `when=is_ubuntu`. uv
itself decides per tool whether an upgrade exists; `extra_args` (a single
free-form string) rides unquoted onto the end. Upgrading writes to uv's own tool
directory and is unprivileged.

::: uv_tools_upgrade_all
    options:
      members: []

## Putting uv's tool bin directory on the PATH

The operation example for [`uv.tools_update_shell`](../../modules/uv.md): `uv
tool update-shell` run only when uv's tool executable directory is missing from
the login shell's `PATH`. The script yields the request twice after
`provision_uv.py`, guarded `when=is_ubuntu`, to show the two-phase convergence.
The operation resolves the directory from the `Info` record's `tool_bin_dir` and
the `PATH` from `server.Env("PATH")` (colon-split by the operation), the
reemote generalization of pyinfra's `Path` fact; with uv uninstalled the
`Info` chain short-circuits and an unknown directory is "nothing to add", not a
perpetual re-yield. Editing the user's shell config is unprivileged.

::: uv_tools_update_shell
    options:
      members: []

## Creating and removing a uv virtualenv

The operation example for [`uv.venv`](../../modules/uv.md): create (and, with
`present=False`, remove) a virtualenv with `uv venv`, idempotent on a private
existence probe. The script creates `/tmp/reemote-uv-example-venv` with
`seed=True` and `python="3.13"` twice and removes it twice, awaiting
`provision_uv.py` and guarding `when=is_ubuntu`. `path` is the venv directory
throughout — the probe, the create and the `rm -rf` removal all point at it —
correcting pyinfra's own `.venv/bin/activate`-versus-create-at-`<path>` mismatch;
`allow_existing`/`clear_existing` are mutually exclusive and rejected, with an
empty `path`, before the fact read. The venv sits in the user's own `/tmp` path,
so the whole cycle is unprivileged and self-restoring.

::: uv_venv
    options:
      members: []

