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



<!---

`reemote.modules.uv.uv` holds the uv facts. `Packages` is the port of
pyinfra's `uv.UvPipPackages`: it runs pyinfra's command verbatim —
`uv pip list --format json`, whose JSON array is the whole stdout (uv's
"Using Python ... environment at: ..." notice rides on stderr) — and reports
the Python packages of the environment uv resolves as sorted, unique
`PackageInfo` entries of the printed name and version. Which environment that
is — an active or discovered `.venv`, or the interpreter uv falls back to —
is uv's own resolution rule, exactly as pyinfra leaves it to the tool.

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
number (pyinfra's `UvVersion` returned the whole `uv 0.8.5 (Homebrew ...)`
line); a chain that stops early, because an older uv lacks a dir subcommand,
leaves the fields past that point `None` where pyinfra's separate facts
answered their `default = ""`. `uv self version` exists on self-managed
installs but prints the same text, so the bare `--version` is the one probe.

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
them uv owns, with one chained shell: `uv python dir` and then `uv python
list --only-installed --output-format=json`. `--only-installed` is pyinfra's
own filter and is kept — without it uv prints its downloadable catalogue, so
a bare machine would appear to hold dozens of Pythons. pyinfra's
`--managed-python` default is deliberately dropped: verified against uv, that
flag hides every system interpreter, so the managed-versus-system split the
fact reports cannot be read from an answer that already excludes one side.
Since uv's JSON carries no managed flag either, the directory is asked for in
the same shell and an interpreter under it is `managed=True`, one at
`/usr/bin/python3` `managed=False`. And where pyinfra's `process_json` keys a
`dict[str, set[str]]` by uv's `key`, that key repeats for one interpreter
discovered at several paths, so a keyed dict silently overwrites; reemote
returns `PythonInfo` entries unique on `(key, path)` and sorted by that pair.

pyinfra's `uv.packages` operation is not ported yet, so the namespace
currently ships the four facts alone; when the operation lands it reads
`Packages` for its idempotence, the `pip`/`npm`/`gem` precedent, and a
`uv.tool_install` counterpart would read `Tools` the same way.

--->
