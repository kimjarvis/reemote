# Module examples: uv

Scripts under `examples/modules/uv/` — one per fact of the
[`uv`](../../modules/uv.md) module, whose four facts are the namespace's sole
surface until pyinfra's `uv.packages` operation lands. See the
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
distribution's canonical name. The fact takes no arguments; which environment
is listed is uv's own resolution rule, exactly as pyinfra leaves it to the
tool. The script follows the shared flow: it reads the project-root
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
leading `uv ` version token is stripped, and a chain that stops early leaves
the fields past that point `None`. The script awaits `provision_uv.py` and
guards the gather exactly as the one above does.

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

The fact example for `Pythons`: one chained shell runs `uv python dir` and then
`uv python list --only-installed --output-format=json`, and each row becomes a
[`PythonInfo`][reemote.modules.uv.uv.PythonInfo] entry — uv's key, the
implementation, the version and the resolved path — with `managed` derived from
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
