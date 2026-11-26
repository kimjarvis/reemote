# uv module

::: reemote.modules.uv.uv
    options:
      show_root_heading: false
      members:
        - PackageInfo
        - Packages



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

pyinfra's `uv.packages` operation is not ported yet, so the namespace
currently ships the fact alone; when the operation lands it reads `Packages`
for its idempotence, the `pip`/`npm`/`gem` precedent.

--->
