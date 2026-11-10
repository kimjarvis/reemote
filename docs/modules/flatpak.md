# flatpak module

::: reemote5.modules.flatpak.flatpak
    options:
      show_root_heading: false
      members:
        - FlatpakPackage
        - FlatpakPackageInfo
        - FlatpakPackages
        - packages



<!---

`reemote5.modules.flatpak.flatpak` holds the flatpak facts and the
`flatpak.packages` operation. `FlatpakPackage` reads one installed flatpak
package's identity — its reverse-domain application `id`, its full install
`ref` and its installed `version` — from the indented label rows of
`flatpak info <package>`, and `FlatpakPackages` lists the bare
`flatpak list --columns=application` IDs of every installed flatpak; both
mirror the baseline's `flatpak` facts and wrap their commands in
`2>/dev/null || true` so a host without `flatpak` reads as absent rather than
raising `CommandNotFoundError` (the baseline's `requires_command` gating is
deliberately dropped). Each of the baseline's three tracked regexes anchors on the
row's leading indentation, so the unindented title line, the blank separators
and every other label (`Arch`, `Branch`, `License`, `Origin`, `Installation`,
`Installed`, `Runtime`, `Sdk` and the commit block) never match.
`FlatpakPackage`'s entry is typed as the exported `FlatpakPackageInfo`
TypedDict, whose three keys are the baseline's verbatim `id`/`ref`/`version`; a
present package that prints no `Version:` row (a runtime) keeps that key with
its empty-string default, so the entry always carries all three fields. An
absent package reads as `None` (the `apt.DebPackage`/`snap.SnapPackage`
None-on-absent convention) rather than the baseline's `default = dict` empty `{}`.
`FlatpakPackages` keeps the baseline's bare `list[str]` scalar-list shape — the
answer is a list of application IDs, not record dicts, so there is no field to
wrap in an entry type.

`flatpak.packages` is the operation counterpart of the baseline's
`flatpak.packages`: it installs or removes the named applications, optionally
from a `remote`. Its idempotence reads `FlatpakPackages` once, so an ID absent
from the installed list under `present=True` is queued for install and one
present under `present=False` for removal, each rendered through the
`FlatpakInstallCommand`/`FlatpakRemoveCommand` descriptors in the baseline's own
verb order (`flatpak install --noninteractive [remote] <packages>`, then
`flatpak uninstall --noninteractive <packages>`). flatpak application IDs carry
no version to compare, so presence is a bare name-membership test. Installing
and removing both write system state, so the request is flagged `sudo=True`;
the fact read is unprivileged.

--->
