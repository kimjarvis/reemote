# gem module

::: reemote.modules.gem.gem
    options:
      show_root_heading: false
      members:
        - GemPackageInfo
        - GemPackages
        - packages



<!---

`reemote.modules.gem.gem` holds the gem fact and the `gem.packages` operation.
`GemPackages` reports the installed RubyGems packages as `GemPackageInfo`
entries — the gem name exactly as `gem list` spells it plus one installed
`version` — parsed from the `name (versions)` rows of `gem list --local`, or of
`GEM_HOME="$(gem env user_gemhome)" GEM_PATH="$(gem env user_gemhome)" gem list
--local` when the user scope is named. It mirrors the baseline's
`gem.GemPackages` fact but widens its reader: the baseline parses each line with
`^([a-zA-Z0-9\-\+\_]+)\s\(([0-9\.]+)\)$`, a regex that matches only a *single*
bare-numeric version, so a multi-version row (`json (2.6.3, 2.6.1)`), a
platform-suffixed one (`nokogiri (1.16.5 x86_64-linux)`), a default-gem
annotation (`stringio (default: 3.0.1)`) and a name carrying a `.` all fall out
of the baseline's answer silently. reemote keeps every row gem prints: the
versions inside the parentheses are split on `,`, a leading `default:` or
trailing ` default` annotation is dropped (a default gem IS installed at that
version, which matters when an operation must not re-install it), and a platform
tag is dropped by taking the first whitespace token of each version. The
`*** LOCAL GEMS ***` header, blank lines and any warning row match nothing and
are skipped, and unparsable output reads as the empty list the baseline's
`default = dict` projects.

Two deliberate deviations beyond the parser. The baseline returns a
`dict[str, list[str]]`; reemote reports sorted, unique `GemPackageInfo` entries
— the deterministic, JSON-printable answer shape every package-list fact here
uses (the `NpmPackages` precedent) — widened to one entry per name/version pair
because gem genuinely keeps every installed version side by side, which is
exactly what the baseline's dict models. And the baseline's `requires_command`
silently yields its `default = dict` when gem is missing: reemote runs the
command bare, so a missing gem exits 127 and surfaces as `CommandNotFoundError`
— an environment without gem is an error the deployer hears about rather than a
mysteriously empty fact. Names are compared verbatim — RubyGems names are
case-sensitive — so, unlike pip's PEP 503 folding, there is no normalization to
share.

`user_install` cannot ride on the listing as a flag: `gem list` has no
`--user-install` option (`gem install`/`uninstall`/`update` do, `gem uninstall`
even defaults it on). The user store is therefore addressed the way gem itself
exposes it — `gem env user_gemhome` prints "the path where gems are installed
when --user-install is given" — pinned into both `GEM_HOME` and `GEM_PATH` so
the listing reads exactly the tree a `--user-install` command writes. An
explicit `gem_home`/`gem_path` override wins: it relocates the whole store, user
flag or not, and `gem_bin` names a non-default executable (an rbenv/rvm shim),
the `NPM_CONFIG_PREFIX`-style environment rendering npm uses.

`gem.packages` is the operation counterpart of the baseline's `gem.packages`,
extended to the surface the Ansible `gem` module needs (issue #57). It installs,
updates or removes gems given as bare names or `<pkg>=<version>` pins,
system-wide or into the invoking user's gem directory. Its idempotence reads
`GemPackages` once, so a pinned gem is present only when that *exact* version is
one of the installed ones (the `ensure_packages` rule `apt.packages`,
`apk.packages`, `npm.packages` and `pip.packages` also follow) and an unpinned
one when the name is installed at any version. `latest=True` follows the
baseline's legacy behavior and updates every installed, unpinned gem with
`gem update` (gem's own verb) without checking that an update exists; a pinned
request is never an upgrade candidate. Removal is version-aware like the
baseline — a pinned request is uninstalled only when that version is installed,
so `packages=("rake=13.2",)` against rake 13.0.6 is a no-op — and a bare name
removes *all* versions of that name, so it subsumes a pin on the same name.

The pin spelling is reemote's one user-facing format, `<pkg>=<version>`, shared
with apt/dnf/apk/npm and translated for the shell by `_gem_package_spec` into
gem's own `<pkg>:<version>` install token (the baseline's `version_join=":"`,
the `dnf` `pkg-version` translation precedent); a removal renders the version as
`gem uninstall --version <v>`, the only grammar uninstall understands, since
`gem uninstall` has no `name:version` token. The baseline's `:` input spelling
is therefore not accepted.

Scope is a single choice: `user_install` selects the invoking user's gem
directory (Ansible's `user_install`) while `gem_home`/`gem_path` relocate the
store through the `GEM_HOME`/`GEM_PATH` command-environment assignments gem
itself reads — so the operation refuses the ambiguous `user_install` +
`gem_home`/`gem_path` combination up front with `ValueError`, because both are
static request arguments that cannot make the two phases disagree (npm's
`global_`/`directory` validation precedent). `bindir` renders `--bindir <dir>`,
Ansible's `bindir`: the directory gem places the gems' executables in. The same
scope keys the fact read *and* every command, so the tree read is always the
tree written.

The three commands render through the `GemInstallCommand`/`GemUpdateCommand`/
`GemRemoveCommand` descriptors in the baseline's own verb order (`gem install`,
`gem update`, `gem uninstall`), with the executable, the bindir, the environment
overrides and every package token `shlex`-quoted. The scope flag is rendered in
*both* directions — `--user-install` when asked, `--no-user-install` otherwise —
so the tree written is always the one the fact read, never gem's own
writable-GEM_HOME heuristic. Removal is rendered non-interactively, a deliberate
deviation from the baseline's bare `gem uninstall <pkg>`, which stops at up to
three prompts an unattended deploy cannot answer: an unpinned removal carries
`-a` (all installed versions of each name), a pinned one `--version <v>`, and
both carry `-x` (drop the executables without asking) and `-I` (ignore gems that
depend on the removed one). Installing into the system store writes root-owned
state, so those requests are flagged `sudo=True` (a `user_install` request into
the login user's own directory is not); the fact read is always unprivileged. All
three baseline options map — `packages`, `present` and `latest` — with `latest`
keeping the baseline's unconditional `gem update`; `user_install`, `bindir` and
the `gem_home`/`gem_path`/`gem_bin` overrides are not baseline options at all but
the Ansible-module surface the namespace was added to cover.

--->
