# npm module

::: reemote.modules.npm.npm
    options:
      show_root_heading: false
      members:
        - NpmPackageInfo
        - NpmPackages
        - packages



<!---

`reemote.modules.npm.npm` holds the npm fact and the `npm.packages`
operation. `NpmPackages` reports the installed npm packages as
`NpmPackageInfo` entries — the package name exactly as npm spells it, scoped
names included, plus the `version` npm reports — parsed from the JSON
dependency tree of `npm ls -g --depth=0 --json`, or of
`! test -d <directory> || (cd <directory> && npm ls --depth=0 --json)` when a
project `directory` is named. It mirrors the baseline's `npm.NpmPackages`
fact but replaces its reader: the baseline scrapes the box-drawing tree
`npm list -g --depth=0` prints with
`^[└├]\─\─\s([a-zA-Z0-9\-]+)@([0-9\.]+)$`, a regex that cannot express a
scoped name (no `@` or `/` in its character class), a version outside its
`[0-9.]+` class (a prerelease such as `1.1.0-beta.4`, or the `latest` dist-tag
a global install can record), or a locale-dependent tree character. reemote
asks npm for `--json` and walks the top-level `dependencies` map instead —
the same probe the issue's own workaround used — so the parse keeps every
version shape npm prints, discards any warning line printed ahead of the
document, ignores an entry flagged `missing` (a declared dependency that is
not installed, which npm reports with only `required`) or without a string
version, and reads unparsable output as the empty list the baseline's
`default = dict` projects. Only depth 0 is reported because that is the tree
`npm install`/`npm uninstall` mutate: a transitive dependency is npm's own
business and must never make a request look already satisfied.

Two deliberate deviations beyond the parser. The baseline's `directory`
variant still ran `npm list -g` *inside* the directory, so the fact reported
the global tree while the local install mutated the project tree — the two
never described the same store; reemote drops that stray `-g`, the rule the
pip fact already follows. And the baseline's `requires_command` silently
answers an empty dict on a host without npm: reemote runs the command bare, so
a missing npm exits 127 and surfaces as `CommandNotFoundError` — an
environment without npm is an error the deployer hears about rather than a
mysteriously empty fact. The `! test -d ... ||` guard is kept, so a project
directory that does not exist yet answers `[]` instead of a non-zero exit,
which is the "nothing installed here yet" answer the operation needs in order
to install into it. npm also exits 1 when a tree has unmet dependencies;
`Host.run_shell` is called with `check=False`, so that status is ignored and
the valid JSON on stdout is still parsed. The answer is the sorted, unique
entry list every package-list fact here uses (the `ApkInstalledPackages`
precedent) rather than the baseline's dict, and — unlike pip's PEP 503
folding — npm names are compared verbatim, because they are case-sensitive on
the registry.

`npm.packages` is the operation counterpart of the baseline's `npm.packages`,
extended to the surface the Ansible `npm` module needs. It installs, upgrades
or removes packages given as bare names or `<pkg>=<version>` pins, globally or
inside one project directory, and can apply a `package.json` manifest. Its
idempotence reads `NpmPackages` once, so a pinned package is present only when
that *exact* version is installed (the `ensure_packages` rule `apt.packages`,
`apk.packages` and `pip.packages` also follow) and an unpinned one when the
name is installed at any version; no other semver range (`^1.2.0`, `~1.2`,
`>=17`) is interpreted — reemote carries no semver dependency — and such a
token degrades to whatever the `=` parser reads out of it. `latest=True`
follows the baseline's legacy behavior and updates every installed, unpinned
package with `npm update` (npm's own verb, not pip's `install --upgrade`, and
the one that respects a manifest's ranges) without checking that an update
exists; a pinned request is never an upgrade candidate. Removal is
version-aware like the baseline — a pinned request is uninstalled only when
that version is the installed one, so `packages=("git=1.1",)` against git 1.0
is a no-op — and contributes one token per package *name*, since npm drops the
whole package whatever selector rode with it.

The pin spelling is reemote's one user-facing format, `<pkg>=<version>`, shared
with apt/dnf/apk and translated for the shell by `_npm_package_spec` into
npm's own `<pkg>@<version>` (the `dnf` `pkg-version` translation precedent);
the baseline's `@` input spelling is therefore not accepted, and a scoped
`@scope/name=1.2.3` request correctly becomes `@scope/name@1.2.3` because the
split is at the *last* `=`.

Scope is one mutually-exclusive choice between `global_` and `directory`,
validated up front with `ValueError` because both are static request arguments
that cannot make the two phases disagree: a global install goes into npm's
prefix, not a project directory, and `global_=False` names the directory whose
manifest is managed. That flag is spelled with a trailing underscore because
`global` is a Python keyword. `prefix` and `unsafe_perm` render as the
`NPM_CONFIG_PREFIX` and `NPM_CONFIG_UNSAFE_PERM` command-environment
assignments Ansible's task sets — the two variables npm itself reads — sitting
directly before `npm` (after the `cd` in the project form, since an assignment
only covers the command it precedes), and the prefix also relocates the tree
the fact reads: so a package installed under the default prefix is never
mistaken for one installed under a relocated one. Ansible's third variable,
`NODE_PATH`, is deliberately out of scope: it steers Node's *runtime* module
resolution, not what `npm install` records as installed, so it changes neither
the fact's answer nor any command's effect; npm's `--userconfig` and `--cache`
knobs are dropped for the same reason.

`package_json` is the companion direction, Ansible's `npm: path=` form: npm
reads the manifest itself, so the command names no packages and no fact gates
it — it is stateless, yielded on every run and *before* any package command,
mirroring pip's `requirements` position. Resolving a manifest's semver ranges
is npm's own solver, which reemote does not reimplement, so convergence is
expressed by the scoped request that follows it in the deploy rather than by
the manifest command itself. Asking to *remove* a manifest raises
`ValueError`, because npm installs a `package.json` and has no
uninstall-from-manifest form — the combination is refused rather than silently
dropped.

The four commands render through the `NpmInstallCommand`/
`NpmUpdateCommand`/`NpmRemoveCommand`/`NpmPackageJsonCommand` descriptors in
the baseline's own verb order (`npm install -g <packages>`, `npm update -g`,
`npm uninstall -g`, and the `cd <directory> && npm <verb> <packages>` project
form), with the directory, the prefix and every package token
`shlex`-quoted — the baseline's own quoting fixture, a directory of
`/app dir; rm -rf /`, renders as `cd '/app dir; rm -rf /' && npm install
react` and never runs the injected command. Installing into a root-owned global
prefix writes system state, so those requests are flagged `sudo=True` (a
project directory owned by the login user is not); the fact read is always
unprivileged. All four baseline options map — `packages`, `present`, `latest`
and `directory` — with `latest` keeping the baseline's unconditional
`npm update`, and the baseline's implicit "no directory means global" default
becomes the explicit `global_=True` the issue asks for; `prefix`, `unsafe_perm`
and `package_json` are not baseline options at all but the Ansible-module
surface the namespace was added to cover.

--->
