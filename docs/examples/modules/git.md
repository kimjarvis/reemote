# Module examples: git

Scripts under `examples/modules/git/` — one per fact or operation of the
[`git`](../../modules/git.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`git_git_branch.py` reading
`GitBranch`, `git_git_config.py` reading `GitConfig`,
`git_git_head_detached.py` reading `GitHeadDetached`, `git_git_is_repo.py`
reading `GitIsRepo`, `git_git_local_commit.py` reading `GitLocalCommit`,
`git_git_remote_branch_commit.py` reading `GitRemoteBranchCommit`,
`git_git_remote_url.py` reading `GitRemoteUrl`,
`git_git_ssh_key_configured.py` reading `GitSshKeyConfigured`,
`git_git_status.py` reading `GitStatus`, `git_git_submodule.py`
reading `GitSubmodule`, `git_git_tag.py` reading `GitTag`, and
`git_git_tracking_branch.py` reading `GitTrackingBranch`, each over
`gather_fact`) are not rendered here — they read a single fact, and the
facts themselves are documented on the
[`git` module page](../../modules/git.md).

## Managing a git config item

The configuration operation: `git.config` mirrors the baseline's `git.config`,
ensuring a git configuration item at the repository, user or system level.
It reads the [`GitConfig`][reemote5.modules.git.git.GitConfig] fact for the
current value list of the key, so a single-valued key is written only when
its list is not exactly `[value]` and a `multi_value` key only when `value`
is absent from the list — otherwise the request is a no-op reporting
`git config <key> is set to <value>`. For a `repo` scope the baseline reads the
existing config only when `repo/.git` is a directory, so the operation probes
that gate first; with no `repo` it targets the login user's `--global` (or,
with `system=True`, `--system`) config. Setting a repository's `--local`
config or the `--system` config often needs root or the repository owner, so
the request can ride under `sudo`. Because the demo hosts ship without
`git` and the `GitConfig` fact itself shells out to the binary, `main` first
drives a small idempotent install deploy (`dnf.packages` under `sudo=True`)
before the config deploy, so the fact read in prepare finds the binary. The
script yields the same request twice —
the second is a guaranteed no-op in the apply phase, because the first write
is visible to it through the fresh per-step fact cache (in the prepare phase,
where nothing is sent, both plan as changes).

::: git_config
    options:
      members: []

## Cloning or updating a repository

The repository operation: `git.repo` mirrors the baseline's `git.repo`, cloning a
fresh repository or reconciling an existing one. It branches on whether
`dest/.git` is a directory (read through a git-local directory probe), so an
absent repository yields one `git clone` command carrying `branch` and
`depth`, while an existing one reconciles `origin` against `src` (writing
`git remote set-url origin <src>` only when the current
[`GitConfig`][reemote5.modules.git.git.GitConfig] URL differs), switches
branch when the requested one differs from
[`GitBranch`][reemote5.modules.git.git.GitBranch] (a `git fetch` followed by
a `git checkout`) and — when `branch` names a tag
([`GitTag`][reemote5.modules.git.git.GitTag]) — detaches HEAD onto it and
skips the pull. Otherwise `pull` (default `True`) compares
[`GitLocalCommit`][reemote5.modules.git.git.GitLocalCommit] against
[`GitRemoteBranchCommit`][reemote5.modules.git.git.GitRemoteBranchCommit] and
issues one `git pull` only when the two differ (the baseline's `skip_pull`); a
remote reset always pulls because the cached remote tip was gathered against
the old origin. `depth`, `rebase`, `fetch_tags` and `force` land on the verbs
they apply to, and `update_submodules` (with `recursive_submodules`) always
appends `git submodule update --init` at the end. Every command writes under
`dest`, so the request can ride under `sudo` when the login user cannot write
there. The baseline's `user`/`group` options, its `ssh_keyscan` preflight and its
leading `files.directory` ensure-directory step are deliberately dropped; the
clone therefore names `dest` itself instead of cloning into `.` from inside a
pre-created directory. The script clones the public
`https://github.com/octocat/Hello-World.git` demo repository (branch
`master`) over HTTPS, so no deploy key is needed on the targets, and `main`
first drives a small idempotent `git` install deploy (`dnf.packages` under
`sudo=True`) because the demo hosts ship without the binary. It yields the
same clone request twice with
`pull=False` — the second is a guaranteed no-op in the apply phase, because
the first clone is visible to it through the fresh per-step fact cache (in
the prepare phase, where nothing is sent, both plan as changes).

::: git_repo
    options:
      members: []

## Managing a linked git worktree

The worktree operation: `git.worktree` mirrors the baseline's `git.worktree`,
managing a linked working tree — creating it from a main repository, pulling
it, or removing it with `present=False`. It branches on the tree's existence
(read through a git-local directory probe), so an absent but wanted tree
yields one `git worktree add` command (carrying the `new_branch`/`detached`,
`force` and `commitish` options) while an already-present tree is left alone.
When present and wanted, it pulls the tree unless `pull=False`; a tree with
neither a tracking branch nor an explicit `from_remote_branch`, or one already
at its remote tip (the baseline's `skip_pull`), is a no-op. `assume_repo_exists`
skips the `repo/.git` validity probe, and creating or removing a tree writes
under the repository's `.git`, so the request can ride under `sudo` when the
login user cannot write there. The demo hosts ship neither `git` nor the
main repository, so `main` first drives a small idempotent `git` install
deploy (`dnf.packages` under `sudo=True`), and the deploy opens with a
`git.repo` request cloning the public
`https://github.com/octocat/Hello-World.git` demo repository into
`/tmp/reemote5-app` before the worktree requests. The script yields the same
add request twice with `pull=False` — the second is a guaranteed no-op in the
apply phase,
because the first add is visible to it through the fresh per-step fact cache
(in the prepare phase, where nothing is sent, both plan as changes).

::: git_worktree
    options:
      members: []
