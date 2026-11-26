# Module examples: uv

Scripts under `examples/modules/uv/` — one per fact of the
[`uv`](../../modules/uv.md) module (the namespace's sole surface until
pyinfra's `uv.packages` operation lands). See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The provisioning helper (`provision_uv.py`) is not rendered here — it is the
shared prerequisite step the scripts below await, not a per-fact
demonstration: Ubuntu's noble apt suite ships no `uv`, so the helper installs
Astral's official `astral-uv` snap (classic confinement, so the binary lands
directly on the PATH), and the module deliberately drops pyinfra's
`requires_command` gating, so — because the fact command is bare — a host
without uv exits 127 and the gather raises `CommandNotFoundError` rather than
answering an empty listing. Note what the snap does and does not do: it
installs the *tool*, not any environment — `uv pip list` reports whatever
environment uv itself resolves (a discovered `.venv` or the fallback
interpreter), so a bare host honestly answers that fallback environment's own
packages, `[]` when it holds none.

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
