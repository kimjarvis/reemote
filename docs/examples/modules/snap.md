# Module examples: snap

Scripts under `examples/modules/snap/` — one per fact or operation of the
[`snap`](../../modules/snap.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`snap_snap_package.py` reading
`SnapPackage` for one package over `gather_fact`, and `snap_snap_packages.py`
reading `SnapPackages` for the whole installed name list, both guarded to the
`ubuntu` group) are not rendered here — they read single facts, and the facts
themselves are documented on the [`snap` module page](../../modules/snap.md).

## Installing snap packages

The package operation: `snap.package` mirrors the baseline's `snap.package`,
installing, removing or re-channeling snaps by name. It reads the
[`SnapPackage`][reemote.modules.snap.snap.SnapPackage] fact once per
requested name, so `None` reads as not installed (`present=True` installs it,
`present=False` on an absent name is a no-op), while an installed snap tracked
on a different `channel` yields a `snap refresh --channel=` switch — snap has
no separate `switch` verb. An installed snap already on the requested channel
is a no-op, so re-running converges. `channel` defaults to `latest/stable` and
`classic=True` adds snap's `--classic` confinement flag to installs only.
the baseline's `requires_command` gating on `snap` is deliberately dropped, matching
the fact. Because installing a snap writes system state, the requests ride
under `sudo`, and the script yields the same install request twice — the second
is a guaranteed no-op in the apply phase, because the first install is visible
to it through the fresh per-step fact cache (in the prepare phase, where nothing
is sent, both plan as changes). Every request carries `when=is_ubuntu`, so on
the shared inventory's non-Ubuntu hosts the step renders
`[skipped: when guard is_ubuntu]` and neither its commands nor the fact behind
them run. As the operation mutates shared system state on the demo host, this
example ships verified (compile-checked and exercised end to end against
`SimulatedHost` in the pytest suite), not by being run against that shared
inventory.

::: snap_package
    options:
      members: []
