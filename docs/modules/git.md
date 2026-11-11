# git module

::: reemote.modules.git.git
    options:
      show_root_heading: false
      members:
        - GitBranch
        - GitConfig
        - GitConfigItem
        - GitHeadDetached
        - GitIsRepo
        - GitLocalCommit
        - GitRemoteBranchCommit
        - GitRemoteUrl
        - GitSshKeyConfigured
        - GitStatus
        - GitStatusSummary
        - GitSubmodule
        - GitSubmoduleStatus
        - GitTag
        - GitTagInfo
        - GitTrackingBranch
        - config
        - repo
        - worktree




<!---

`reemote.modules.git.git` holds the git facts — `GitBranch`, the branch a
repository is on, read from `git describe --all`, `GitConfig`, the
configuration items of a repository's local config or of the login user's
global/system config, as `GitConfigItem` entries, read from `git config -l`,
`GitHeadDetached`, whether
the checked-out HEAD is detached from any branch, read from
`git symbolic-ref -q HEAD`, `GitIsRepo`, whether a directory is a valid git
work tree, read from `git rev-parse --is-inside-work-tree`, `GitLocalCommit`,
the SHA a local ref resolves to, read from `git rev-parse`,
`GitRemoteBranchCommit`, the tip SHA of a branch on a remote, read from
`git ls-remote`, `GitRemoteUrl`, a remote's fetch or push URL, read from
`git remote get-url`, `GitSshKeyConfigured`, whether the login user's SSH key
authenticates to a git remote host, read from `ssh -T git@<host>`,
`GitStatus`, the working tree's staged, unstaged and untracked changes, read
from `git status --porcelain`, `GitSubmodule`, the repository's
submodules and their sync state, as `GitSubmoduleStatus` entries, read from
`git submodule status`, `GitTag`,
the repository's tags as `GitTagInfo` entries, read from `git tag`, and
`GitTrackingBranch`, the
upstream tracking branch of the checked-out branch, read from the
`git status --branch --porcelain` header. Every fact but `GitSshKeyConfigured`
— which probes an SSH remote host rather than a repository — takes the
repository path it reads, and each repo-scoped command carries the baseline's
`! test -d <repo> ||` guard, so a missing repository directory
short-circuits to the fact's empty answer (`""`, `[]`, `False` or
`None`) instead of failing the read. The repo path — and every other user-supplied
operand — is `shlex`-quoted, a deliberate deviation from the baseline, which
interpolates them raw. Alongside the facts the namespace holds operations
that change git state; `git.config`, documented under
[Operations](#operations) below, manages a configuration item at the
repository, user or system level, and `git.repo` clones or updates a
repository at a directory, reconciling `origin`, switching branches or
checking out a tag and pulling, while `git.worktree` manages a linked working
tree — creating it from a repository, pulling it, or removing it.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a generator —
implemented as a `process` coroutine returning the raw value (over asyncssh in
the real implementation). Read facts through
[`FactCache.get`][reemote.core.FactCache.get], which is itself a coroutine,
so results are awaited and cached within a phase step. `GitBranch` takes the
repository path: it runs
`! test -d <repo> || (cd <repo> && git describe --all)` and strips the
`heads/`/`tags/` prefix from the answer, mirroring the baseline's `git.GitBranch`,
so a checkout of `develop` reads as `"develop"`. A missing repository
directory short-circuits the guard to no output, which reads as `""`. With
the directory present, a missing `git` exits 127 — reported as
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError] (the baseline's
`requires_command` gating is deliberately dropped).

`GitConfig` takes an optional repository path and a `system` flag: without a
`repo` it runs `git config --global -l || true` (or `--system` with
`system=True`), with a `repo` it runs the `--local` read behind the
directory guard, mirroring the baseline's `git.GitConfig`. Each `key=value` row
appends its value to the key's list — the split is on the first `=`, so a
value may itself contain `=` — and the rows fold into one `GitConfigItem`
entry per key, in first-appearance order, where a multi-valued key
(`safe.directory`) keeps every value in its `values` list, for example
`[{"key": "user.email", "values": ["nick@gmail.com"]}]`. Rows without an
`=` are skipped
(the baseline would raise `ValueError` on them; reemote hardens the parser).
The `|| true` guard maps a missing git or config file to `[]` (the baseline's
`default = dict`, projected onto entries), as does a missing repository
directory.

`GitHeadDetached` takes the repository path: it runs a reemote-original
probe with no baseline equivalent,
`! test -d <repo> || (cd <repo> && if git symbolic-ref -q HEAD >/dev/null 2>&1; then echo attached; elif git rev-parse --git-dir >/dev/null 2>&1; then echo detached; fi)`.
`git symbolic-ref -q HEAD` succeeds only when HEAD names a branch, so an
attached HEAD reads as `False` and a detached one — the state a tag or
bare-commit checkout leaves, where a later `git pull` would fail — reads as
`True`. A missing repository directory, or a non-repo where neither probe
fires, reads as `None`, so an operation can tell a detached HEAD from an
undetermined one rather than guessing.

`GitIsRepo` takes the repository path: it runs
`! test -d <repo> || (cd <repo> && git rev-parse --is-inside-work-tree 2>/dev/null)`,
a reemote-original fact, and answers `True` only when git prints `true`. It
validates a work tree precisely rather than trusting the existence of a
`.git` directory, which a corrupt or half-cloned repository also has. A bare
repository prints `false`; a missing directory or a non-repo prints nothing
(the `2>/dev/null` swallows git's `fatal:`); all read as `False`.

`GitLocalCommit` takes the repository path and an optional `ref` (default
`HEAD`): it runs
`! test -d <repo> || (cd <repo> && git rev-parse <ref> 2>/dev/null) || true`
and keeps the first line only when it fullmatches a 40-character (SHA-1) or
64-character (SHA-256) lowercase hex digest, mirroring the baseline's
`git.GitLocalCommit`. A missing repository, an unknown ref or any non-SHA
answer reads as `None`.

`GitRemoteBranchCommit` takes the repository path, a `remote` (default
`origin`) and an optional `branch`: it runs
`! test -d <repo> || (cd <repo> && git ls-remote <remote> <branch> 2>/dev/null | head -n1)`
and keeps the first row's first tab-separated column under the same SHA
fullmatch, mirroring the baseline's `git.GitRemoteBranchCommit`. A falsy
`branch` asks for the remote's `HEAD`, exactly like the baseline's truthiness
check. A missing repository, an unreachable remote or an unknown branch
reads as `None`.

`GitRemoteUrl` takes the repository path, a `remote` (default `origin`) and a
`push` flag: it runs
`! test -d <repo> || (cd <repo> && git remote get-url [--push] <remote> 2>/dev/null)`,
a reemote-original fact, and keeps the first non-blank line. An operation
can confirm a clone still points at the expected URL — detecting a migration
— instead of pulling blindly; `push=True` reads the push URL, which may
differ from the fetch one. An unknown remote or a missing repository prints
nothing and reads as `None`.

`GitSshKeyConfigured` is the one git fact that is not repo-scoped: it takes a
`remote_host`, a `user` (default `git`) and a `timeout`, and runs a batched,
non-interactive `ssh -T <user>@<remote_host>` probe that normalizes ssh's
exit status to a single `authenticated`/`unauthenticated` token. It answers
`True` when the login user's SSH key can authenticate to the host, letting a
deployment verify connectivity before a clone or pull would fail on
authentication. Only ssh's exit 255 (a connection or auth failure) counts as
unauthenticated — a successful `ssh -T git@<host>` handshake exits non-zero
by design — while a missing `ssh` exits 127 and surfaces as
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError].

`GitStatus` takes the repository path: it runs
`! test -d <repo> || (cd <repo> && git status --porcelain)`, a
reemote-original fact, and classifies each row into a `GitStatusSummary` —
a `dirty` flag plus the `staged`, `unstaged` and `untracked` path lists —
from the two-column porcelain status prefix, where `??` marks an untracked
path. It gives an operation the working-tree state it needs to refuse a pull
or checkout that would clobber local changes. A missing repository directory
reads as a clean, empty summary.

`GitSubmodule` takes the repository path: it runs
`! test -d <repo> || (cd <repo> && git submodule status)`, a reemote-original
fact, and returns one `GitSubmoduleStatus` entry per line — the `path`, the
recorded `sha`, the optional `describe` and a `state` mapped from the leading
status character (a space for synced, `-` uninitialized, `+` modified, `U`
conflicted). An operation can then update only the submodules that are
actually out of sync, sparing needless network fetches. A missing repository
directory, or a repo without submodules, reads as `[]`.

`GitTag` takes the repository path: it runs
`! test -d <repo> || (cd <repo> && git tag)` and returns one `GitTagInfo`
entry (a `name`) per line, in listing order, mirroring the baseline's `git.GitTag`
— for example `[{"name": "v1.0.0"}, {"name": "v1.0.1"}]`. A missing
repository directory reads as `[]`, as does a tagless repository.

`GitTrackingBranch` takes the repository path: it runs
`! test -d <repo> || (cd <repo> && git status --branch --porcelain)` and
searches the first output line — the `## <branch>...<upstream>` header — for
the token after the `...` separator, mirroring the baseline's
`git.GitTrackingBranch`, for example `"origin/master"`. A branch without an
upstream carries no separator and reads as `None`, as does a missing
repository directory; the trailing `[ahead N, behind M]` detail never
reaches the match.


## Operations

An operation is an async generator: it reads facts through
[`FactCache.get`][reemote.core.FactCache.get], decides whether a change is
needed, records `ctx.changed`/`ctx.reason`, and yields one typed command
descriptor per mutation — never touching the host itself. Reach operations
through the namespace (`git.config`), not the top-level package.

`git.config` manages a git configuration item at the repository, user or
system level (the baseline's `git.config`). It takes the `key` to ensure and the
`value` it should hold; `multi_value=True` adds the value with `--add`
instead of setting it, for keys that carry several values; `repo` edits that
repository's `--local` config (defaulting to the login user's `--global`
config when unset); and `system`, with no `repo`, works at the `--system`
level. It is idempotent: a single-valued key is written only when its current
value list is not exactly `[value]`, and a `multi_value` key only when
`value` is absent from the current list; otherwise it is a no-op reporting
`git config <key> is set to <value>`. For a `repo` scope the baseline reads the
existing config only when `repo/.git` is a directory, so `git.config` probes
the git-local `_DirectoryExists` fact first and reads `GitConfig` only behind
that gate — when the repository directory is absent the existing config reads
as `[]` and the write still runs, exactly as the baseline does. Setting a
repository's `--local` config or the `--system` config often needs root or the
repository owner, so flag the request `sudo=True` when the login user cannot
write there. The command it yields (`GitConfigCommand`) `shlex`-quotes the
key, value and repo path — a deliberate deviation from the baseline, which wraps
the value in literal double quotes — so a value carrying shell metacharacters
is read literally rather than double-quote-expanded. Like `apt.ppa`, this
operation deliberately stays outside the spool-and-install family: its
payload is a CLI invocation, not the file's text — `git config` owns the
config file, resolving which file a scope names (`~/.gitconfig` or the XDG
path for `--global`, a build-dependent path for `--system`, the gitdir's
`config` for `--local`), parsing git's INI subsections and
`include`/`includeIf` directives, merging multi-valued keys and writing
through a lockfile, none of which a hand-rolled content transform can
reproduce.


`git.repo` clones a new repository or updates an existing one (the baseline's
`git.repo`). `src` is the URL to clone from or keep `origin` pointed at and
`dest` is the directory it lives in; `branch` names the branch or tag to
check out; `pull` (default `True`) toggles the update pull and `rebase`
passes `--rebase` to it; `depth` truncates clone, fetch and pull history to
`N` commits; `fetch_tags` adds `--tags` to the fetch; `force` adds `--force`
to fetch, checkout and pull; and `update_submodules` runs
`git submodule update --init` at the end, with `recursive_submodules`
appending `--recursive`. The operation branches on whether `dest/.git` is a
directory (read through the git-local `_DirectoryExists` fact) so it is
idempotent in both directions. When the repository is **absent** it yields
one clone command carrying `branch` and `depth`. When the repository **exists**
it reconciles `origin` against `src` — reading `GitConfig`'s
`remote.origin.url` key, the baseline's GH #1763 check — and yields one
`git remote set-url origin <src>` command only when that URL exists and
differs from `src`; then, when `branch` is given and differs from
`GitBranch`, it yields one `git fetch` command (carrying
`depth`/`fetch_tags`/`force`) followed by one `git checkout <branch>` (with
`force`) so the target is present locally before the switch; when `branch`
names a tag (`GitTag`) it yields a second checkout that detaches HEAD onto
the tag and skips the pull; finally, when `pull` is set and the checkout is
not a tag, it compares `GitLocalCommit` against `GitRemoteBranchCommit` for
the effective branch (the baseline's `skip_pull`) and yields one `git pull` (with
`depth`/`rebase`/`force`) only when the two differ or cannot be resolved. A
remote reset always pulls, because the cached remote tip was gathered against
the old origin. `update_submodules` is independent and always appends its
submodule command. Cloning, fetching, checking out, pulling and updating
submodules all write under `dest`, so flag the request `sudo=True` when the
login user cannot write there. The commands it yields
(`GitCloneCommand`, `GitRemoteSetUrlCommand`, `GitFetchCommand`,
`GitCheckoutCommand`, `GitRepoPullCommand` and `GitSubmoduleUpdateCommand`)
`shlex`-quote every operand — a deliberate deviation from the baseline's raw
`QuoteString` interpolation — and the baseline's own fixtures still render
identically for every git verb. Three the baseline behaviours are deliberately
dropped: the `user`/`group` options and their final `chown -R` command (plus
the `safe.directory` `git config` prefix that only ever accompanies them),
the `ssh_keyscan` preflight (no `ssh` namespace exists to delegate to), and
the leading `files.directory` ensure-directory step — which is why
`GitCloneCommand` names `dest` itself instead of cloning into `.` from inside
a pre-created directory.


`git.worktree` manages a linked git working tree (the baseline's `git.worktree`):
the `worktree` directory is what it creates, pulls or removes, and `repo`
names the main repository a new tree is added from. The operation branches on
the tree's existence — read through the git-local `_DirectoryExists` fact —
so it is idempotent in three directions. When the tree is **absent** and
wanted (`present=True`) it validates the source repository (`repo` is
required, else `ValueError`; unless `assume_repo_exists`, `repo/.git` must be
a directory, else `OperationError`) and yields one add command, carrying the
`new_branch` (`-b`), `detached` (`--detach`), `force` (`--force`) and
`commitish` options. When the tree is **present** but unwanted
(`present=False`) it yields one remove command (`force` appends `--force`).
When the tree is **present and wanted** it pulls it: `pull=False` is a no-op;
a tree with neither a tracking branch (`GitTrackingBranch`) nor an explicit
`from_remote_branch` reports it is not linked to one; and a tree whose local
`GitLocalCommit` already equals the remote tip (`GitRemoteBranchCommit`)
short-circuits the pull (the baseline's `skip_pull`); otherwise it yields one pull
command, with `rebase` inserting `--rebase` and `from_remote_branch` — a
`(remote, branch)` 2-tuple — naming the source. Creating or removing a
worktree writes under the repository's `.git`, often root-owned, so flag the
request `sudo=True` when the login user cannot write there. The commands it
yields (`GitWorktreeAddCommand`, `GitWorktreeRemoveCommand` and
`GitPullCommand`) `shlex`-quote every operand — a deliberate deviation from
the baseline's raw `QuoteString` interpolation — and a tracking-driven pull renders
a bare `git pull`, naming the remote and branch only for the explicit
`from_remote_branch` form. The baseline's `user`/`group` options and the up-to-four
`chown` commands they emit are deliberately dropped: reemote has no `chown`
primitive and routes ownership through the inventory `privilege` plus the
`sudo`/`su` meta-parameters.

## Example

The read-only fact-gathering scripts `git_git_branch.py`,
`git_git_config.py`, `git_git_head_detached.py`, `git_git_is_repo.py`,
`git_git_local_commit.py`, `git_git_remote_branch_commit.py`,
`git_git_remote_url.py`, `git_git_ssh_key_configured.py`,
`git_git_status.py`, `git_git_submodule.py`, `git_git_tag.py` and
`git_git_tracking_branch.py` under
[`examples/modules/git/`](../examples/modules/git.md) gather `GitBranch`,
`GitConfig`, `GitHeadDetached`, `GitIsRepo`, `GitLocalCommit`,
`GitRemoteBranchCommit`, `GitRemoteUrl`, `GitSshKeyConfigured`, `GitStatus`,
`GitSubmodule`, `GitTag` and `GitTrackingBranch` respectively across an
inventory with [`gather_fact`][reemote.drivers.gather_fact] and print each
host's result as JSON.

The `git_config.py` operation script under
[`examples/modules/git/`](../examples/modules/git.md) drives `git.config`
through the two-phase [`run`][reemote.drivers.run] — a dry-run plan then a
real apply — over an inventory, yielding the same request twice so the second
is a guaranteed apply-phase no-op.

The `git_repo.py` operation script under
[`examples/modules/git/`](../examples/modules/git.md) drives `git.repo`
through the same two-phase [`run`][reemote.drivers.run], cloning a
repository into a fresh directory and yielding the request twice so the
second pass sees `dest/.git` present, `origin` reconciled and the branch
already checked out, and is a guaranteed apply-phase no-op.

The `git_worktree.py` operation script under
[`examples/modules/git/`](../examples/modules/git.md) drives `git.worktree`
through the same two-phase [`run`][reemote.drivers.run], ensuring a linked
working tree checked out on a new branch from a repository and yielding the
request twice so the second, seeing the tree already present, is a guaranteed
apply-phase no-op.

--->
