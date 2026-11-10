# snap module

::: reemote5.modules.snap.snap
    options:
      show_root_heading: false
      members:
        - SnapPackage
        - SnapPackageInfo
        - SnapPackages
        - package



<!---

`reemote5.modules.snap.snap` holds the snap facts and the `snap.package`
operation. `SnapPackage` reads one installed snap package's identity (its
name, publisher, store id, tracked channel and installed version) from the
labelled field rows of `snap info <package>`, and `SnapPackages` lists the
bare `snap list` names of every installed snap — both mirroring the baseline's
`snap` facts and wrapping their commands in `2>/dev/null || true` so a host
without `snap` reads as absent rather than raising
`CommandNotFoundError` (the baseline's `requires_command` gating is deliberately
dropped). `SnapPackage`'s entry is typed as the exported `SnapPackageInfo`
TypedDict, which renames the baseline's hyphenated `snap-id` key onto the valid
`snap_id` identifier — the same label-to-key normalization `ApkPackageInfo`
makes of apk's headers. An absent package reads as `None` (the
`apt.DebPackage`/`dnf.RpmPackage` None-on-absent convention) rather than
the baseline's `default = dict` empty `{}`.

`snap.package` is the operation counterpart of the baseline's `snap.package`: it
installs, removes, or re-channels the named snaps. Its idempotence reads
`SnapPackage` once per requested name — `None` means not installed (install,
or nothing under `present=False`), while an installed snap tracked on a
different `channel` yields a channel switch (snap has no separate `switch`
verb, so `snap refresh --channel=` moves it). Requests install, then remove,
then refresh, in the baseline's order, each rendered through the
`SnapInstallCommand`/`SnapRemoveCommand`/`SnapRefreshCommand` descriptors.
`classic` adds snap's `--classic` confinement flag to installs only; the
`channel` defaults to `latest/stable`. Installing, removing and refreshing
all write system state, so the request is flagged `sudo=True`; the
per-package fact read is unprivileged. Unlike the baseline's two-fact design,
existence is derived from `SnapPackage`'s None-on-absent answer rather than
a separate `snap list` membership check, so one read serves both questions
for the handful of names a request names.

--->
