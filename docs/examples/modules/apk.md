# Module examples: apk

Scripts under `examples/modules/apk/` — one per fact or operation of the
[`apk`](../../modules/apk.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`apk_apk_packages.py` reading
`ApkInstalledPackages`, `apk_apk_explicit_packages.py` reading
`ApkExplicitPackages`, `apk_apk_autoremovable.py` reading
`ApkAutoremovablePackages`, `apk_apk_repositories.py` reading `ApkRepositories`
and `apk_apk_package.py` reading `ApkPackage` for one package, all over
`gather_fact`) are not rendered here — they read a single fact, and the facts
themselves are documented on the [`apk` module page](../../modules/apk.md).

## Installing packages

The package operation: `apk.packages` installs (or with `present=False`
removes) packages by name, reading the
[`ApkInstalledPackages`][reemote5.modules.apk.apk.ApkInstalledPackages] fact
(a list of `ApkPackageVersion` entries, grouped by `name` and compared on the
entries' `version`s) for exact-version idempotence — a `<pkg>=<version>` pin
counts as present only when that exact version is installed, so re-running is
a no-op. `latest=True` additionally upgrades every installed, *unpinned* name
with `apk upgrade` (pyinfra's legacy blind upgrade). apk never prompts, so the
commands carry no `-y` flag, unlike the apt and dnf package examples. Where
pyinfra's `apk.packages` also takes
`update`/`upgrade` options that prefix a whole-system `apk update` /
`apk upgrade`, reemote5 deliberately drops both. Two apk-native install
modifiers ride on the add/upgrade verbs: `no_cache` adds `--no-cache` (bypass
the package cache entirely) and `no_scripts` adds `--no-scripts` (skip
post-install triggers and scriptlets); both flow into the `latest` upgrade
path too, are ignored by the removal direction, and are a deliberate apk-only
asymmetry (apt's `no_recommends`/`allow_downgrades` and dnf's `nobest` are the
closest cousins, but neither tool exposes a cache-bypass or script-skip flag).
The script yields the same install request twice — the second is a guaranteed
no-op in the apply phase, because the first install is visible to it through
the fresh per-step fact cache (in the prepare phase, where nothing is sent,
both plan as changes) — then a third request that demonstrates both modifiers
together on a different package. Installing writes to the root-owned apk
database, so the requests are flagged `doas=True` and the driver elevates the
commands — `doas` rather than `sudo` because the Alpine host ships no `sudo`
(a `sudo apk add` would fail with exit 127 before reaching `apk`), and
`doas` is passwordless, so the host needs a `permit nopass` rule in
`/etc/doas.conf`.

The inventory is read from the project root's `inventory.json` (validated up
front with `validate_inventory`), and every request carries
`when=is_alpine` — the request-level counterpart of the `when` guard the
[`composed_when_guard`](../demonstrations/composition.md) demonstration shows
for a canary tag. The predicate reads only static inventory data
(`host.group`) and is a stable module-level function, so both phases reach the
same decision; the driver skips a rejected request without materializing it,
so on a non-Alpine host neither the `apk` commands nor the
`ApkInstalledPackages` fact behind them run, and plan and log render
`[skipped: when guard is_alpine]` in the request's lockstep slot. That skip
is visible on every run: the shared `inventory.json` holds one Alpine host
alongside hosts in other groups, so the run installs on the Alpine host and
skips every other one. The read-only fact examples guard differently by
necessity: `gather_fact` takes no
`when` parameter, so their predicate filters the host list before the
fan-out.

::: apk_packages
    options:
      members: []

## Managing a virtual package group

The virtual-package operation: `apk.virtual` creates (or with `present=False`
removes) an apk *virtual package* group — a reemote5 original with no pyinfra
counterpart. Alpine's `apk add -t <tag> <packages>` records a temporary virtual
package `<tag>` depending on `<packages>` in `/etc/apk/world`, so a build step
can pull in a toolchain and later tear the whole group down with a single
`apk del <tag>`. The operation reads the
[`ApkExplicitPackages`][reemote5.modules.apk.apk.ApkExplicitPackages] fact for
world-name idempotence — the group is present exactly when `<tag>` appears
as an entry name in the world — so re-running is a no-op. The script wraps a
compile in the classic
Alpine pattern: create a `.build-deps` group, then remove it, yielding each
request twice so the second create and the second remove are guaranteed no-ops
in the apply phase through the fresh per-step fact cache. Creating and removing
both write the root-owned apk database and world file, so the requests are
flagged `doas=True` and the driver elevates the commands — `doas` rather than
`sudo` because the Alpine host ships no `sudo` (a `sudo apk add -t` would
fail with exit 127 before reaching `apk`) and `doas` is passwordless, so the
host needs a `permit nopass` rule in `/etc/doas.conf`.

Like the other apk examples, this script reads the shared project-root
`inventory.json` (validated up front with `validate_inventory`) and every
request carries `when=is_alpine`, so on a non-Alpine host neither the `apk
add -t`/`apk del` commands nor the `ApkExplicitPackages` fact behind them
run — the driver skips each request without materializing it and plan and log
render `[skipped: when guard is_alpine]`. Since the shared inventory holds
one Alpine host among hosts in other groups, each run creates and removes the
group on that host and skips every other one.

::: apk_virtual
    options:
      members: []

## Holding packages back from upgrades

The hold operation: `apk.hold` pins packages back from upgrades — the apk
counterpart of [`apt.mark`](../../modules/apt.md), and a reemote5 original
(pyinfra ships no apk hold). apk has no `apt-mark` verb; it holds a package
by writing a version *constraint* into `/etc/apk/world`, so `present=True`
re-adds it with `apk add <pkg>=<version>` and `present=False` re-adds the
bare `apk add <pkg>`, rewriting the world line without a bound so the
package stays installed and only the pin goes. The operation reads the
[`ApkHeldPackages`][reemote5.modules.apk.apk.ApkHeldPackages] fact
(a list of `ApkPackageVersion` entries, looked up by `name` and compared on
the entry's `version` — the world constraint verbatim) for
version-aware idempotence — a name is held exactly
when its world constraint matches the wanted pin, so re-running is a no-op
and re-pinning to a *different* version is detected. With `version` omitted,
each name is pinned to its currently installed version (`ApkInstalledPackages`'
newest); pass `version` to pin to an explicit constraint (`1.2.3-r0`,
`~1.2`, `<2.0`) or to pin a package that is not yet installed. The script
holds a package twice — the second is a guaranteed no-op in the apply phase,
because the first pin is visible to it through the fresh per-step fact cache
(in the prepare phase, where nothing is sent, both plan as changes) — then
releases it, yielding the release twice for the same reason. Holding and
releasing both write the root-owned apk database and world file, so the
requests are flagged `doas=True` and the driver elevates the commands —
`doas` rather than `sudo` because the Alpine host ships no `sudo` (a
`sudo apk add` would fail with exit 127 before reaching `apk`) and `doas` is
passwordless, so the host needs a `permit nopass` rule in `/etc/doas.conf`.

Like the other apk examples, this script reads the shared project-root
`inventory.json` (validated up front with `validate_inventory`) and every
request carries `when=is_alpine`, so on a non-Alpine host neither the
`apk add` pin commands nor the `ApkHeldPackages` and `ApkInstalledPackages`
facts behind them run — the driver skips each request without materializing it
and plan and log render `[skipped: when guard is_alpine]`. Each run therefore
pins and releases on the inventory's single Alpine host and skips the hosts
in the other groups.

::: apk_hold
    options:
      members: []

## Removing orphan packages

The autoremove operation: `apk.autoremove` removes orphaned packages —
those apk pulled in as dependencies that nothing in `/etc/apk/world`
requires any more — a reemote5 original with no pyinfra counterpart, and
the apk sibling of [`apt.auto_remove`](../../modules/apt.md). apk has no
`autoremove` verb; it exposes orphan purge as the `--purge` flag of
`apk add`/`apk fix`, which reemote5 splits out into this standalone
operation so a deploy can clean up orphans without also performing an
install or a repair. The operation reads the
[`ApkAutoremovablePackages`][reemote5.modules.apk.apk.ApkAutoremovablePackages] fact
(`apk add --purge --simulate`, a pure simulation that never removes
anything): a non-empty list runs the real `apk add --purge` (the reason
names the packages, already sorted so both phases report identically), an
empty list is a no-op. The script yields the *same* request twice — the
second is a guaranteed no-op in the apply phase once the first cleanup
left nothing purgable, because the fresh per-step fact cache re-simulates
against the cleaned-up state (in the prepare phase, where nothing is sent,
both plan as changes). Purging writes to the root-owned apk database, so
the requests are flagged `doas=True` and the driver elevates the commands —
`doas` rather than `sudo` because the Alpine host ships no `sudo` (a
`sudo apk add --purge` would fail with exit 127 before reaching `apk`) and
`doas` is passwordless, so the host needs a `permit nopass` rule in
`/etc/doas.conf`; the read-only purge simulation stays unprivileged.
Pair this with `apk_packages.py`'s `apk.packages` (`present=False`) to
compose the remove-then-cleanup flow as two explicit, separately plannable
steps.

Like the other apk examples, this script reads the shared project-root
`inventory.json` (validated up front with `validate_inventory`) and every
request carries `when=is_alpine`, so on a non-Alpine host neither the
`apk add --purge` command nor the `ApkAutoremovablePackages` purge simulation behind
it runs — the driver skips each request without materializing it and plan and
log render `[skipped: when guard is_alpine]`. Each run therefore purges on
the inventory's single Alpine host and skips the hosts in the other groups.

::: apk_autoremove
    options:
      members: []
