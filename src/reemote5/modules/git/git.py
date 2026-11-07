# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The ``git`` module: the ``GitBranch``, ``GitConfig``, ``GitHeadDetached``,
# ``GitIsRepo``, ``GitLocalCommit``, ``GitRemoteBranchCommit``, ``GitRemoteUrl``,
# ``GitSshKeyConfigured``, ``GitStatus``, ``GitSubmodule``, ``GitTag`` and
# ``GitTrackingBranch`` facts plus the ``config``, ``repo`` and ``worktree``
# operations and their typed command descriptors.
#
# Fact side: ``GitBranch`` (the branch or tag ``git describe --all`` reports
# for a repository), ``GitConfig`` (the configuration items of a repository's
# local config, or of the login user's global/system config, as
# ``GitConfigItem`` entries),
# ``GitHeadDetached`` (whether ``HEAD`` is detached on a commit rather than a
# branch, a tri-state), ``GitIsRepo`` (whether a directory is a valid git work
# tree), ``GitLocalCommit`` (the SHA a local ref resolves to),
# ``GitRemoteBranchCommit`` (the tip SHA of a branch on a remote, via
# ``git ls-remote``), ``GitRemoteUrl`` (a remote's fetch or push URL),
# ``GitSshKeyConfigured`` (whether ``ssh -T`` authenticates to a git remote
# host), ``GitStatus`` (the working tree's staged, unstaged and untracked
# changes), ``GitSubmodule`` (each submodule's path, SHA and sync state, as
# ``GitSubmoduleStatus`` entries), ``GitTag`` (the repository's tags, as
# ``GitTagInfo`` entries) and ``GitTrackingBranch`` (the
# upstream tracking branch of the checked-out branch). ``GitHeadDetached``,
# ``GitIsRepo``, ``GitRemoteUrl``, ``GitSshKeyConfigured``, ``GitStatus`` and
# ``GitSubmodule`` are reemote5-original -- pyinfra has no counterpart --
# and back the safe-pull, idempotency and pre-flight checks the migrated facts
# cannot. Every fact but ``GitSshKeyConfigured`` takes the repository path it
# reads, and each repo-scoped command carries pyinfra's ``! test -d <repo> ||``
# guard, so a missing repository directory short-circuits to no output instead
# of failing the read. Like every fact they are phase-unaware: the ``process``
# coroutine just gathers the raw value.
#
# Operation side: ``git.config`` manages a git configuration item at the
# repository, user or system level, comparing against ``GitConfig`` and yielding
# the ``GitConfigCommand`` descriptor only on a difference; ``git.repo``
# clones or updates a repository -- reconciling ``origin`` against ``src``
# through ``GitConfig``/``GitBranch``/``GitTag``/``GitLocalCommit``/
# ``GitRemoteBranchCommit`` and yielding the
# ``GitCloneCommand``/``GitRemoteSetUrlCommand``/``GitFetchCommand``/
# ``GitCheckoutCommand``/``GitRepoPullCommand``/``GitSubmoduleUpdateCommand``
# descriptors; ``git.worktree``
# manages git working trees -- adding one from a source repository, pulling an
# existing one, or removing it -- and yields the
# ``GitWorktreeAddCommand``/``GitWorktreeRemoveCommand``/``GitPullCommand``
# descriptors. All three read the in-namespace git facts plus a private
# ``_DirectoryExists`` stand-in for ``files.Directory`` (the
# zero-cross-module-import invariant forbids reaching into the ``files``
# namespace, so the existence check lives here -- the ``apt.sources_file``
# pattern): ``git.worktree`` gates on the worktree and the source ``.git``,
# ``git.repo`` gates its clone/update branch on ``dest/.git``, and
# ``git.config`` gates a repo-scoped write on ``repo/.git``, exactly as
# pyinfra gates it on ``files.Directory``.
# """

import posixpath
import re
import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from typing import Any, TypedDict

from ...commands import Command
from ...core import OpContext, OperationError
from ...hosts import Host

# pyinfra's SHA matcher, verbatim: a full 40-character (SHA-1) or
# 64-character (SHA-256) lowercase hex digest. ``GitLocalCommit`` and
# ``GitRemoteBranchCommit`` keep an answer only when it fullmatches, so an
# error message or a truncated digest never passes as a commit SHA.
_SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

# The ref prefixes ``git describe --all`` prints, which ``GitBranch``
# strips (pyinfra's ``re.sub(r"(heads|tags)/", "", ...)``): a branch
# answers ``heads/<name>``, a tagged commit ``tags/<name>``.
_DESCRIBE_PREFIX_RE = re.compile(r"(heads|tags)/")

# The upstream separator in ``git status --branch --porcelain``'s header
# row (``## <branch>...<upstream> [<ahead/behind>...]``): the token after
# the ``...`` is ``GitTrackingBranch``'s answer, and ``\S+`` stops at the
# first space so the trailing status detail never reaches the match.
_TRACKING_RE = re.compile(r"\.{3}(\S+)\b")

# The ``git submodule status`` leading-character states, which
# ``GitSubmodule`` maps each row's prefix through: a space is an
# initialized submodule sitting at the recorded commit, ``-`` is one that is
# not initialized, ``+`` is a checked-out commit that differs from the
# recorded one, and ``U`` is a merge conflict. Any other prefix (a future git
# state) reads as ``unknown`` rather than raising.
_SUBMODULE_STATES = {
    " ": "synced",
    "-": "uninitialized",
    "+": "modified",
    "U": "conflicted",
}


# --- Facts -------------------------------------------------------------------


class GitBranch:
    """The branch a repository is on, according to ``git describe --all``.

    **Commands:**

    ```bash
    ! test -d <repo> || (cd <repo> && git describe --all)
    ```

    **Returns:**

    ```python
    str
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/git/git_git_branch.py``.
    """

    # Mirrors pyinfra's ``git.GitBranch``: ``git describe --all`` prints the
    # current ref as ``heads/<branch>`` (or ``tags/<tag>`` on a tagged
    # commit) and the ``heads/``/``tags/`` prefix is stripped, so a checkout
    # of ``develop`` reads as::
    #
    #     "develop"
    #
    # A missing repository directory short-circuits pyinfra's
    # ``! test -d <repo> ||`` guard to no output, which reads as ``""``.
    # The repo path is ``shlex``-quoted -- a deliberate deviation from
    # pyinfra, which interpolates it raw. pyinfra's ``requires_command``
    # gating is deliberately dropped: with the directory present, a *missing*
    # ``git`` exits 127, which ``run_shell`` reports as ``CommandNotFoundError``
    # rather than ``""``.

    @classmethod
    def command(cls, repo: str) -> str:
        """Shell describing the repo's current ref, guarded on the directory.

        The repo path is quoted, so a path carrying shell metacharacters is
        read literally rather than executed.
        """
        quoted = shlex.quote(repo)
        return f"! test -d {quoted} || (cd {quoted} && git describe --all)"

    @classmethod
    def parse(cls, output: Iterable[str]) -> str:
        """Parse ``git describe --all`` output into the ref name.

        The ``heads/``/``tags/`` prefix is stripped from the joined output
        (pyinfra's ``re.sub``); empty output reads as ``""``.
        """
        return _DESCRIBE_PREFIX_RE.sub("", "\n".join(output))

    @classmethod
    async def process(cls, host: Host, repo: str) -> str:
        branches = getattr(host, "git_branches", None)
        if branches is not None:
            # SimulatedHost seam: repo path -> branch name. str is
            # immutable, so no copy is needed; an unknown repo reads as
            # "", like the short-circuited directory guard.
            return branches.get(repo, "")
        # Real transport: `git describe --all` over asyncssh.
        stdout = await host.run_shell(cls.command(repo))
        return cls.parse(stdout.splitlines())


class GitConfigItem(TypedDict):
    """
    Attributes:
        key: The configuration item's key, for example ``user.email``.
        values: The key's values in listing order; a multi-valued key
            (``safe.directory``) keeps every entry.
    """

    # One configuration item ``GitConfig`` reports.
    #
    # A TypedDict rather than a class instance, like ``AptSource``: entries
    # are ordinary dicts, so equality against a literal dict works and
    # ``json.dumps`` serializes them directly.

    key: str
    values: list[str]


class GitConfig:
    """The git configuration items, as a list of key/values entries.

    **Commands:**

    ```bash
    ! test -d <repo> || (cd <repo> && git config --local -l)
    ```

    **Returns:**

    ```python
    list[GitConfigItem]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/git/git_git_config.py``.
    """

    # Mirrors pyinfra's ``git.GitConfig``: ``git config -l`` prints
    # ``key=value`` rows and the rows fold into one ``GitConfigItem`` per
    # key, in first-appearance order -- a multi-valued key
    # (``safe.directory``) keeps every value in its ``values`` list in
    # listing order -- for example::
    #
    #     [{"key": "user.email", "values": ["nick@gmail.com"]}]
    #
    # The entry list is a deliberate shape deviation from pyinfra's
    # key -> value-list dict (the TypedDict-entry guideline). Without a
    # ``repo`` the login user's scope is read -- ``git config
    # --global -l || true``, or ``git config --system -l || true`` with
    # ``system=True`` -- where the
    # ``|| true`` guard maps a missing git or config file to ``[]``
    # (pyinfra's ``default = dict``, projected onto entries). With a
    # ``repo`` the repository's
    # ``--local`` config is read, and a missing directory short-circuits the
    # ``! test -d`` guard to ``[]`` as well. Rows without an ``=`` are
    # skipped (pyinfra would raise ``ValueError`` on them; reemote5 hardens
    # the parser). The repo path is ``shlex``-quoted -- a deliberate
    # deviation from pyinfra, which interpolates it raw.

    @classmethod
    def command(cls, repo: str | None = None, system: bool = False) -> str:
        """Shell listing the config items of the requested scope.

        ``repo=None`` reads the global (or with ``system=True`` the system)
        scope, swallowing a missing config with ``|| true``; a ``repo``
        reads its local config behind the directory guard.
        """
        if repo is None:
            level = "--system" if system else "--global"
            return f"git config {level} -l || true"
        quoted = shlex.quote(repo)
        return f"! test -d {quoted} || (cd {quoted} && git config --local -l)"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[GitConfigItem]:
        """Parse ``git config -l`` rows into the config item list.

        Each ``key=value`` row appends its value to the key's list (the
        split is on the *first* ``=``, so a value may contain ``=``);
        rows without an ``=`` are skipped. One entry is emitted per key,
        in first-appearance order.
        """
        items: dict[str, list[str]] = {}
        for line in output:
            key, sep, value = line.partition("=")
            if not sep:
                continue
            items.setdefault(key, []).append(value)
        return [{"key": key, "values": values} for key, values in items.items()]

    @classmethod
    async def process(
        cls, host: Host, repo: str | None = None, system: bool = False
    ) -> list[GitConfigItem]:
        scope = repo if repo is not None else ("system" if system else "global")
        configs = getattr(host, "git_configs", None)
        if configs is not None:
            # SimulatedHost seam: scope key -> config dict, where the scope
            # key is the repo path or the "global"/"system" sentinel. The
            # dict store (mutated by the simulated ``git.config``) is
            # projected into entries with copied value lists so callers
            # never mutate the simulated state; an unknown scope reads as
            # [] (pyinfra's ``default = dict``, projected).
            return [
                {"key": key, "values": list(values)}
                for key, values in configs.get(scope, {}).items()
            ]
        # Real transport: `git config -l` over asyncssh.
        stdout = await host.run_shell(cls.command(repo, system))
        return cls.parse(stdout.splitlines())


class GitHeadDetached:
    """Whether the repository's ``HEAD`` is detached, or None if undetermined.

    **Commands:**

    ```bash
    ! test -d <repo> || (cd <repo> && if git symbolic-ref -q HEAD >/dev/null 2>&1; then echo attached; elif git rev-parse --git-dir >/dev/null 2>&1; then echo detached; fi)
    ```

    **Returns:**

    ```python
    bool | None
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/git/git_git_head_detached.py``.
    """

    # A reemote5-original fact (pyinfra has no counterpart). ``git symbolic-ref
    # -q HEAD`` succeeds -- printing ``refs/heads/<branch>`` -- while ``HEAD``
    # points at a branch, and fails silently when ``HEAD`` is detached on a bare
    # commit, the state a tag checkout leaves behind and where a later ``git
    # pull`` fails. The probe is wrapped so the three outcomes stay
    # distinguishable, and the answer is that tri-state, for example::
    #
    #     True   # detached: a tag or commit is checked out directly
    #
    # ``attached`` reads as ``False`` (``HEAD`` is on a branch), ``detached`` as
    # ``True``, and *no* token -- a missing repository directory (the
    # ``! test -d`` guard) or a directory that is not a repository (the ``git
    # rev-parse --git-dir`` fallback also fails) -- reads as ``None``, so an
    # absent or invalid repository is never mistaken for a detached one (an
    # operation can then clone instead of skipping the pull). The repo path is
    # ``shlex``-quoted, like every sibling fact.

    @classmethod
    def command(cls, repo: str) -> str:
        """Shell printing ``attached``/``detached``, guarded on the directory.

        ``git symbolic-ref -q HEAD`` is the primary probe; a ``git rev-parse
        --git-dir`` fallback separates a detached ``HEAD`` from a directory
        that is not a repository at all, which prints neither token.
        """
        quoted = shlex.quote(repo)
        return (
            f"! test -d {quoted} || (cd {quoted} && "
            "if git symbolic-ref -q HEAD >/dev/null 2>&1; then echo attached; "
            "elif git rev-parse --git-dir >/dev/null 2>&1; then echo detached; fi)"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> bool | None:
        """Parse the ``attached``/``detached`` token into the tri-state.

        ``detached`` reads as ``True`` and ``attached`` as ``False``; any
        other first non-blank line -- including no output at all -- reads as
        ``None`` (the repository state could not be determined).
        """
        for line in output:
            token = line.strip()
            if not token:
                continue
            if token == "detached":
                return True
            if token == "attached":
                return False
            return None
        return None

    @classmethod
    async def process(cls, host: Host, repo: str) -> bool | None:
        detached = getattr(host, "git_head_detached", None)
        if detached is not None:
            # SimulatedHost seam: repo path -> detached bool. An absent repo
            # reads as None (undetermined), like the wrapped probe printing
            # no token for a missing or invalid repository.
            return detached.get(repo)
        # Real transport: the `git symbolic-ref` probe over asyncssh.
        stdout = await host.run_shell(cls.command(repo))
        return cls.parse(stdout.splitlines())


class GitIsRepo:
    """Whether a directory is a valid git working-tree repository.

    **Commands:**

    ```bash
    ! test -d <repo> || (cd <repo> && git rev-parse --is-inside-work-tree 2>/dev/null)
    ```

    **Returns:**

    ```python
    bool
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/git/git_git_is_repo.py``.
    """

    # A reemote5-original fact (pyinfra has no counterpart). ``git rev-parse
    # --is-inside-work-tree`` asks git itself to resolve the working tree, so it
    # *validates* the repository instead of merely trusting that a ``.git``
    # directory exists -- a corrupt or half-initialized ``.git`` answers
    # nothing::
    #
    #     True
    #
    # The answer is ``True`` only when the first output line is exactly ``true``.
    # A missing repository directory (the ``! test -d`` guard), a directory that
    # is not a repository, a *bare* repository (which has no work tree, so
    # ``--is-inside-work-tree`` prints ``false``) and a corrupt ``.git`` all read
    # as ``False`` (``2>/dev/null`` swallows git's ``fatal:`` diagnostic). This
    # is a more precise validity check than probing for a ``.git`` directory, so
    # an operation can refuse to act on a broken checkout. The repo path is
    # ``shlex``-quoted, like every sibling fact.

    @classmethod
    def command(cls, repo: str) -> str:
        """Shell resolving the work tree, guarded on the directory.

        ``2>/dev/null`` swallows the ``fatal: not a git repository`` a
        non-repository writes, leaving ``process`` to report ``False``.
        """
        quoted = shlex.quote(repo)
        return (
            f"! test -d {quoted} || "
            f"(cd {quoted} && git rev-parse --is-inside-work-tree 2>/dev/null)"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> bool:
        """Parse ``git rev-parse --is-inside-work-tree`` into a bool.

        ``True`` only when the first non-blank line is exactly ``true``;
        anything else -- including no output -- reads as ``False``.
        """
        for line in output:
            token = line.strip()
            if not token:
                continue
            return token == "true"
        return False

    @classmethod
    async def process(cls, host: Host, repo: str) -> bool:
        repos = getattr(host, "git_repos", None)
        if repos is not None:
            # SimulatedHost seam: the set of paths that are valid work-tree
            # repositories. Membership is the answer; an absent repo reads as
            # False, like the guard/rev-parse printing no `true`.
            return repo in repos
        # Real transport: `git rev-parse --is-inside-work-tree` over asyncssh.
        stdout = await host.run_shell(cls.command(repo))
        return cls.parse(stdout.splitlines())


class GitLocalCommit:
    """The SHA of a local ref (default ``HEAD``) in a repository, or None.

    **Commands:**

    ```bash
    ! test -d <repo> || (cd <repo> && git rev-parse <ref> 2>/dev/null) || true
    ```

    **Returns:**

    ```python
    str | None
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/git/git_git_local_commit.py``.
    """

    # Mirrors pyinfra's ``git.GitLocalCommit``: ``git rev-parse <ref>``
    # prints the commit digest the ref resolves to, and the first line is
    # kept only when it fullmatches a 40-character (SHA-1) or 64-character
    # (SHA-256) lowercase hex digest, for example::
    #
    #     "1a2b3c4d5e6f7890abcdef1234567890abcdef12"
    #
    # A missing repository directory (the ``! test -d`` guard), an unknown
    # ref (``2>/dev/null`` plus the trailing ``|| true`` swallow the
    # failure) or any non-SHA answer reads as ``None``, exactly like
    # pyinfra's fixtures. The repo path and the ref are ``shlex``-quoted --
    # a deliberate deviation from pyinfra, which interpolates both raw.

    @classmethod
    def command(cls, repo: str, ref: str = "HEAD") -> str:
        """Shell resolving the ref's SHA, swallowing every failure mode.

        The directory guard, ``2>/dev/null`` and ``|| true`` keep a
        missing repo, an unknown ref and a missing git all at no output.
        """
        quoted = shlex.quote(repo)
        return (
            f"! test -d {quoted} || "
            f"(cd {quoted} && git rev-parse {shlex.quote(ref)} 2>/dev/null) || true"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> str | None:
        """Parse ``git rev-parse`` output into the SHA string, or None.

        Only the first line is considered: it must fullmatch the SHA
        digest shape (40 or 64 lowercase hex characters); anything else --
        including no output at all -- reads as ``None``.
        """
        for line in output:
            stripped = line.strip()
            return stripped if _SHA_RE.fullmatch(stripped) else None
        return None

    @classmethod
    async def process(cls, host: Host, repo: str, ref: str = "HEAD") -> str | None:
        commits = getattr(host, "git_local_commits", None)
        if commits is not None:
            # SimulatedHost seam: repo path -> ref -> SHA. str is
            # immutable, so no copy is needed; an unknown repo or ref
            # reads as None, like the swallowed rev-parse failure.
            return commits.get(repo, {}).get(ref)
        # Real transport: `git rev-parse` over asyncssh.
        stdout = await host.run_shell(cls.command(repo, ref))
        return cls.parse(stdout.splitlines())


class GitRemoteBranchCommit:
    """The tip SHA of a branch on a remote, via ``git ls-remote``, or None.

    **Commands:**

    ```bash
    ! test -d <repo> || (cd <repo> && git ls-remote <remote> <branch> 2>/dev/null | head -n1)
    ```

    **Returns:**

    ```python
    str | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/git/git_git_remote_branch_commit.py``.
    """

    # Mirrors pyinfra's ``git.GitRemoteBranchCommit``: ``git ls-remote
    # <remote> <branch>`` prints ``<sha>\\t<ref>`` rows and the first column
    # of the first row (``| head -n1``) is kept when it fullmatches the SHA
    # digest shape, for example::
    #
    #     "1a2b3c4d5e6f7890abcdef1234567890abcdef12"
    #
    # ``branch`` defaults to ``None``, which asks for the remote's ``HEAD``;
    # a falsy branch (``None`` or ``""``) takes that default, exactly like
    # pyinfra's truthiness check. A missing repository directory (the
    # ``! test -d`` guard), an unreachable remote or an unknown branch
    # (``2>/dev/null``) reads as ``None``, as does a first column that is
    # not a full digest. The repo path, remote name and ref are
    # ``shlex``-quoted -- a deliberate deviation from pyinfra, which
    # interpolates them raw.

    @classmethod
    def command(cls, repo: str, remote: str = "origin", branch: str | None = None) -> str:
        """Shell listing the remote's ref, guarded on the directory.

        A falsy ``branch`` asks for the remote's ``HEAD``, mirroring
        pyinfra's ``ref = branch if branch else "HEAD"``.
        """
        ref = branch if branch else "HEAD"
        quoted = shlex.quote(repo)
        return (
            f"! test -d {quoted} || (cd {quoted} && git ls-remote "
            f"{shlex.quote(remote)} {shlex.quote(ref)} 2>/dev/null | head -n1)"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> str | None:
        """Parse ``git ls-remote`` output into the tip SHA, or None.

        Only the first row's first tab-separated column is considered, and
        only when it fullmatches the SHA digest shape; anything else --
        including no output at all -- reads as ``None``.
        """
        for line in output:
            digest = line.strip().split("\t", 1)[0]
            return digest if _SHA_RE.fullmatch(digest) else None
        return None

    @classmethod
    async def process(
        cls, host: Host, repo: str, remote: str = "origin", branch: str | None = None
    ) -> str | None:
        # Resolve the effective ref first, even on the simulated path, so
        # a falsy branch keys the seam as "HEAD" on both transports.
        ref = branch if branch else "HEAD"
        commits = getattr(host, "git_remote_commits", None)
        if commits is not None:
            # SimulatedHost seam: repo path -> remote name -> ref -> SHA.
            # str is immutable, so no copy is needed; an unknown repo,
            # remote or ref reads as None, like the swallowed ls-remote
            # failure.
            return commits.get(repo, {}).get(remote, {}).get(ref)
        # Real transport: `git ls-remote` over asyncssh.
        stdout = await host.run_shell(cls.command(repo, remote, branch))
        return cls.parse(stdout.splitlines())


class GitRemoteUrl:
    """A remote's fetch (or push) URL, or None when it cannot be resolved.

    **Commands:**

    ```bash
    ! test -d <repo> || (cd <repo> && git remote get-url <remote> 2>/dev/null)
    ```

    **Returns:**

    ```python
    str | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/git/git_git_remote_url.py``.
    """

    # A reemote5-original fact (pyinfra has no counterpart). ``git remote
    # get-url <remote>`` prints the URL a repository fetches from -- or, with
    # ``push=True``, the one it pushes to (``--push``) -- for example::
    #
    #     "git@github.com:acme/app.git"
    #
    # ``remote`` defaults to ``origin``. A missing repository directory (the
    # ``! test -d`` guard), an unknown remote (``git remote get-url`` exits 2
    # with ``error: No such remote``, swallowed by ``2>/dev/null``) and any
    # other failure all read as ``None``, so an operation can compare the
    # answer against the URL it expects and re-point a migrated remote instead
    # of pulling from the wrong one. The repo path and the remote name are
    # ``shlex``-quoted, like every sibling fact.

    @classmethod
    def command(cls, repo: str, remote: str = "origin", push: bool = False) -> str:
        """Shell printing the remote's URL, guarded on the directory.

        ``--push`` selects the push URL; ``2>/dev/null`` swallows the
        ``error: No such remote`` an unknown remote writes, leaving
        ``process`` to report ``None``.
        """
        quoted = shlex.quote(repo)
        flag = " --push" if push else ""
        return (
            f"! test -d {quoted} || (cd {quoted} && "
            f"git remote get-url{flag} {shlex.quote(remote)} 2>/dev/null)"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> str | None:
        """Parse ``git remote get-url`` output into the URL string, or None.

        The first non-blank line, stripped, is the URL; empty output -- an
        unknown remote or a missing repository -- reads as ``None``.
        """
        for line in output:
            token = line.strip()
            if not token:
                continue
            return token
        return None

    @classmethod
    async def process(
        cls, host: Host, repo: str, remote: str = "origin", push: bool = False
    ) -> str | None:
        kind = "push" if push else "fetch"
        urls = getattr(host, "git_remote_urls", None)
        if urls is not None:
            # SimulatedHost seam: repo path -> remote name -> "fetch"/"push"
            # -> URL. str is immutable, so no copy is needed; an unknown
            # repo, remote or kind reads as None, like the swallowed
            # `git remote get-url` failure.
            return urls.get(repo, {}).get(remote, {}).get(kind)
        # Real transport: `git remote get-url` over asyncssh.
        stdout = await host.run_shell(cls.command(repo, remote, push))
        return cls.parse(stdout.splitlines())


class GitSshKeyConfigured:
    """Whether ``ssh -T`` authenticates the user to a git remote host.

    **Commands:**

    ```bash
    ssh -T -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=<timeout> <user>@<remote_host> >/dev/null 2>&1; rc=$?; test "$rc" -eq 127 && exit 127; test "$rc" -ne 255 && echo authenticated || echo unauthenticated
    ```

    **Returns:**

    ```python
    bool
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/git/git_git_ssh_key_configured.py``.
    """

    # A reemote5-original fact (pyinfra has no counterpart), and the one fact
    # here that is *not* repository-scoped: it probes the login user's SSH
    # configuration against a remote host before a clone or pull depends on it.
    # ``ssh -T <user>@<host>`` attempts a non-interactive authentication; the
    # probe normalizes ssh's exit status in-shell because a *successful* git
    # handshake still exits non-zero (GitHub answers ``1`` -- it grants no
    # shell -- while printing its welcome banner on stderr), and only ``255``
    # means the connection or the authentication itself failed::
    #
    #     True
    #
    # The answer is ``True`` when the probe prints ``authenticated`` (any exit
    # status but ``255``) and ``False`` when it prints ``unauthenticated``
    # (``255``: unreachable host, refused key, no agent). ``BatchMode=yes``
    # forbids password prompts so the fact never blocks, and
    # ``StrictHostKeyChecking=accept-new`` records an unseen host key instead of
    # failing on it. A missing ``ssh`` exits ``127``, which the probe re-raises
    # so ``run_shell`` reports ``CommandNotFoundError`` rather than a false
    # ``False``. The ``user``@``remote_host`` target is ``shlex``-quoted.

    @classmethod
    def command(cls, remote_host: str, user: str = "git", timeout: int = 10) -> str:
        """Shell printing ``authenticated``/``unauthenticated`` for the host.

        ``ssh -T`` runs in batch mode with a connect timeout; its exit status
        is captured (``rc``) and mapped -- ``127`` propagates a missing ssh,
        ``255`` is the only failure, anything else reads as authenticated.
        """
        target = shlex.quote(f"{user}@{remote_host}")
        return (
            f"ssh -T -o BatchMode=yes -o StrictHostKeyChecking=accept-new "
            f"-o ConnectTimeout={int(timeout)} {target} >/dev/null 2>&1; "
            'rc=$?; test "$rc" -eq 127 && exit 127; '
            'test "$rc" -ne 255 && echo authenticated || echo unauthenticated'
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> bool:
        """Parse the ``authenticated``/``unauthenticated`` token into a bool.

        ``True`` only when the first non-blank line is ``authenticated``;
        anything else -- including no output -- reads as ``False``.
        """
        for line in output:
            token = line.strip()
            if not token:
                continue
            return token == "authenticated"
        return False

    @classmethod
    async def process(
        cls, host: Host, remote_host: str, user: str = "git", timeout: int = 10
    ) -> bool:
        target = f"{user}@{remote_host}"
        authenticated = getattr(host, "git_ssh_authenticated", None)
        if authenticated is not None:
            # SimulatedHost seam: the set of "user@host" targets whose SSH
            # authentication succeeds. Membership is the answer; an absent
            # target reads as False, like the probe printing unauthenticated.
            return target in authenticated
        # Real transport: the `ssh -T` probe over asyncssh.
        stdout = await host.run_shell(cls.command(remote_host, user, timeout))
        return cls.parse(stdout.splitlines())


class GitStatusSummary(TypedDict):
    """
    Attributes:
        dirty: ``True`` when any of the three path lists is non-empty.
        staged: Paths with an index (``git add``-ed) change.
        unstaged: Paths with a work-tree change not yet staged. A path both
            staged and modified in the work tree appears in both lists.
        untracked: Paths git does not track (the porcelain ``??`` rows).
    """

    # The working-tree state ``GitStatus`` reports for a repository.
    #
    # A TypedDict rather than a class instance, like ``AptSource``: the answer
    # is an ordinary dict, so equality against a literal dict works and
    # ``json.dumps`` serializes it directly.

    dirty: bool
    staged: list[str]
    unstaged: list[str]
    untracked: list[str]


class GitStatus:
    """The working tree's staged, unstaged and untracked changes.

    **Commands:**

    ```bash
    ! test -d <repo> || (cd <repo> && git status --porcelain)
    ```

    **Returns:**

    ```python
    GitStatusSummary
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/git/git_git_status.py``.
    """

    # A reemote5-original fact (pyinfra has no counterpart). ``git status
    # --porcelain`` prints one row per changed path as two status columns
    # (index, then work tree) plus the path, and ``??`` marks an untracked
    # file; the rows are folded into a ``GitStatusSummary``, for example::
    #
    #     {"dirty": True, "staged": [], "unstaged": ["app.py"], "untracked": []}
    #
    # A row whose index column is not a space is staged, one whose work-tree
    # column is not a space is unstaged (a path can be both), and ``??`` is
    # untracked; ``dirty`` is ``True`` when any list is non-empty. A rename row
    # keeps its ``orig -> new`` path verbatim. A missing repository directory
    # short-circuits the ``! test -d`` guard to no output, which reads as a
    # clean summary (``dirty=False``, empty lists), as does a repository with
    # no changes -- so an operation can refuse to pull over a dirty tree. The
    # repo path is ``shlex``-quoted, like every sibling fact.

    @classmethod
    def command(cls, repo: str) -> str:
        """Shell printing the porcelain working-tree status, guarded on the dir."""
        quoted = shlex.quote(repo)
        return f"! test -d {quoted} || (cd {quoted} && git status --porcelain)"

    @classmethod
    def parse(cls, output: Iterable[str]) -> GitStatusSummary:
        """Parse ``git status --porcelain`` rows into a summary.

        Each row's first two columns are the index and work-tree status and
        its fourth column onward the path (``XY<space>PATH``): ``??`` is
        untracked, a non-space index column is staged, a non-space work-tree
        column is unstaged. Short or blank rows are skipped; empty output
        reads as a clean summary.
        """
        staged: list[str] = []
        unstaged: list[str] = []
        untracked: list[str] = []
        for line in output:
            if len(line) < 4 or not line.strip():
                continue
            index, worktree, path = line[0], line[1], line[3:]
            if index == "?" and worktree == "?":
                untracked.append(path)
                continue
            if index != " ":
                staged.append(path)
            if worktree != " ":
                unstaged.append(path)
        return {
            "dirty": bool(staged or unstaged or untracked),
            "staged": staged,
            "unstaged": unstaged,
            "untracked": untracked,
        }

    @classmethod
    async def process(cls, host: Host, repo: str) -> GitStatusSummary:
        statuses = getattr(host, "git_statuses", None)
        if statuses is not None:
            # SimulatedHost seam: repo path -> status summary. Copy the
            # summary and its three lists so callers never mutate the
            # simulated state; an unknown repo reads as a clean summary,
            # like the short-circuited directory guard.
            status = statuses.get(repo)
            if status is None:
                return {"dirty": False, "staged": [], "unstaged": [], "untracked": []}
            return {
                **status,
                "staged": list(status["staged"]),
                "unstaged": list(status["unstaged"]),
                "untracked": list(status["untracked"]),
            }
        # Real transport: `git status --porcelain` over asyncssh.
        stdout = await host.run_shell(cls.command(repo))
        return cls.parse(stdout.splitlines())


class GitSubmoduleStatus(TypedDict):
    """
    Attributes:
        path: The submodule's path within the repository.
        sha: The commit the submodule is checked out at.
        state: One of ``synced``, ``uninitialized``, ``modified``,
            ``conflicted`` or ``unknown``.
        describe: The ``git describe`` detail git appends in parentheses, or
            ``None`` when the row carries none (an uninitialized submodule).
    """

    # One submodule row ``GitSubmodule`` reports.
    #
    # A TypedDict rather than a class instance, like ``AptSource``: rows are
    # ordinary dicts, so equality against a literal dict works and
    # ``json.dumps`` serializes them directly.

    path: str
    sha: str
    state: str
    describe: str | None


class GitSubmodule:
    """The repository's submodules and their synchronization state.

    **Commands:**

    ```bash
    ! test -d <repo> || (cd <repo> && git submodule status)
    ```

    **Returns:**

    ```python
    list[GitSubmoduleStatus]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/git/git_git_submodule.py``.
    """

    # A reemote5-original fact (pyinfra has no counterpart). ``git submodule
    # status`` prints one row per submodule as a leading state character, the
    # recorded SHA, the path and -- for an initialized submodule -- a
    # ``git describe`` detail in parentheses; the rows fold into a list of
    # ``GitSubmoduleStatus`` dicts, for example::
    #
    #     [{"path": "vendor/lib", "sha": "1a2b...", "state": "synced",
    #       "describe": "heads/main"}]
    #
    # The leading character maps through ``_SUBMODULE_STATES``: a space is
    # ``synced`` (initialized, at the recorded commit), ``-`` is
    # ``uninitialized``, ``+`` is ``modified`` (checked out elsewhere) and
    # ``U`` is ``conflicted``; any other character reads as ``unknown``. A
    # missing repository directory short-circuits the ``! test -d`` guard to no
    # output, which reads as ``[]``, as does a repository with no submodules --
    # so an operation can run ``submodule update`` only when a row is out of
    # sync. The repo path is ``shlex``-quoted, like every sibling fact.

    @classmethod
    def command(cls, repo: str) -> str:
        """Shell printing the submodule status rows, guarded on the directory."""
        quoted = shlex.quote(repo)
        return f"! test -d {quoted} || (cd {quoted} && git submodule status)"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[GitSubmoduleStatus]:
        """Parse ``git submodule status`` rows into the submodule list.

        Each row is ``<state><sha> <path>[(<describe>)]``: the first
        character is the state prefix, the token after it up to the first
        space the SHA, and the remainder the path plus an optional trailing
        parenthesized describe (split on the *last* `` (``, so a path
        containing one is kept whole). Blank rows are skipped; empty output
        reads as ``[]``.
        """
        submodules: list[GitSubmoduleStatus] = []
        for line in output:
            if not line.strip():
                continue
            prefix, remainder = line[0], line[1:]
            sha, _, rest = remainder.partition(" ")
            path_part, sep, describe_part = rest.rpartition(" (")
            if sep and describe_part.endswith(")"):
                path, describe = path_part, describe_part[:-1]
            else:
                path, describe = rest, None
            submodules.append(
                {
                    "path": path,
                    "sha": sha,
                    "state": _SUBMODULE_STATES.get(prefix, "unknown"),
                    "describe": describe,
                }
            )
        return submodules

    @classmethod
    async def process(cls, host: Host, repo: str) -> list[GitSubmoduleStatus]:
        submodules = getattr(host, "git_submodules", None)
        if submodules is not None:
            # SimulatedHost seam: repo path -> submodule row list. Copy each
            # row so callers never mutate the simulated state; an unknown
            # repo reads as [], like the short-circuited directory guard.
            return [{**entry} for entry in submodules.get(repo, [])]
        # Real transport: `git submodule status` over asyncssh.
        stdout = await host.run_shell(cls.command(repo))
        return cls.parse(stdout.splitlines())


class GitTagInfo(TypedDict):
    """
    Attributes:
        name: The tag name, for example ``v1.0.0``.
    """

    # One tag ``GitTag`` reports. Named with the Info suffix because the
    # bare ``GitTag`` slot is the fact class (the ApkPackageInfo precedent).
    #
    # A TypedDict rather than a class instance, like ``AptSource``: entries
    # are ordinary dicts, so equality against a literal dict works and
    # ``json.dumps`` serializes them directly.

    name: str


class GitTag:
    """The repository's tags, as ``GitTagInfo`` entries in ``git tag`` order.

    **Commands:**

    ```bash
    ! test -d <repo> || (cd <repo> && git tag)
    ```

    **Returns:**

    ```python
    list[GitTagInfo]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/git/git_git_tag.py``.
    """

    # Mirrors pyinfra's ``git.GitTag``: ``git tag`` lists one tag per line,
    # sorted by name, and each line becomes one ``GitTagInfo`` entry, in
    # listing order, for example::
    #
    #     [{"name": "v1.0.0"}, {"name": "v1.0.1"}]
    #
    # The entry list is a deliberate shape deviation from pyinfra's bare
    # name list (the TypedDict-entry guideline).
    # A missing repository directory short-circuits pyinfra's
    # ``! test -d <repo> ||`` guard to no output, which reads as ``[]``.
    # The repo path is ``shlex``-quoted -- a deliberate deviation from
    # pyinfra, which interpolates it raw.

    @classmethod
    def command(cls, repo: str) -> str:
        """Shell listing the repo's tags, guarded on the directory."""
        quoted = shlex.quote(repo)
        return f"! test -d {quoted} || (cd {quoted} && git tag)"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[GitTagInfo]:
        """Parse ``git tag`` output into the tag entry list.

        Every line becomes one ``{"name": line}`` entry, in listing order
        (pyinfra returns its output lines unchanged); empty output reads
        as ``[]``.
        """
        return [{"name": line} for line in output]

    @classmethod
    async def process(cls, host: Host, repo: str) -> list[GitTagInfo]:
        tags = getattr(host, "git_tags", None)
        if tags is not None:
            # SimulatedHost seam: repo path -> tag entry list. Copy each
            # entry so callers never mutate the simulated state; an unknown
            # repo reads as [], like the short-circuited directory guard.
            return [{**entry} for entry in tags.get(repo, [])]
        # Real transport: `git tag` over asyncssh.
        stdout = await host.run_shell(cls.command(repo))
        return cls.parse(stdout.splitlines())


class GitTrackingBranch:
    """The upstream tracking branch of the checked-out branch, or None.

    **Commands:**

    ```bash
    ! test -d <repo> || (cd <repo> && git status --branch --porcelain)
    ```

    **Returns:**

    ```python
    str | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/git/git_git_tracking_branch.py``.
    """

    # Mirrors pyinfra's ``git.GitTrackingBranch``: ``git status --branch
    # --porcelain`` heads its output with ``## <branch>...<upstream>`` (plus
    # an optional ``[ahead N, behind M]`` detail) and the token after the
    # ``...`` separator is the upstream, for example::
    #
    #     "origin/master"
    #
    # A branch without an upstream (``## branch1``) carries no separator and
    # reads as ``None`` -- pyinfra's fixture -- as does a missing repository
    # directory (the ``! test -d`` guard short-circuits to no output). Only
    # the first output line is searched, and the ``\\S+`` token stops at the
    # first space, so the trailing status detail never reaches the match.
    # The repo path is ``shlex``-quoted -- a deliberate deviation from
    # pyinfra, which interpolates it raw.

    @classmethod
    def command(cls, repo: str) -> str:
        """Shell printing the porcelain branch header, guarded on the dir."""
        quoted = shlex.quote(repo)
        return f"! test -d {quoted} || (cd {quoted} && git status --branch --porcelain)"

    @classmethod
    def parse(cls, output: Iterable[str]) -> str | None:
        """Parse the porcelain branch header into the upstream, or None.

        Only the first line is searched for the ``...<upstream>`` token
        (pyinfra's ``re.search``); a line without the separator -- or no
        output at all -- reads as ``None``.
        """
        for line in output:
            match = _TRACKING_RE.search(line)
            return match.group(1) if match else None
        return None

    @classmethod
    async def process(cls, host: Host, repo: str) -> str | None:
        tracking = getattr(host, "git_tracking_branches", None)
        if tracking is not None:
            # SimulatedHost seam: repo path -> upstream branch. str is
            # immutable, so no copy is needed; an unknown repo reads as
            # None, like a branch without an upstream.
            return tracking.get(repo)
        # Real transport: `git status --branch --porcelain` over asyncssh.
        stdout = await host.run_shell(cls.command(repo))
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------


class _DirectoryExists:
    """Whether a directory exists at the path (git-local ``files.Directory``).

    A private, in-namespace stand-in for ``files.Directory``: ``git.worktree``
    must learn whether the worktree directory (and the source repository's
    ``.git``) exist, but the zero-cross-module-import invariant forbids
    reaching into the ``files`` namespace for its ``Directory`` fact -- the
    ``apt.sources_file`` pattern of an in-namespace fact instead of a
    cross-module import. Only the boolean existence matters here, not
    ``Directory``'s full metadata dict, so this reads the *same*
    ``directory_stats`` seam and answers ``True`` only for a real directory:
    a path that exists but is not a directory (``False`` in the seam), or is
    absent (no key), reads ``False``. On the real transport it runs
    ``test -d``, whose non-zero exit on a missing or non-directory path maps
    to ``False`` exactly like the seam.
    """

    @classmethod
    async def process(cls, host: Host, path: str) -> bool:
        stats = getattr(host, "directory_stats", None)
        if stats is not None:
            # SimulatedHost seam (shared with files.Directory): a path maps
            # to a metadata dict for a directory, False for an existing
            # non-directory, or is absent for a missing path.
            return isinstance(stats.get(path), dict)
        # Real transport: `test -d` over asyncssh (check is off in run_shell).
        stdout = await host.run_shell(f"test -d {shlex.quote(path)} && echo yes")
        return stdout.strip() == "yes"


@dataclass(frozen=True)
class GitWorktreeAddCommand(Command):
    """``cd <repo> && git worktree add ...``: register a new working tree.

    The create mutation ``git.worktree`` yields. Mirrors pyinfra's argument
    order exactly: ``-b <new_branch>`` (or ``--detach`` when no new branch)
    comes first, then ``--force``, then the worktree path, then the optional
    ``commitish``. Every user-supplied operand is ``shlex``-quoted, a
    deliberate deviation from pyinfra's raw ``QuoteString`` interpolation --
    plain paths quote to themselves, so command parity holds for normal
    values while a path carrying shell metacharacters is read literally.
    """

    repo: str
    worktree: str
    new_branch: str | None = None
    detached: bool = False
    force: bool = False
    commitish: str | None = None

    def shell(self) -> str:
        parts = ["cd", shlex.quote(self.repo), "&&", "git", "worktree", "add"]
        if self.new_branch:
            parts.extend(["-b", shlex.quote(self.new_branch)])
        elif self.detached:
            parts.append("--detach")
        if self.force:
            parts.append("--force")
        parts.append(shlex.quote(self.worktree))
        if self.commitish:
            parts.append(shlex.quote(self.commitish))
        return " ".join(parts)


@dataclass(frozen=True)
class GitWorktreeRemoveCommand(Command):
    """``cd <worktree> && git worktree remove .``: unregister a working tree.

    The removal mutation ``git.worktree`` yields for ``present=False``.
    pyinfra changes into the worktree (``cd``) and removes ``.`` (itself), so
    the command needs no separate repo argument; ``force`` appends ``--force``
    to drop an unclean tree.
    """

    worktree: str
    force: bool = False

    def shell(self) -> str:
        parts = [
            "cd",
            shlex.quote(self.worktree),
            "&&",
            "git",
            "worktree",
            "remove",
            ".",
        ]
        if self.force:
            parts.append("--force")
        return " ".join(parts)


@dataclass(frozen=True)
class GitPullCommand(Command):
    """``cd <worktree> && git pull``: update an existing working tree.

    The pull mutation ``git.worktree`` yields for an existing tree linked to
    a tracking branch (or given an explicit ``from_remote_branch``).
    ``rebase`` inserts ``--rebase``; ``from_remote_branch`` -- a
    ``(remote, branch)`` tuple -- appends the two operands, exactly as
    pyinfra does. A pull driven by the tree's own tracking branch renders a
    bare ``git pull`` (the remote/branch are *not* appended), matching
    pyinfra, which only names them for the explicit ``from_remote_branch``
    form.
    """

    worktree: str
    rebase: bool = False
    from_remote_branch: tuple[str, str] | None = None

    def shell(self) -> str:
        parts = ["cd", shlex.quote(self.worktree), "&&", "git", "pull"]
        if self.rebase:
            parts.append("--rebase")
        if self.from_remote_branch:
            remote, branch = self.from_remote_branch
            parts.extend([shlex.quote(remote), shlex.quote(branch)])
        return " ".join(parts)


async def worktree(
    ctx: OpContext,
    worktree: str,
    repo: str | None = None,
    detached: bool = False,
    new_branch: str | None = None,
    commitish: str | None = None,
    pull: bool = True,
    rebase: bool = False,
    from_remote_branch: tuple[str, str] | None = None,
    present: bool = True,
    assume_repo_exists: bool = False,
    force: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Manage git working trees.

    Args:
        ctx (OpContext): Execution context for the operation.
        worktree (str): The working-tree directory to manage.
        repo (str | None, optional): The main repository directory the
            worktree is added from (required when creating; unused when
            removing). Defaults to None.
        detached (bool, optional): Create the working tree with a detached
            ``HEAD``. Defaults to False.
        new_branch (str | None, optional): A local branch created together
            with the worktree. Defaults to None.
        commitish (str | None, optional): The commit/branch/tag the worktree
            is created from. Defaults to None.
        pull (bool, optional): Pull changes for an existing worktree linked
            to a remote branch. Defaults to True.
        rebase (bool, optional): When pulling, use ``--rebase``. Defaults to
            False.
        from_remote_branch (tuple[str, str] | None, optional): A
            ``(remote, branch)`` 2-tuple naming the remote branch to pull
            from. Defaults to None.
        present (bool, optional): Whether the working tree should exist.
            Defaults to True.
        assume_repo_exists (bool, optional): Skip the ``repo/.git`` validity
            check. Defaults to False.
        force (bool, optional): Pass ``--force`` when adding or removing the
            worktree. Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated worktree add/remove and pull commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OperationError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/git/git_worktree.py``.
    """

    # Mirrors pyinfra's ``git.worktree``. The operation branches on the
    # worktree's existence (read through the git-local ``_DirectoryExists``
    # fact), so it is idempotent across the three directions:
    #
    # * Create (``present`` and the directory is absent): validates the
    #   source repository -- ``repo`` must be given (``ValueError``) and,
    #   unless ``assume_repo_exists``, ``repo/.git`` must be a directory
    #   (``OperationError``, pyinfra's message) -- then yields one
    #   ``GitWorktreeAddCommand``.
    # * Remove (``present=False`` and the directory exists): yields one
    #   ``GitWorktreeRemoveCommand``.
    # * Pull (``present`` and the directory exists): when ``pull`` is set and
    #   the tree is linked to a tracking branch (``GitTrackingBranch``) or an
    #   explicit ``from_remote_branch`` is given, it short-circuits the pull
    #   when the tree's ``GitLocalCommit`` already equals the remote tip
    #   (``GitRemoteBranchCommit``) -- pyinfra's ``skip_pull`` -- otherwise it
    #   yields one ``GitPullCommand``. With ``pull=False``, no tracking branch
    #   and no ``from_remote_branch``, it is a no-op.
    # * Absent and ``present=False`` is a no-op.
    #
    # Creating or removing a worktree writes under the repository's ``.git``,
    # which is often root-owned, so flag the request ``sudo=True`` when the
    # login user cannot write there.
    #
    # Deliberate deviations from pyinfra: the ``user``/``group`` options (and
    # the up-to-four ``chown`` commands they emit for the worktree, its
    # ``.git/worktrees`` metadata and the new branch's refs/logs) are dropped
    # -- reemote5 has no ``chown`` primitive and routes ownership through the
    # inventory ``privilege`` plus the ``sudo``/``su`` meta-parameters, not a
    # per-operation ``chown``. The ``repo is None`` check runs *before* the
    # ``repo/.git`` probe (pyinfra joins ``None`` with ``.git`` first and would
    # crash), and argument-shape errors raise ``ValueError`` (reemote5's
    # argument-validation idiom) while the invalid-repository state error
    # keeps pyinfra's ``OperationError``. Because reemote5 operations are
    # phase-unaware, those errors surface in *both* the prepare and apply
    # phases. ``**kwargs`` absorbs pyinfra's global arguments (``op()`` already
    # strips ``name``/``sudo``/``su``).
    reasons: list[str] = []
    exists = await ctx.facts.get(_DirectoryExists, worktree)

    if present and not exists:
        # Create the worktree from the source repository.
        if repo is None:
            raise ValueError("repo must be specified when creating a worktree")
        if not assume_repo_exists:
            repo_git = posixpath.join(repo, ".git")
            if not await ctx.facts.get(_DirectoryExists, repo_git):
                raise OperationError(
                    f"The following folder is not a valid GIT repository : {repo}"
                )
        ctx.changed = True
        reasons.append(f"worktree {worktree} missing, will add")
        yield GitWorktreeAddCommand(
            repo=repo,
            worktree=worktree,
            new_branch=new_branch,
            detached=detached,
            force=force,
            commitish=commitish,
        )
    elif exists and not present:
        # Remove the existing worktree.
        ctx.changed = True
        reasons.append(f"worktree {worktree} present, will remove")
        yield GitWorktreeRemoveCommand(worktree=worktree, force=force)
    elif exists and present:
        # The worktree exists and is wanted: pull/rebase it.
        if not pull:
            reasons.append("pull is disabled")
        else:
            tracking = await ctx.facts.get(GitTrackingBranch, worktree)
            if not tracking and not from_remote_branch:
                reasons.append(
                    f"worktree {worktree} is not linked to a tracking branch"
                )
            else:
                if from_remote_branch is not None and len(from_remote_branch) != 2:
                    raise ValueError(
                        "The remote branch must be a 2-tuple (remote, branch) "
                        'such as ("origin", "master")'
                    )
                remote_name: str | None = None
                remote_branch: str | None = None
                if from_remote_branch:
                    remote_name, remote_branch = from_remote_branch
                elif tracking and "/" in tracking:
                    remote_name, remote_branch = tracking.split("/", 1)

                skip_pull = False
                if remote_name and remote_branch:
                    local_commit = await ctx.facts.get(GitLocalCommit, worktree)
                    remote_commit = await ctx.facts.get(
                        GitRemoteBranchCommit, worktree, remote_name, remote_branch
                    )
                    if (
                        local_commit
                        and remote_commit
                        and local_commit == remote_commit
                    ):
                        skip_pull = True

                if skip_pull:
                    reasons.append(f"worktree {worktree} is already up to date")
                else:
                    ctx.changed = True
                    reasons.append(f"worktree {worktree} will be pulled")
                    yield GitPullCommand(
                        worktree=worktree,
                        rebase=rebase,
                        from_remote_branch=from_remote_branch,
                    )
    else:
        # Absent and not wanted: nothing to do.
        reasons.append(f"worktree {worktree} already absent")

    ctx.reason = "; ".join(reasons)


@dataclass(frozen=True)
class GitConfigCommand(Command):
    """``git config [--add] <key> <value>``: set or add a config item.

    The mutation ``git.config`` yields. Mirrors pyinfra's argument shape:
    the scope selector comes first -- ``git config --global`` (or
    ``--system``) for a repo-less login-user config, or
    ``cd <repo> && git config --local`` for a repository -- then ``--add``
    for a multi-valued key, then the key and the value. Every user-supplied
    operand is ``shlex``-quoted, a deliberate deviation from pyinfra, which
    quotes the key with ``QuoteString`` but wraps the value in a *literal*
    ``"<value>"`` double-quoted string. reemote5 quotes both through
    ``shlex``, so a value carrying shell metacharacters is read literally
    rather than double-quote-expanded; plain values quote to themselves, so
    command parity holds for ordinary keys and values.
    """

    key: str
    value: str
    multi_value: bool = False
    repo: str | None = None
    system: bool = False

    def shell(self) -> str:
        if self.repo is None:
            level = "--system" if self.system else "--global"
            parts = ["git", "config", level]
        else:
            parts = [
                "cd",
                shlex.quote(self.repo),
                "&&",
                "git",
                "config",
                "--local",
            ]
        if self.multi_value:
            parts.append("--add")
        parts.extend([shlex.quote(self.key), shlex.quote(self.value)])
        return " ".join(parts)


async def config(
    ctx: OpContext,
    key: str,
    value: str,
    multi_value: bool = False,
    repo: str | None = None,
    system: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Manage git config at repository, user or system level.

    Args:
        ctx (OpContext): Execution context for the operation.
        key (str): The config key to ensure.
        value (str): The value this key should have.
        multi_value (bool, optional): Add the value (``--add``) rather than
            set it, for keys that carry several values. Defaults to False.
        repo (str | None, optional): Edit this repository's ``--local``
            config; defaults to the login user's ``--global`` config when
            unset. Defaults to None.
        system (bool, optional): With no ``repo``, work at ``--system`` level
            instead of ``--global``. Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The config-set command, when the value differs.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/git/git_config.py``.
    """

    # Mirrors pyinfra's ``git.config``. Idempotence mirrors pyinfra: a
    # single-valued key is written only when its current value list is not
    # exactly ``[value]``; a ``multi_value`` key is written only when ``value``
    # is absent from the current list. Otherwise the operation is a no-op
    # carrying pyinfra's ``git config <key> is set to <value>`` reason.
    #
    # For a ``repo`` scope pyinfra reads the existing config only when
    # ``repo/.git`` is a directory (its ``files.Directory`` gate), so reemote5
    # probes the git-local ``_DirectoryExists`` fact first and reads
    # ``GitConfig`` only behind that gate; when the repository directory is
    # absent the existing config reads as ``[]`` and the write still runs,
    # exactly as pyinfra does. A repo-less scope reads ``GitConfig`` directly,
    # where ``system`` selects the ``--system`` level over the ``--global``
    # default.
    #
    # Setting a repository's ``--local`` config or the ``--system`` config
    # often needs root or the repository owner, so flag the request
    # ``sudo=True`` when the login user cannot write there.
    #
    # Deliberate deviation from pyinfra: the value is ``shlex``-quoted rather
    # than wrapped in literal double quotes (see ``GitConfigCommand``), so a
    # value carrying shell metacharacters is read literally. ``**kwargs``
    # absorbs pyinfra's global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    existing_items: list[GitConfigItem] = []
    if not repo:
        existing_items = await ctx.facts.get(GitConfig, system=system)
    elif await ctx.facts.get(_DirectoryExists, posixpath.join(repo, ".git")):
        existing_items = await ctx.facts.get(GitConfig, repo=repo)
    existing_config = {item["key"]: item["values"] for item in existing_items}

    if not multi_value and existing_config.get(key) != [value]:
        ctx.changed = True
        ctx.reason = f"git config {key} will be set to {value}"
        yield GitConfigCommand(
            key=key, value=value, multi_value=False, repo=repo, system=system
        )
    elif multi_value and value not in existing_config.get(key, []):
        ctx.changed = True
        ctx.reason = f"git config {key} will add {value}"
        yield GitConfigCommand(
            key=key, value=value, multi_value=True, repo=repo, system=system
        )
    else:
        ctx.reason = f"git config {key} is set to {value}"


@dataclass(frozen=True)
class GitCloneCommand(Command):
    """``git clone <src> [--depth N] [--branch B] <dest>``: clone a fresh repo.

    The create mutation ``git.repo`` yields when ``dest`` is not yet a
    repository. pyinfra clones *inside* an already-created ``dest`` (its
    ``files.directory`` step makes the directory first, then it runs
    ``cd dest && git clone <src> ... .``); reemote5 deliberately drops that
    ensure-directory step -- there is no ``files.directory`` operation to
    reach and namespaces never reuse each other's operations -- so this
    descriptor names ``dest`` itself instead of ``.``: ``git clone`` creates
    the target path when it does not already exist, which is exactly what
    pyinfra's ``mkdir`` step was working around. Mirrors pyinfra's argument
    order (``--depth`` before ``--branch`` before the destination). Every
    user-supplied operand is ``shlex``-quoted, a deliberate deviation from
    pyinfra's raw ``QuoteString`` interpolation -- plain URLs and paths quote
    to themselves, so command parity holds for normal values while one
    carrying shell metacharacters is read literally.
    """

    src: str
    dest: str
    branch: str | None = None
    depth: int | None = None

    def shell(self) -> str:
        parts = ["git", "clone", shlex.quote(self.src)]
        if self.depth is not None:
            parts.extend(["--depth", str(self.depth)])
        if self.branch:
            parts.extend(["--branch", shlex.quote(self.branch)])
        parts.append(shlex.quote(self.dest))
        return " ".join(parts)


@dataclass(frozen=True)
class GitRemoteSetUrlCommand(Command):
    """``cd <dest> && git remote set-url origin <url>``: re-point a clone.

    The reconciliation mutation ``git.repo`` yields when an existing
    repository's ``origin`` fetch URL -- read through ``GitConfig``'s
    ``remote.origin.url`` key, pyinfra's GH #1763 check -- is present but
    differs from the requested ``src``, so a later fetch/checkout/pull
    operates against the new source instead of silently continuing to track
    the old one. Only the ``origin`` remote is ever re-pointed, matching
    pyinfra, and the URL is ``shlex``-quoted, a deliberate deviation from
    pyinfra's raw ``QuoteString`` interpolation.
    """

    dest: str
    url: str

    def shell(self) -> str:
        return " ".join(
            [
                "cd",
                shlex.quote(self.dest),
                "&&",
                "git",
                "remote",
                "set-url",
                "origin",
                shlex.quote(self.url),
            ]
        )


@dataclass(frozen=True)
class GitFetchCommand(Command):
    """``cd <dest> && git fetch [--depth N] [--tags] [--force]``: fetch refs.

    The fetch mutation ``git.repo`` yields before a branch checkout, so the
    target branch or tag is present locally before ``GitCheckoutCommand``
    tries to check it out. Mirrors pyinfra's argument order (``--depth``
    first, then ``--tags`` when ``fetch_tags`` is set, then ``--force`` when
    ``force`` is set).
    """

    dest: str
    depth: int | None = None
    tags: bool = False
    force: bool = False

    def shell(self) -> str:
        parts = ["cd", shlex.quote(self.dest), "&&", "git", "fetch"]
        if self.depth is not None:
            parts.extend(["--depth", str(self.depth)])
        if self.tags:
            parts.append("--tags")
        if self.force:
            parts.append("--force")
        return " ".join(parts)


@dataclass(frozen=True)
class GitCheckoutCommand(Command):
    """``cd <dest> && git checkout <branch> [--force]``: switch the HEAD.

    The checkout mutation ``git.repo`` yields, either after a
    ``GitFetchCommand`` when the requested ``branch`` differs from the
    repository's current one, or on its own when ``branch`` names a tag
    (pyinfra's ``is_tag`` path, which checks the tag out detached).
    ``force`` appends ``--force``, mirroring pyinfra's ``force`` flag list.
    The branch is ``shlex``-quoted, a deliberate deviation from pyinfra's
    raw ``QuoteString`` interpolation.
    """

    dest: str
    branch: str
    force: bool = False

    def shell(self) -> str:
        parts = [
            "cd",
            shlex.quote(self.dest),
            "&&",
            "git",
            "checkout",
            shlex.quote(self.branch),
        ]
        if self.force:
            parts.append("--force")
        return " ".join(parts)


@dataclass(frozen=True)
class GitRepoPullCommand(Command):
    """``cd <dest> && git pull [--depth N] [--rebase] [--force]``: update a clone.

    The pull mutation ``git.repo`` yields for an existing repository whose
    local branch tip does not already match the remote tip (pyinfra's
    ``skip_pull`` short-circuit). Mirrors pyinfra's argument order
    (``--depth`` first, then ``--rebase``, then ``--force``). Unlike the
    ``GitPullCommand`` ``git.worktree`` yields, a repository pull never names
    a remote or branch: git resolves the checked-out branch's own upstream,
    exactly as pyinfra's ``git.repo`` does.

    The ``branch`` field is *not* rendered into the shell. It carries the
    effective branch the operation compared (``branch or current_branch``)
    so the ``SimulatedHost`` seam knows which ``git_local_commits`` row to
    fast-forward to the ``git_remote_commits`` tip, letting a repeat
    ``git.repo`` read local == remote and short-circuit as a no-op.
    """

    dest: str
    branch: str | None = None
    depth: int | None = None
    rebase: bool = False
    force: bool = False

    def shell(self) -> str:
        parts = ["cd", shlex.quote(self.dest), "&&", "git", "pull"]
        if self.depth is not None:
            parts.extend(["--depth", str(self.depth)])
        if self.rebase:
            parts.append("--rebase")
        if self.force:
            parts.append("--force")
        return " ".join(parts)


@dataclass(frozen=True)
class GitSubmoduleUpdateCommand(Command):
    """``cd <dest> && git submodule update --init [--recursive]``: sync submodules.

    The submodule mutation ``git.repo`` yields whenever ``update_submodules``
    is set, independent of whether a clone/fetch/checkout/pull also ran --
    exactly as pyinfra appends it after every other command. ``recursive``
    appends ``--recursive``, mirroring ``recursive_submodules``. The command
    is stateless from the simulation's point of view (no git fact here reads
    submodule state), so ``recursive_submodules`` only ever matters when
    ``update_submodules`` is also set, just as in pyinfra.
    """

    dest: str
    recursive: bool = False

    def shell(self) -> str:
        parts = [
            "cd",
            shlex.quote(self.dest),
            "&&",
            "git",
            "submodule",
            "update",
            "--init",
        ]
        if self.recursive:
            parts.append("--recursive")
        return " ".join(parts)


async def repo(
    ctx: OpContext,
    src: str,
    dest: str,
    branch: str | None = None,
    pull: bool = True,
    rebase: bool = False,
    update_submodules: bool = False,
    recursive_submodules: bool = False,
    depth: int | None = None,
    *,
    fetch_tags: bool = False,
    force: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Clone or update a git repository.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (str): The git source URL to clone from or keep ``origin``
            pointed at.
        dest (str): The directory to clone into / update.
        branch (str | None, optional): The branch or tag to check out.
            Defaults to None.
        pull (bool, optional): Pull changes for an existing repository.
            Defaults to True.
        rebase (bool, optional): When pulling, use ``--rebase``. Defaults to
            False.
        update_submodules (bool, optional): Run ``git submodule update
            --init`` afterwards. Defaults to False.
        recursive_submodules (bool, optional): Add ``--recursive`` to that
            submodule update. Defaults to False.
        depth (int | None, optional): Truncate clone, fetch and pull history
            to N commits (``--depth N`` on each). Defaults to None.
        fetch_tags (bool, optional): Fetch all tags (``--tags``) before a
            branch checkout. Defaults to False.
        force (bool, optional): Pass ``--force`` to fetch, checkout and pull.
            Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated clone, remote-update, fetch, checkout, pull and
            submodule-update commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/git/git_repo.py``.
    """

    # Mirrors pyinfra's ``git.repo``. The operation branches on whether
    # ``dest/.git`` is a directory (read through the git-local
    # ``_DirectoryExists`` fact), so it is idempotent in both directions:
    #
    # * Clone (not a repository yet): yields one ``GitCloneCommand``,
    #   carrying ``branch`` (``--branch``) and ``depth`` (``--depth``).
    # * Update (already a repository): reconciles ``origin`` against ``src``
    #   -- reading ``GitConfig``'s ``remote.origin.url``, pyinfra's GH #1763
    #   check -- and yields one ``GitRemoteSetUrlCommand`` only when that URL
    #   exists and differs from ``src``; then, when ``branch`` is given and
    #   differs from ``GitBranch``, yields one ``GitFetchCommand`` (carrying
    #   ``depth``/``fetch_tags``/``force``) and one ``GitCheckoutCommand``
    #   (carrying ``force``); then, when ``branch`` names a tag (found in
    #   ``GitTag``), yields a second ``GitCheckoutCommand`` detached onto it
    #   and marks the repository a tag checkout; finally, when ``pull`` is
    #   set and it is not a tag checkout, it compares ``GitLocalCommit``
    #   against ``GitRemoteBranchCommit`` for the effective branch (pyinfra's
    #   ``skip_pull``) and yields one ``GitRepoPullCommand`` (carrying
    #   ``depth``/``rebase``/``force``) only when the two differ or cannot be
    #   resolved. ``origin`` never resets the comparison when the remote URL
    #   just changed -- pyinfra skips ``skip_pull`` in that case, since the
    #   cached remote tip was gathered against the old origin.
    #
    # ``update_submodules`` is independent of all of the above and always
    # yields its ``GitSubmoduleUpdateCommand`` when set.
    #
    # Cloning, fetching, checking out, pulling and updating submodules all
    # write under ``dest``, which is often root- or another-user-owned, so
    # flag the request ``sudo=True`` when the login user cannot write there.
    #
    # Deliberate deviations from pyinfra: the ``user``/``group`` options (and
    # pyinfra's final ``chown -R`` command plus its ``safe.directory``
    # git-config prefix, which it only ever applies when ``user``/``group``
    # are set) are dropped -- reemote5 has no ``chown`` primitive and routes
    # ownership through the inventory ``privilege`` plus the ``sudo``/``su``
    # meta-parameters, the same decision already recorded for
    # ``git.worktree``. The ``ssh_keyscan`` option (pyinfra's
    # ``ssh.keyscan._inner(...)``, which reads the ``Home``/``FindInFile``
    # facts and yields ``ssh-keyscan``/``ssh-keygen -R`` commands against
    # ``~/.ssh/known_hosts``) is also dropped -- reemote5 has no ``ssh``
    # namespace or known_hosts primitive. pyinfra's initial
    # ``files.directory._inner(dest)`` ensure-directory step is dropped too
    # (no such operation exists to reuse), which is why ``GitCloneCommand``
    # names ``dest`` itself instead of cloning into ``.`` from inside a
    # pre-created directory. pyinfra's own operation fixtures still render
    # identically for every other command. Because reemote5 operations are
    # phase-unaware, an invalid ``branch``/``src`` combination is not
    # pre-validated -- git itself reports the failure when the command runs.
    # ``**kwargs`` absorbs pyinfra's global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    reasons: list[str] = []
    git_dir = posixpath.join(dest, ".git")
    is_repo = await ctx.facts.get(_DirectoryExists, git_dir)

    if not is_repo:
        ctx.changed = True
        reasons.append(f"{dest} is not a repository, will clone from {src}")
        yield GitCloneCommand(src=src, dest=dest, branch=branch, depth=depth)
    else:
        existing_config = await ctx.facts.get(GitConfig, repo=dest)
        config_values = {item["key"]: item["values"] for item in existing_config}
        existing_remote = config_values.get("remote.origin.url")
        remote_changed = existing_remote is not None and existing_remote != [src]
        if remote_changed:
            ctx.changed = True
            reasons.append(f"origin url will be set to {src}")
            yield GitRemoteSetUrlCommand(dest=dest, url=src)

        current_branch = await ctx.facts.get(GitBranch, dest)
        if branch is not None and current_branch != branch:
            ctx.changed = True
            reasons.append(f"on {current_branch or 'unknown branch'}, will switch to {branch}")
            yield GitFetchCommand(dest=dest, depth=depth, tags=fetch_tags, force=force)
            yield GitCheckoutCommand(dest=dest, branch=branch, force=force)

        tags = await ctx.facts.get(GitTag, dest)
        is_tag = bool(branch) and any(tag["name"] == branch for tag in tags)
        if is_tag:
            ctx.changed = True
            reasons.append(f"{branch} is a tag, will check it out")
            yield GitCheckoutCommand(dest=dest, branch=branch, force=force)

        if pull and not is_tag:
            skip_pull = False
            effective_branch = branch or current_branch
            if not remote_changed and effective_branch:
                local_commit = await ctx.facts.get(GitLocalCommit, dest, effective_branch)
                remote_commit = await ctx.facts.get(
                    GitRemoteBranchCommit, dest, branch=effective_branch
                )
                if local_commit and remote_commit and local_commit == remote_commit:
                    skip_pull = True
            if skip_pull:
                reasons.append(f"git repository {dest} is already up to date")
            else:
                ctx.changed = True
                reasons.append(f"git repository {dest} will be pulled")
                yield GitRepoPullCommand(
                    dest=dest,
                    branch=effective_branch,
                    depth=depth,
                    rebase=rebase,
                    force=force,
                )

    if update_submodules:
        ctx.changed = True
        reasons.append(
            f"{dest} submodules will be updated recursively"
            if recursive_submodules
            else f"{dest} submodules will be updated"
        )
        yield GitSubmoduleUpdateCommand(dest=dest, recursive=recursive_submodules)

    ctx.reason = "; ".join(reasons)


__all__ = [
    "GitBranch",
    "GitCheckoutCommand",
    "GitCloneCommand",
    "GitConfig",
    "GitConfigCommand",
    "GitConfigItem",
    "GitFetchCommand",
    "GitHeadDetached",
    "GitIsRepo",
    "GitLocalCommit",
    "GitPullCommand",
    "GitRemoteBranchCommit",
    "GitRemoteSetUrlCommand",
    "GitRemoteUrl",
    "GitRepoPullCommand",
    "GitSshKeyConfigured",
    "GitStatus",
    "GitStatusSummary",
    "GitSubmodule",
    "GitSubmoduleStatus",
    "GitSubmoduleUpdateCommand",
    "GitTag",
    "GitTagInfo",
    "GitTrackingBranch",
    "GitWorktreeAddCommand",
    "GitWorktreeRemoveCommand",
    "config",
    "repo",
    "worktree",
]
