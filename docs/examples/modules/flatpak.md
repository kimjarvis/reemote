# Module examples: flatpak

Scripts under `examples/modules/flatpak/` — one per fact or operation of the
[`flatpak`](../../modules/flatpak.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering example (`flatpak_flatpak_package.py` reading
`FlatpakPackage` for one package over `gather_fact`, guarded to the `ubuntu`
group) is not rendered here — it reads a single fact, and the fact itself is
documented on the [`flatpak` module page](../../modules/flatpak.md).

## Installing and removing flatpak applications

The package operation: `flatpak.packages` installs (or with `present=False`
removes) flatpak applications by their reverse-domain IDs, reading the
[`FlatpakPackages`][reemote.modules.flatpak.flatpak.FlatpakPackages] fact
once for name-membership idempotence — flatpak application IDs carry no
version to compare, so a listed ID is present — and re-running is a no-op.
An optional `remote` names the source to install from (e.g. `flathub`),
applied to the install direction only, as in the baseline. The script installs
one application from `flathub`, then removes it again — a self-restoring
cycle that leaves the shared Ubuntu host's flatpak state as it found it —
yielding each request twice so the second of each pair is a guaranteed
no-op in the apply phase through the fresh per-step fact cache. Installing
and removing both write system state, so the requests are
flagged `sudo=True`. Like the other flatpak examples the script reads the
shared project-root `inventory.json` (validated up front with
`validate_inventory`) and every request carries `when=is_ubuntu`, because
flatpak is set up out of the box on Ubuntu — on a non-Ubuntu host neither
the `flatpak install`/`flatpak uninstall` commands nor the fact behind them
run, and plan and log render `[skipped: when guard is_ubuntu]`.

::: flatpak_packages
    options:
      members: []
