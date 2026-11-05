# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the git module: ``GitBranch``, ``GitConfig``,
``GitHeadDetached``, ``GitIsRepo``, ``GitLocalCommit``,
``GitRemoteBranchCommit``, ``GitRemoteUrl``, ``GitSshKeyConfigured``,
``GitStatus``, ``GitSubmodule``, ``GitTag`` and ``GitTrackingBranch``,
plus the ``config``, ``repo`` and ``worktree`` operations.

Covers ``reemote5.modules.git.git``: pyinfra's own fixtures over the
real-transport path (verbatim canned outputs and rendered command
strings), parsing edge cases (the ``heads/``/``tags/`` prefix strip,
``key=value`` rows with and without ``=``, multi-valued keys, the SHA
fullmatch gate for both digest lengths, the tab-separated ``ls-remote``
row, the porcelain ``...`` upstream token with and without its trailing
status detail), empty output, the repo-path ``shlex`` quoting, the
``SimulatedHost`` seams (read, defaults, copy semantics) and the
``gather_fact`` fan-out across hosts. The six reemote5-original facts
(``GitHeadDetached``, ``GitIsRepo``, ``GitRemoteUrl``,
``GitSshKeyConfigured``, ``GitStatus`` and ``GitSubmodule``) have no
pyinfra fixture, so they are exercised with realistic canned output plus
their tri-state, state-map, summary and describe-splitting edge cases.

The ``git.config`` operation tests reuse pyinfra's own operation fixtures
(``tests/operations/git.config``): the global/system/repo scopes, the
single- and multi-value idempotence, the ``repo/.git`` directory gate,
the key/value/repo quoting, the simulated ``git_configs`` mutation, and
the end-to-end plan/apply through ``run``.

The ``git.worktree`` operation tests mirror pyinfra's create/remove/pull
directions: the ``add``/``remove``/``pull`` command renders and their
``shlex`` quoting, the existence- and tracking-branch-driven idempotence (the
``skip_pull`` short-circuit, the "not linked to a tracking branch" and "pull
is disabled" no-ops), the ``repo``/repository/``from_remote_branch`` error
paths, the simulated ``directory_stats``/``git_*`` seam mutations, and the
end-to-end add-then-noop, dry-run and ``sudo`` flows through ``run``.

The ``git.repo`` operation tests mirror pyinfra's own
``tests/operations/git.repo`` fixtures: the clone/create branch (bare, with
``--depth``/``--branch`` and with the src/branch quoting fixtures), the
existing-repository reconciliation (``remote.origin.url`` differ ->
``git remote set-url`` + pull, already-equal -> skip), the branch-switch
path (fetch + checkout, with ``fetch_tags`` and ``depth``), the
``skip_pull`` short-circuit (local commit == remote tip), the tag-detach
path (branch names a tag -> a second checkout and no pull), the
submodule-append branch (with and without ``--recursive``) and its
independence from the clone/pull outcome, the ``shlex`` quoting of the
dest/src/branch operands, the simulated clone/set-url/checkout/pull seam
mutations across ``directory_stats``/``git_configs``/``git_branches``/
``git_local_commits``/``git_remote_commits``/``git_repos``, and the
end-to-end clone-then-pull-then-noop, dry-run and ``sudo`` flows through
``run``. pyinfra's ``user``/``group``/``ssh_keyscan`` options are
intentionally dropped (see the operation's docstring), so no test asserts
them.
"""

import pytest

import reemote5
from reemote5.core import FactCache, OpContext, OperationError, op
from reemote5.drivers import (
    command_shell,
    format_log,
    format_plan,
    gather_fact,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote5.hosts import Host
from reemote5.modules import git
from reemote5.modules.git.git import (
    GitBranch,
    GitCheckoutCommand,
    GitCloneCommand,
    GitConfig,
    GitConfigCommand,
    GitFetchCommand,
    GitHeadDetached,
    GitIsRepo,
    GitLocalCommit,
    GitPullCommand,
    GitRemoteBranchCommit,
    GitRemoteSetUrlCommand,
    GitRemoteUrl,
    GitRepoPullCommand,
    GitSshKeyConfigured,
    GitStatus,
    GitSubmodule,
    GitSubmoduleUpdateCommand,
    GitTag,
    GitTrackingBranch,
    GitWorktreeAddCommand,
    GitWorktreeRemoveCommand,
)
from tests.simulated_host import SimulatedHost

# pyinfra's own fixture SHA (tests/facts/git.GitLocalCommit,
# git.GitRemoteBranchCommit) and a 64-character SHA-256 digest.
SHA40 = "1a2b3c4d5e6f7890abcdef1234567890abcdef12"
SHA64 = "ab" * 32


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.shells: list[str] = []

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        self.shells.append(shell)
        return self._stdout


# --- GitBranch ---------------------------------------------------------------


async def test_git_branch_parses_pyinfras_fixture():
    # pyinfra's own fact fixture (tests/facts/git.GitBranch): the heads/
    # prefix is stripped from the `git describe --all` answer, and the
    # rendered command matches the fixture's verbatim (shlex.quote leaves
    # the plain "myrepo" untouched).
    host = CannedShellHost("heads/develop\n")
    assert await GitBranch.process(host, "myrepo") == "develop"
    assert host.shells == ["! test -d myrepo || (cd myrepo && git describe --all)"]


def test_git_branch_parse_strips_both_prefixes():
    # A tagged commit answers tags/<name>; the prefix is stripped from the
    # joined output exactly like pyinfra's re.sub.
    assert GitBranch.parse(["heads/main"]) == "main"
    assert GitBranch.parse(["tags/v1.0.0"]) == "v1.0.0"
    # Empty output (the short-circuited directory guard) reads as "".
    assert GitBranch.parse([]) == ""


def test_git_branch_command_quotes_the_repo_path():
    # A repo path carrying shell metacharacters is read literally.
    assert GitBranch.command("my repo") == (
        "! test -d 'my repo' || (cd 'my repo' && git describe --all)"
    )


async def test_git_branch_reads_the_simulated_seam():
    host = SimulatedHost(name="h", git_branches={"/srv/app": "develop"})
    assert await GitBranch.process(host, "/srv/app") == "develop"
    # An unknown repo reads as "", like the short-circuited guard; the
    # seam defaults to {}: a simulated machine with no repositories.
    assert await GitBranch.process(host, "/srv/other") == ""
    assert await GitBranch.process(SimulatedHost(name="h"), "/srv/app") == ""


async def test_git_branch_gathers_across_hosts():
    # gather_fact forwards the repo argument to every host's process and
    # keys the results by host name.
    hosts = [
        SimulatedHost(name="web1", git_branches={"/srv/app": "main"}),
        SimulatedHost(name="web2", git_branches={"/srv/app": "develop"}),
    ]
    assert await gather_fact(hosts, GitBranch, "/srv/app") == {
        "web1": "main",
        "web2": "develop",
    }


# --- GitConfig ---------------------------------------------------------------


async def test_git_config_parses_pyinfras_global_fixture():
    # pyinfra's own fact fixture (tests/facts/git.GitConfig, global scope):
    # the rendered command matches verbatim and the key -> value-list dict
    # pyinfra returns is projected onto GitConfigItem entries.
    host = CannedShellHost("user.email=nick@gmail.com\n")
    assert await GitConfig.process(host) == [
        {"key": "user.email", "values": ["nick@gmail.com"]}
    ]
    assert host.shells == ["git config --global -l || true"]


async def test_git_config_system_scope_renders_the_system_flag():
    host = CannedShellHost("")
    assert await GitConfig.process(host, system=True) == []
    assert host.shells == ["git config --system -l || true"]


async def test_git_config_parses_pyinfras_multi_value_fixture():
    # pyinfra's multi-valued fixture: a repeated key (safe.directory)
    # keeps every value in listing order.
    host = CannedShellHost(
        """safe.directory=/a/safe/repo
safe.directory=/another/safe/repo
"""
    )
    assert await GitConfig.process(host) == [
        {"key": "safe.directory", "values": ["/a/safe/repo", "/another/safe/repo"]}
    ]


async def test_git_config_parses_pyinfras_repo_fixture():
    # pyinfra's repo-scoped fixture: the --local read behind the directory
    # guard, rendered verbatim.
    host = CannedShellHost("user.email=nick@gmail.com\n")
    assert await GitConfig.process(host, "myrepo") == [
        {"key": "user.email", "values": ["nick@gmail.com"]}
    ]
    assert host.shells == [
        "! test -d myrepo || (cd myrepo && git config --local -l)"
    ]


def test_git_config_parse_splits_on_the_first_equals_only():
    # A value may itself carry "="; rows without one are skipped (pyinfra
    # would raise ValueError on them -- reemote5 hardens the parser).
    assert GitConfig.parse(
        [
            "url.https://example.com/.insteadof=git@example.com:",
            "no equals sign",
            "user.name=nick",
        ]
    ) == [
        {"key": "url.https://example.com/.insteadof", "values": ["git@example.com:"]},
        {"key": "user.name", "values": ["nick"]},
    ]
    # Empty output (missing config, swallowed by `|| true`) reads as [],
    # pyinfra's ``default = dict`` projected onto entries.
    assert GitConfig.parse([]) == []


def test_git_config_command_quotes_the_repo_path():
    assert GitConfig.command("my repo") == (
        "! test -d 'my repo' || (cd 'my repo' && git config --local -l)"
    )


async def test_git_config_reads_the_simulated_seam():
    host = SimulatedHost(
        name="h",
        git_configs={
            "/srv/app": {"user.email": ["app@example.com"]},
            "global": {"user.name": ["kim"]},
            "system": {"core.pager": ["less"]},
        },
    )
    # Repo scope, global scope and system scope each read their own key,
    # projected from the mutated dict store into entry lists.
    assert await GitConfig.process(host, "/srv/app") == [
        {"key": "user.email", "values": ["app@example.com"]}
    ]
    assert await GitConfig.process(host) == [{"key": "user.name", "values": ["kim"]}]
    assert await GitConfig.process(host, system=True) == [
        {"key": "core.pager", "values": ["less"]}
    ]
    # An unknown scope reads as []; the seam defaults to {} everywhere.
    assert await GitConfig.process(host, "/srv/other") == []
    assert await GitConfig.process(SimulatedHost(name="h")) == []


async def test_git_config_seam_returns_a_copy():
    host = SimulatedHost(
        name="h", git_configs={"/srv/app": {"user.email": ["app@example.com"]}}
    )
    config = await GitConfig.process(host, "/srv/app")
    config[0]["values"].append("mutated@example.com")
    config.append({"key": "extra", "values": ["leak"]})
    # The simulated state is untouched: both the entry dicts and the value
    # lists are copied on read.
    assert host.git_configs["/srv/app"] == {"user.email": ["app@example.com"]}


# --- GitHeadDetached ---------------------------------------------------------


async def test_git_head_detached_reads_an_attached_head():
    # A reemote5-original fact: on a branch `git symbolic-ref -q HEAD`
    # succeeds, which the wrapped probe reports as "attached" -> False.
    host = CannedShellHost("attached\n")
    assert await GitHeadDetached.process(host, "myrepo") is False
    assert host.shells == [
        (
            "! test -d myrepo || (cd myrepo && "
            "if git symbolic-ref -q HEAD >/dev/null 2>&1; then echo attached; "
            "elif git rev-parse --git-dir >/dev/null 2>&1; then echo detached; fi)"
        )
    ]


async def test_git_head_detached_reads_a_detached_head():
    # A tag or bare-commit checkout leaves HEAD detached -> True.
    assert (
        await GitHeadDetached.process(CannedShellHost("detached\n"), "myrepo")
        is True
    )


def test_git_head_detached_parse_maps_the_tri_state():
    assert GitHeadDetached.parse(["attached"]) is False
    assert GitHeadDetached.parse(["detached"]) is True
    # No token -- a missing repo (the guard) or a non-repo directory (both
    # probes fail) -- reads as None, never mistaken for a detached HEAD.
    assert GitHeadDetached.parse([]) is None
    assert GitHeadDetached.parse([""]) is None
    assert GitHeadDetached.parse(["garbage"]) is None


def test_git_head_detached_command_quotes_the_repo_path():
    assert GitHeadDetached.command("my repo") == (
        "! test -d 'my repo' || (cd 'my repo' && "
        "if git symbolic-ref -q HEAD >/dev/null 2>&1; then echo attached; "
        "elif git rev-parse --git-dir >/dev/null 2>&1; then echo detached; fi)"
    )


async def test_git_head_detached_reads_the_simulated_seam():
    host = SimulatedHost(
        name="h", git_head_detached={"/srv/app": True, "/srv/clean": False}
    )
    assert await GitHeadDetached.process(host, "/srv/app") is True
    assert await GitHeadDetached.process(host, "/srv/clean") is False
    # An unknown repo reads as None (undetermined); the seam defaults to {}.
    assert await GitHeadDetached.process(host, "/srv/other") is None
    assert (
        await GitHeadDetached.process(SimulatedHost(name="h"), "/srv/app") is None
    )


# --- GitIsRepo ---------------------------------------------------------------


async def test_git_is_repo_reads_a_valid_work_tree():
    # A reemote5-original fact: `git rev-parse --is-inside-work-tree` prints
    # "true" in a valid work tree -> True.
    host = CannedShellHost("true\n")
    assert await GitIsRepo.process(host, "myrepo") is True
    assert host.shells == [
        (
            "! test -d myrepo || "
            "(cd myrepo && git rev-parse --is-inside-work-tree 2>/dev/null)"
        )
    ]


def test_git_is_repo_parse_accepts_only_the_true_token():
    assert GitIsRepo.parse(["true"]) is True
    # A bare repo prints "false"; a non-repo or a missing directory prints
    # nothing (2>/dev/null swallows git's fatal:); both read as False.
    assert GitIsRepo.parse(["false"]) is False
    assert GitIsRepo.parse([]) is False
    assert GitIsRepo.parse([""]) is False
    assert GitIsRepo.parse(["fatal: not a git repository"]) is False


def test_git_is_repo_command_quotes_the_repo_path():
    assert GitIsRepo.command("my repo") == (
        "! test -d 'my repo' || "
        "(cd 'my repo' && git rev-parse --is-inside-work-tree 2>/dev/null)"
    )


async def test_git_is_repo_reads_the_simulated_seam():
    host = SimulatedHost(name="h", git_repos={"/srv/app"})
    assert await GitIsRepo.process(host, "/srv/app") is True
    # A path absent from the set reads as False; the seam defaults to empty.
    assert await GitIsRepo.process(host, "/srv/other") is False
    assert await GitIsRepo.process(SimulatedHost(name="h"), "/srv/app") is False


# --- GitLocalCommit ----------------------------------------------------------


async def test_git_local_commit_parses_pyinfras_ref_fixture():
    # pyinfra's own fact fixture (tests/facts/git.GitLocalCommit, explicit
    # ref): the rendered command and the SHA answer match verbatim.
    host = CannedShellHost(f"{SHA40}\n")
    assert await GitLocalCommit.process(host, "myrepo", "mybranch") == SHA40
    assert host.shells == [
        "! test -d myrepo || (cd myrepo && git rev-parse mybranch 2>/dev/null) || true"
    ]


async def test_git_local_commit_parses_pyinfras_default_head_fixture():
    # pyinfra's default-ref fixture: ref defaults to HEAD.
    host = CannedShellHost(f"{SHA40}\n")
    assert await GitLocalCommit.process(host, "myrepo") == SHA40
    assert host.shells == [
        "! test -d myrepo || (cd myrepo && git rev-parse HEAD 2>/dev/null) || true"
    ]


async def test_git_local_commit_is_none_on_empty_output():
    # pyinfra's two empty-output fixtures: a missing repo or an unknown
    # ref both read as None.
    assert await GitLocalCommit.process(CannedShellHost(""), "myrepo") is None
    assert (
        await GitLocalCommit.process(CannedShellHost(""), "myrepo", "v34.0.1")
        is None
    )


def test_git_local_commit_parse_gates_on_the_sha_shape():
    # A 64-character SHA-256 digest passes the fullmatch too; anything
    # else -- an error message, a short digest, a trailing token -- None.
    assert GitLocalCommit.parse([SHA64]) == SHA64
    assert GitLocalCommit.parse([SHA40]) == SHA40
    assert GitLocalCommit.parse(["fatal: ambiguous argument"]) is None
    assert GitLocalCommit.parse([SHA40[:39]]) is None
    # Only the first line is considered, exactly like pyinfra.
    assert GitLocalCommit.parse([f"{SHA40}", "extra"]) == SHA40
    assert GitLocalCommit.parse([]) is None


def test_git_local_commit_command_quotes_repo_and_ref():
    assert GitLocalCommit.command("my repo", "my branch") == (
        "! test -d 'my repo' || (cd 'my repo' "
        "&& git rev-parse 'my branch' 2>/dev/null) || true"
    )


async def test_git_local_commit_reads_the_simulated_seam():
    host = SimulatedHost(name="h", git_local_commits={"/srv/app": {"HEAD": SHA40}})
    assert await GitLocalCommit.process(host, "/srv/app") == SHA40
    # An unknown ref, repo or an unseeded host reads as None, like the
    # swallowed rev-parse failure.
    assert await GitLocalCommit.process(host, "/srv/app", "mybranch") is None
    assert await GitLocalCommit.process(host, "/srv/other") is None
    assert await GitLocalCommit.process(SimulatedHost(name="h"), "/srv/app") is None


# --- GitRemoteBranchCommit ---------------------------------------------------


async def test_git_remote_branch_commit_parses_pyinfras_fixture():
    # pyinfra's own fact fixture (tests/facts/git.GitRemoteBranchCommit):
    # the tab-separated ls-remote row keeps only the SHA column, and the
    # rendered command matches verbatim.
    host = CannedShellHost(f"{SHA40}\trefs/heads/main\n")
    assert (
        await GitRemoteBranchCommit.process(host, "myrepo", branch="main") == SHA40
    )
    assert host.shells == [
        (
            "! test -d myrepo || "
            "(cd myrepo && git ls-remote origin main 2>/dev/null | head -n1)"
        )
    ]


async def test_git_remote_branch_commit_defaults_to_the_remote_head():
    # A falsy branch asks for the remote's HEAD (pyinfra's truthiness
    # check), on the real transport and on the seam alike.
    host = CannedShellHost(f"{SHA40}\tHEAD\n")
    assert await GitRemoteBranchCommit.process(host, "myrepo") == SHA40
    assert host.shells == [
        (
            "! test -d myrepo || "
            "(cd myrepo && git ls-remote origin HEAD 2>/dev/null | head -n1)"
        )
    ]


async def test_git_remote_branch_commit_is_none_on_empty_output():
    # pyinfra's empty-output fixture: an unreachable remote or an unknown
    # branch reads as None.
    assert (
        await GitRemoteBranchCommit.process(
            CannedShellHost(""), "myrepo", branch="main"
        )
        is None
    )


def test_git_remote_branch_commit_parse_gates_on_the_sha_shape():
    # The first tab-separated column must fullmatch the digest shape.
    assert GitRemoteBranchCommit.parse([f"{SHA64}\trefs/heads/main"]) == SHA64
    assert GitRemoteBranchCommit.parse(["not-a-sha\trefs/heads/main"]) is None
    # A row without a tab is just its single column.
    assert GitRemoteBranchCommit.parse([SHA40]) == SHA40
    assert GitRemoteBranchCommit.parse([]) is None


def test_git_remote_branch_commit_command_quotes_every_operand():
    assert GitRemoteBranchCommit.command("my repo", "my remote", "my branch") == (
        "! test -d 'my repo' || (cd 'my repo' "
        "&& git ls-remote 'my remote' 'my branch' 2>/dev/null | head -n1)"
    )


async def test_git_remote_branch_commit_reads_the_simulated_seam():
    host = SimulatedHost(
        name="h",
        git_remote_commits={
            "/srv/app": {
                "origin": {"main": SHA40, "HEAD": SHA64},
                "upstream": {"main": SHA64},
            }
        },
    )
    assert (
        await GitRemoteBranchCommit.process(host, "/srv/app", branch="main") == SHA40
    )
    assert await GitRemoteBranchCommit.process(host, "/srv/app") == SHA64
    assert (
        await GitRemoteBranchCommit.process(host, "/srv/app", "upstream", "main")
        == SHA64
    )
    # An unknown repo, remote or branch reads as None; so does a falsy
    # branch keyed as "HEAD" on a remote without it.
    assert (
        await GitRemoteBranchCommit.process(host, "/srv/other", branch="main")
        is None
    )
    assert (
        await GitRemoteBranchCommit.process(host, "/srv/app", "fork", "main") is None
    )
    assert (
        await GitRemoteBranchCommit.process(SimulatedHost(name="h"), "/srv/app")
        is None
    )


# --- GitRemoteUrl ------------------------------------------------------------


async def test_git_remote_url_reads_the_fetch_url():
    # A reemote5-original fact: `git remote get-url origin` prints the fetch
    # URL behind the directory guard.
    host = CannedShellHost("git@github.com:acme/app.git\n")
    assert await GitRemoteUrl.process(host, "myrepo") == "git@github.com:acme/app.git"
    assert host.shells == [
        "! test -d myrepo || (cd myrepo && git remote get-url origin 2>/dev/null)"
    ]


async def test_git_remote_url_renders_the_push_flag_and_remote():
    host = CannedShellHost("https://github.com/acme/app.git\n")
    assert (
        await GitRemoteUrl.process(host, "myrepo", "upstream", push=True)
        == "https://github.com/acme/app.git"
    )
    assert host.shells == [
        (
            "! test -d myrepo || "
            "(cd myrepo && git remote get-url --push upstream 2>/dev/null)"
        )
    ]


def test_git_remote_url_parse_keeps_the_first_non_blank_line():
    assert GitRemoteUrl.parse(["git@github.com:acme/app.git"]) == (
        "git@github.com:acme/app.git"
    )
    # An unknown remote (error: No such remote, swallowed by 2>/dev/null) or a
    # missing repo prints nothing -> None.
    assert GitRemoteUrl.parse([]) is None
    assert GitRemoteUrl.parse([""]) is None


def test_git_remote_url_command_quotes_repo_and_remote():
    assert GitRemoteUrl.command("my repo", "my remote") == (
        "! test -d 'my repo' || "
        "(cd 'my repo' && git remote get-url 'my remote' 2>/dev/null)"
    )


async def test_git_remote_url_reads_the_simulated_seam():
    host = SimulatedHost(
        name="h",
        git_remote_urls={
            "/srv/app": {
                "origin": {
                    "fetch": "git@github.com:acme/app.git",
                    "push": "ssh://git@push.example.com/acme/app.git",
                }
            }
        },
    )
    assert (
        await GitRemoteUrl.process(host, "/srv/app")
        == "git@github.com:acme/app.git"
    )
    # push=True resolves the "push" kind, a distinct URL from the fetch one.
    assert await GitRemoteUrl.process(host, "/srv/app", push=True) == (
        "ssh://git@push.example.com/acme/app.git"
    )
    # An unknown repo, remote or kind reads as None.
    assert await GitRemoteUrl.process(host, "/srv/app", "fork") is None
    assert await GitRemoteUrl.process(host, "/srv/other") is None
    assert await GitRemoteUrl.process(SimulatedHost(name="h"), "/srv/app") is None


async def test_git_remote_url_gathers_across_hosts():
    # gather_fact forwards the repo argument to every host's process.
    hosts = [
        SimulatedHost(
            name="web1",
            git_remote_urls={"/srv/app": {"origin": {"fetch": "git@h1:app.git"}}},
        ),
        SimulatedHost(
            name="web2",
            git_remote_urls={"/srv/app": {"origin": {"fetch": "git@h2:app.git"}}},
        ),
    ]
    assert await gather_fact(hosts, GitRemoteUrl, "/srv/app") == {
        "web1": "git@h1:app.git",
        "web2": "git@h2:app.git",
    }


# --- GitSshKeyConfigured -----------------------------------------------------


async def test_git_ssh_key_configured_reads_an_authenticated_probe():
    # A reemote5-original fact, and the one git fact that is not repo-scoped:
    # the in-shell probe normalizes ssh's exit status to a single token, so a
    # successful handshake (any status but 255) prints "authenticated".
    host = CannedShellHost("authenticated\n")
    assert await GitSshKeyConfigured.process(host, "github.com") is True
    assert host.shells == [
        (
            "ssh -T -o BatchMode=yes -o StrictHostKeyChecking=accept-new "
            "-o ConnectTimeout=10 git@github.com >/dev/null 2>&1; "
            'rc=$?; test "$rc" -eq 127 && exit 127; '
            'test "$rc" -ne 255 && echo authenticated || echo unauthenticated'
        )
    ]


async def test_git_ssh_key_configured_reads_an_unauthenticated_probe():
    assert (
        await GitSshKeyConfigured.process(
            CannedShellHost("unauthenticated\n"), "github.com"
        )
        is False
    )


def test_git_ssh_key_configured_parse_maps_the_token():
    assert GitSshKeyConfigured.parse(["authenticated"]) is True
    assert GitSshKeyConfigured.parse(["unauthenticated"]) is False
    # No output at all reads as False (never authenticated by default).
    assert GitSshKeyConfigured.parse([]) is False
    assert GitSshKeyConfigured.parse([""]) is False


def test_git_ssh_key_configured_command_renders_user_and_timeout():
    assert GitSshKeyConfigured.command("github.com", "deploy", 5) == (
        "ssh -T -o BatchMode=yes -o StrictHostKeyChecking=accept-new "
        "-o ConnectTimeout=5 deploy@github.com >/dev/null 2>&1; "
        'rc=$?; test "$rc" -eq 127 && exit 127; '
        'test "$rc" -ne 255 && echo authenticated || echo unauthenticated'
    )


def test_git_ssh_key_configured_command_quotes_a_target_needing_it():
    # The user@host target is quoted as one shell word when it carries a
    # metacharacter (a space here).
    assert "'git@my host'" in GitSshKeyConfigured.command("my host")


async def test_git_ssh_key_configured_reads_the_simulated_seam():
    host = SimulatedHost(name="h", git_ssh_authenticated={"git@github.com"})
    # The default user is git, so the probed target is "git@github.com".
    assert await GitSshKeyConfigured.process(host, "github.com") is True
    # A different user builds a different target, absent from the set -> False.
    assert await GitSshKeyConfigured.process(host, "github.com", "deploy") is False
    # An unseeded host authenticates nothing.
    assert (
        await GitSshKeyConfigured.process(SimulatedHost(name="h"), "github.com")
        is False
    )


# --- GitStatus ---------------------------------------------------------------


async def test_git_status_reads_a_dirty_work_tree():
    # A reemote5-original fact: `git status --porcelain` rows fold into the
    # staged/unstaged/untracked summary behind the directory guard.
    host = CannedShellHost(" M app.py\n?? new.txt\n")
    assert await GitStatus.process(host, "myrepo") == {
        "dirty": True,
        "staged": [],
        "unstaged": ["app.py"],
        "untracked": ["new.txt"],
    }
    assert host.shells == [
        "! test -d myrepo || (cd myrepo && git status --porcelain)"
    ]


def test_git_status_parse_classifies_every_column_pair():
    assert GitStatus.parse(
        [
            "M  staged.py",
            " M unstaged.py",
            "MM both.py",
            "?? new.txt",
            "A  added.py",
            "R  old.py -> new.py",
        ]
    ) == {
        "dirty": True,
        "staged": ["staged.py", "both.py", "added.py", "old.py -> new.py"],
        "unstaged": ["unstaged.py", "both.py"],
        "untracked": ["new.txt"],
    }


def test_git_status_parse_of_a_clean_tree_is_not_dirty():
    # Empty output -- a clean repo or the short-circuited guard -- is a clean
    # summary, never dirty.
    assert GitStatus.parse([]) == {
        "dirty": False,
        "staged": [],
        "unstaged": [],
        "untracked": [],
    }


def test_git_status_command_quotes_the_repo_path():
    assert GitStatus.command("my repo") == (
        "! test -d 'my repo' || (cd 'my repo' && git status --porcelain)"
    )


async def test_git_status_reads_the_simulated_seam():
    host = SimulatedHost(
        name="h",
        git_statuses={
            "/srv/app": {
                "dirty": True,
                "staged": [],
                "unstaged": ["app.py"],
                "untracked": [],
            }
        },
    )
    assert await GitStatus.process(host, "/srv/app") == {
        "dirty": True,
        "staged": [],
        "unstaged": ["app.py"],
        "untracked": [],
    }
    clean = {"dirty": False, "staged": [], "unstaged": [], "untracked": []}
    # An unknown repo reads as a clean summary; the seam defaults to {}.
    assert await GitStatus.process(host, "/srv/other") == clean
    assert await GitStatus.process(SimulatedHost(name="h"), "/srv/app") == clean


async def test_git_status_seam_returns_a_copy():
    host = SimulatedHost(
        name="h",
        git_statuses={
            "/srv/app": {
                "dirty": True,
                "staged": ["a.py"],
                "unstaged": [],
                "untracked": ["b.py"],
            }
        },
    )
    status = await GitStatus.process(host, "/srv/app")
    status["staged"].append("mutated.py")
    status["untracked"] = ["leak"]
    # The simulated state is untouched: the summary and its lists are copied.
    assert host.git_statuses["/srv/app"] == {
        "dirty": True,
        "staged": ["a.py"],
        "unstaged": [],
        "untracked": ["b.py"],
    }


# --- GitSubmodule ------------------------------------------------------------


async def test_git_submodule_reads_the_rows():
    # A reemote5-original fact: each `git submodule status` row folds into a
    # path/sha/state/describe dict behind the directory guard.
    host = CannedShellHost(f" {SHA40} vendor/foo (heads/main)\n")
    assert await GitSubmodule.process(host, "myrepo") == [
        {
            "path": "vendor/foo",
            "sha": SHA40,
            "state": "synced",
            "describe": "heads/main",
        }
    ]
    assert host.shells == [
        "! test -d myrepo || (cd myrepo && git submodule status)"
    ]


def test_git_submodule_parse_maps_every_state_prefix():
    assert GitSubmodule.parse(
        [
            f" {SHA40} vendor/synced (heads/main)",
            f"-{SHA40} vendor/uninit",
            f"+{SHA64} vendor/modified (v1.0-3-gabc)",
            f"U{SHA40} vendor/conflict (desc)",
        ]
    ) == [
        {
            "path": "vendor/synced",
            "sha": SHA40,
            "state": "synced",
            "describe": "heads/main",
        },
        {
            "path": "vendor/uninit",
            "sha": SHA40,
            "state": "uninitialized",
            "describe": None,
        },
        {
            "path": "vendor/modified",
            "sha": SHA64,
            "state": "modified",
            "describe": "v1.0-3-gabc",
        },
        {
            "path": "vendor/conflict",
            "sha": SHA40,
            "state": "conflicted",
            "describe": "desc",
        },
    ]


def test_git_submodule_parse_of_an_unknown_prefix_and_empty():
    # An unrecognized leading character (a future git state) reads as
    # "unknown" rather than raising; empty output is [].
    assert GitSubmodule.parse([f"?{SHA40} vendor/x"]) == [
        {"path": "vendor/x", "sha": SHA40, "state": "unknown", "describe": None}
    ]
    assert GitSubmodule.parse([]) == []


def test_git_submodule_parse_splits_the_describe_on_the_last_paren():
    # The describe is split off the LAST " (", so a path that itself contains
    # " (" stays whole and only the trailing detail becomes the describe.
    assert GitSubmodule.parse([f" {SHA40} vendor/lib (old) (heads/main)"]) == [
        {
            "path": "vendor/lib (old)",
            "sha": SHA40,
            "state": "synced",
            "describe": "heads/main",
        }
    ]


def test_git_submodule_command_quotes_the_repo_path():
    assert GitSubmodule.command("my repo") == (
        "! test -d 'my repo' || (cd 'my repo' && git submodule status)"
    )


async def test_git_submodule_reads_the_simulated_seam():
    host = SimulatedHost(
        name="h",
        git_submodules={
            "/srv/app": [
                {
                    "path": "vendor/foo",
                    "sha": SHA40,
                    "state": "synced",
                    "describe": "heads/main",
                }
            ]
        },
    )
    assert await GitSubmodule.process(host, "/srv/app") == [
        {
            "path": "vendor/foo",
            "sha": SHA40,
            "state": "synced",
            "describe": "heads/main",
        }
    ]
    # An unknown repo reads as []; the seam defaults to {}.
    assert await GitSubmodule.process(host, "/srv/other") == []
    assert (
        await GitSubmodule.process(SimulatedHost(name="h"), "/srv/app") == []
    )


async def test_git_submodule_seam_returns_a_copy():
    host = SimulatedHost(
        name="h",
        git_submodules={
            "/srv/app": [
                {
                    "path": "vendor/foo",
                    "sha": SHA40,
                    "state": "synced",
                    "describe": None,
                }
            ]
        },
    )
    rows = await GitSubmodule.process(host, "/srv/app")
    rows[0]["state"] = "mutated"
    rows.append({"path": "leak", "sha": "", "state": "", "describe": None})
    # The simulated state is untouched: each row dict is copied on read.
    assert host.git_submodules["/srv/app"] == [
        {
            "path": "vendor/foo",
            "sha": SHA40,
            "state": "synced",
            "describe": None,
        }
    ]


# --- GitTag ------------------------------------------------------------------


async def test_git_tag_parses_pyinfras_fixture():
    # pyinfra's own fact fixture (tests/facts/git.GitTag): each tag line
    # becomes one GitTagInfo entry, in listing order.
    host = CannedShellHost(
        """v1.0.0
v1.0.1
"""
    )
    assert await GitTag.process(host, "myrepo") == [
        {"name": "v1.0.0"},
        {"name": "v1.0.1"},
    ]
    assert host.shells == ["! test -d myrepo || (cd myrepo && git tag)"]


def test_git_tag_parse_keeps_every_line_verbatim():
    assert GitTag.parse(["b-tag", "a-tag"]) == [{"name": "b-tag"}, {"name": "a-tag"}]
    # Empty output (the short-circuited guard, or a tagless repo) is [].
    assert GitTag.parse([]) == []


def test_git_tag_command_quotes_the_repo_path():
    assert GitTag.command("my repo") == (
        "! test -d 'my repo' || (cd 'my repo' && git tag)"
    )


async def test_git_tag_reads_the_simulated_seam():
    host = SimulatedHost(
        name="h", git_tags={"/srv/app": [{"name": "v1.0.0"}, {"name": "v1.0.1"}]}
    )
    assert await GitTag.process(host, "/srv/app") == [
        {"name": "v1.0.0"},
        {"name": "v1.0.1"},
    ]
    # An unknown repo reads as []; the seam defaults to {} everywhere.
    assert await GitTag.process(host, "/srv/other") == []
    assert await GitTag.process(SimulatedHost(name="h"), "/srv/app") == []


async def test_git_tag_seam_returns_a_copy():
    host = SimulatedHost(name="h", git_tags={"/srv/app": [{"name": "v1.0.0"}]})
    tags = await GitTag.process(host, "/srv/app")
    tags[0]["name"] = "mutated"
    tags.append({"name": "v2.0.0"})
    # The simulated state is untouched: each entry dict is copied on read.
    assert host.git_tags["/srv/app"] == [{"name": "v1.0.0"}]


# --- GitTrackingBranch -------------------------------------------------------


async def test_git_tracking_branch_parses_pyinfras_status_detail_fixture():
    # pyinfra's own fact fixture (tests/facts/git.GitTrackingBranch): the
    # upstream token is read even with the trailing [behind 692] detail.
    host = CannedShellHost("## branch1...origin/master [behind 692]\n")
    assert await GitTrackingBranch.process(host, "myrepo") == "origin/master"
    assert host.shells == [
        "! test -d myrepo || (cd myrepo && git status --branch --porcelain)"
    ]


async def test_git_tracking_branch_parses_pyinfras_plain_fixture():
    # pyinfra's fixture without the status detail.
    host = CannedShellHost("## branch1...origin/master\n")
    assert await GitTrackingBranch.process(host, "myrepo") == "origin/master"


async def test_git_tracking_branch_parses_pyinfras_no_upstream_fixture():
    # pyinfra's fixture for a branch without an upstream: no "..." token,
    # so None. Changed-file rows after the header are never searched.
    host = CannedShellHost("## branch1\n")
    assert await GitTrackingBranch.process(host, "myrepo") is None
    assert GitTrackingBranch.parse(["## branch1", " M file...origin/x"]) is None
    # Empty output (the short-circuited guard) reads as None too.
    assert GitTrackingBranch.parse([]) is None


def test_git_tracking_branch_command_quotes_the_repo_path():
    assert GitTrackingBranch.command("my repo") == (
        "! test -d 'my repo' || "
        "(cd 'my repo' && git status --branch --porcelain)"
    )


async def test_git_tracking_branch_reads_the_simulated_seam():
    host = SimulatedHost(
        name="h", git_tracking_branches={"/srv/app": "origin/master"}
    )
    assert await GitTrackingBranch.process(host, "/srv/app") == "origin/master"
    # An unknown repo reads as None, like a branch without an upstream;
    # the seam defaults to {} everywhere.
    assert await GitTrackingBranch.process(host, "/srv/other") is None
    assert (
        await GitTrackingBranch.process(SimulatedHost(name="h"), "/srv/app") is None
    )


# --- Namespace exports -------------------------------------------------------


def test_git_namespace_exports_every_fact():
    # The package, the module and the top-level package all reach the
    # same twelve fact classes and the four TypedDict result shapes.
    for name in (
        "GitBranch",
        "GitConfig",
        "GitConfigItem",
        "GitHeadDetached",
        "GitIsRepo",
        "GitLocalCommit",
        "GitRemoteBranchCommit",
        "GitRemoteUrl",
        "GitSshKeyConfigured",
        "GitStatus",
        "GitStatusSummary",
        "GitSubmodule",
        "GitSubmoduleStatus",
        "GitTag",
        "GitTagInfo",
        "GitTrackingBranch",
    ):
        module_cls = getattr(git.git, name)
        assert getattr(git, name) is module_cls
        assert getattr(reemote5, name) is module_cls
    assert reemote5.git is git


def test_git_namespace_exports_the_config_operation_and_descriptor():
    # The operation is reached through the namespace and the package
    # (git.config), never the top level; the descriptor is re-exported at
    # all three sites like the other command descriptors.
    assert git.config is git.git.config
    assert git.GitConfigCommand is git.git.GitConfigCommand
    assert reemote5.GitConfigCommand is git.git.GitConfigCommand
    assert not hasattr(reemote5, "config")


def test_git_namespace_exports_the_worktree_operation_and_descriptors():
    # ``git.worktree`` is reached through the namespace and the package, never
    # the top level; its three command descriptors are re-exported at all
    # three sites like the other descriptors.
    assert git.worktree is git.git.worktree
    for name in (
        "GitWorktreeAddCommand",
        "GitWorktreeRemoveCommand",
        "GitPullCommand",
    ):
        module_cls = getattr(git.git, name)
        assert getattr(git, name) is module_cls
        assert getattr(reemote5, name) is module_cls
    assert not hasattr(reemote5, "worktree")


# --- config operation --------------------------------------------------------

# pyinfra's git.config fixtures render the scope selector first, then --add
# for a multi-valued key, then the key and value. reemote5 shlex-quotes the
# value (pyinfra wraps it in literal double quotes), so a plain value renders
# bare -- command parity holds for ordinary keys and values.
GIT_CONFIG_GLOBAL_SHELL = "git config --global key value"
GIT_CONFIG_SYSTEM_SHELL = "git config --system key value"
GIT_CONFIG_ADD_SHELL = "git config --global --add key value"
GIT_CONFIG_LOCAL_SHELL = "cd /my/repo && git config --local key value"


async def materialize_config(host: Host, **kwargs):
    """Run ``git.config`` against a fresh cache; return ``(ctx, commands)``."""
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in git.config(ctx, **kwargs)]
    return ctx, commands


def test_git_config_command_renders_the_scopes():
    assert GitConfigCommand(key="key", value="value").shell() == GIT_CONFIG_GLOBAL_SHELL
    assert (
        GitConfigCommand(key="key", value="value", system=True).shell()
        == GIT_CONFIG_SYSTEM_SHELL
    )
    assert (
        GitConfigCommand(key="key", value="value", multi_value=True).shell()
        == GIT_CONFIG_ADD_SHELL
    )
    assert (
        GitConfigCommand(key="key", value="value", repo="/my/repo").shell()
        == GIT_CONFIG_LOCAL_SHELL
    )


def test_git_config_command_quotes_key_value_and_repo():
    # pyinfra's set_key_needs_quotes / set_repo_needs_quotes /
    # set_multivalue_needs_quotes fixtures: a key or repo carrying shell
    # metacharacters is shlex-quoted, so it is read literally rather than
    # executed.
    assert (
        GitConfigCommand(
            key="user.name; whoami", value="Anon", repo="/my/repo"
        ).shell()
        == "cd /my/repo && git config --local 'user.name; whoami' Anon"
    )
    assert (
        GitConfigCommand(
            key="user.name", value="Anon", repo="/my repo; whoami"
        ).shell()
        == "cd '/my repo; whoami' && git config --local user.name Anon"
    )
    assert (
        GitConfigCommand(
            key="url.evil; whoami.insteadOf",
            value="git://new",
            multi_value=True,
            repo="/my repo",
        ).shell()
        == "cd '/my repo' && git config --local --add "
        "'url.evil; whoami.insteadOf' git://new"
    )


def test_git_config_command_elevates_as_a_whole_under_sudo():
    # command_shell prefixes the whole rendered shell with sudo, exactly as
    # it does for any descriptor.
    assert (
        command_shell(GitConfigCommand(key="key", value="value"), sudo=True)
        == f"sudo {GIT_CONFIG_GLOBAL_SHELL}"
    )


async def test_git_config_sets_an_absent_global_value():
    # pyinfra's set_global.json: an empty global config, key absent -> the
    # write runs and the change is recorded.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize_config(host, key="key", value="value")
    assert ctx.changed
    assert ctx.reason == "git config key will be set to value"
    assert commands == [GitConfigCommand(key="key", value="value")]


async def test_git_config_sets_a_system_value():
    # set_system.json / edit_system.json: system=True targets the --system
    # scope, read from (and written to) the "system" sentinel.
    host = SimulatedHost(name="h", git_configs={"system": {"key": ["wrongvalue"]}})
    ctx, commands = await materialize_config(
        host, key="key", value="value", system=True
    )
    assert ctx.changed
    assert commands == [GitConfigCommand(key="key", value="value", system=True)]


async def test_git_config_edits_a_differing_global_value():
    # edit_global.json: the key holds a different value -> rewritten.
    host = SimulatedHost(name="h", git_configs={"global": {"key": ["wrongvalue"]}})
    ctx, commands = await materialize_config(host, key="key", value="value")
    assert ctx.changed
    assert commands == [GitConfigCommand(key="key", value="value")]


async def test_git_config_noop_when_the_global_value_matches():
    # set_existing_global.json: the key already equals [value] -> no-op with
    # pyinfra's verbatim noop_description.
    host = SimulatedHost(name="h", git_configs={"global": {"key": ["value"]}})
    ctx, commands = await materialize_config(host, key="key", value="value")
    assert not ctx.changed
    assert ctx.reason == "git config key is set to value"
    assert commands == []


async def test_git_config_noop_when_the_system_value_matches():
    # set_existing_system.json.
    host = SimulatedHost(name="h", git_configs={"system": {"key": ["value"]}})
    ctx, commands = await materialize_config(
        host, key="key", value="value", system=True
    )
    assert not ctx.changed
    assert ctx.reason == "git config key is set to value"
    assert commands == []


async def test_git_config_adds_the_first_multi_value():
    # add_first_multi_value.json: an empty config, multi_value -> --add.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize_config(
        host, key="key", value="value", multi_value=True
    )
    assert ctx.changed
    assert ctx.reason == "git config key will add value"
    assert commands == [GitConfigCommand(key="key", value="value", multi_value=True)]


async def test_git_config_adds_a_second_multi_value():
    # add_second_multi_value.json: the key already holds one value, a second
    # is added rather than replacing it.
    host = SimulatedHost(name="h", git_configs={"global": {"key": ["value"]}})
    ctx, commands = await materialize_config(
        host, key="key", value="another_value", multi_value=True
    )
    assert ctx.changed
    assert commands == [
        GitConfigCommand(key="key", value="another_value", multi_value=True)
    ]


async def test_git_config_noop_when_the_multi_value_is_present():
    # add_existing_multi_value.json: the value is already in the list.
    host = SimulatedHost(name="h", git_configs={"global": {"key": ["value"]}})
    ctx, commands = await materialize_config(
        host, key="key", value="value", multi_value=True
    )
    assert not ctx.changed
    assert ctx.reason == "git config key is set to value"
    assert commands == []


async def test_git_config_sets_a_repo_local_value():
    # set_repo.json: the repo's .git directory exists, so the local config is
    # read; the key is absent (only another_key is set) -> a --local write.
    host = SimulatedHost(
        name="h",
        directory_stats={"/my/repo/.git": {"mode": 755}},
        git_configs={"/my/repo": {"another_key": ["value"]}},
    )
    ctx, commands = await materialize_config(
        host, key="key", value="value", repo="/my/repo"
    )
    assert ctx.changed
    assert commands == [GitConfigCommand(key="key", value="value", repo="/my/repo")]


async def test_git_config_repo_gate_skips_the_fact_read_when_git_dir_absent():
    # pyinfra reads the repo config only behind the files.Directory(repo/.git)
    # gate. With no .git directory the existing config reads as {} even though
    # a matching value is seeded, so the write still runs: the gate skips the
    # fact read, it does not suppress the command.
    host = SimulatedHost(name="h", git_configs={"/no/repo": {"key": ["value"]}})
    ctx, commands = await materialize_config(
        host, key="key", value="value", repo="/no/repo"
    )
    assert ctx.changed
    assert commands == [GitConfigCommand(key="key", value="value", repo="/no/repo")]


async def test_git_config_absorbs_unknown_kwargs():
    # **kwargs mirrors pyinfra's global-arguments slot; op() already strips
    # the name/sudo/su meta-parameters, the rest is ignored.
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize_config(
        host, key="key", value="value", future_flag=True
    )
    assert commands == [GitConfigCommand(key="key", value="value")]


async def test_git_config_simulated_seam_sets_and_adds():
    # send_command maps GitConfigCommand onto git_configs: a single-valued
    # write replaces the key's list (idempotently), a multi_value write
    # appends, and each scope keys its own sentinel or repo path.
    host = SimulatedHost(name="h")
    await send_command(host, GitConfigCommand(key="key", value="value"))
    assert host.git_configs == {"global": {"key": ["value"]}}
    await send_command(host, GitConfigCommand(key="key", value="value"))
    assert host.git_configs == {"global": {"key": ["value"]}}
    await send_command(host, GitConfigCommand(key="multi", value="a", multi_value=True))
    await send_command(host, GitConfigCommand(key="multi", value="b", multi_value=True))
    await send_command(host, GitConfigCommand(key="multi", value="a", multi_value=True))
    assert host.git_configs["global"]["multi"] == ["a", "b"]
    await send_command(host, GitConfigCommand(key="k", value="v", system=True))
    assert host.git_configs["system"] == {"k": ["v"]}
    await send_command(host, GitConfigCommand(key="k", value="v", repo="/srv/app"))
    assert host.git_configs["/srv/app"] == {"k": ["v"]}


async def test_git_config_end_to_end_set_then_noop():
    # Through run(): the first apply sets the global config into the
    # simulated state; a second run reads it back and is a no-op.

    def deploy(host):
        yield op(git.config, name="Set the global user name", key="user.name", value="kim")

    host = SimulatedHost(name="h")

    def factory(entry):
        return host  # reuse ONE host so the second run sees the first's write

    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], deploy, host_factory=factory
    )
    assert plans["h"][0].changed
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (GitConfigCommand(key="user.name", value="kim"),)
    assert host.git_configs == {"global": {"user.name": ["kim"]}}
    assert "$ git config --global user.name kim" in format_log(report)

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert not entry2.changed
    assert entry2.reason == "git config user.name is set to kim"


async def test_git_config_dry_run_sends_nothing():
    # dry=True materializes the plan (changed, one command) but mutates no
    # simulated state -- the write is only planned, never sent.

    def deploy(host):
        yield op(git.config, name="Set the global user name", key="user.name", value="kim")

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        deploy,
        dry=True,
        host_factory=lambda entry: host,
    )
    assert report is None
    assert plans["h"][0].changed
    assert plans["h"][0].commands == (GitConfigCommand(key="user.name", value="kim"),)
    assert host.git_configs == {}
    assert "changed" in format_plan(plans)


async def test_git_config_privilege_sudo_flows_into_the_log():
    # A repo --local or --system write often needs root: sudo=True prefixes
    # the whole shell in the apply log. The credential comes from the
    # inventory privilege, never the operation, and never appears in the log.

    def deploy(host):
        yield op(
            git.config,
            name="Set the system date format",
            key="log.date",
            value="iso",
            system=True,
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], deploy)
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.changed
    assert "$ sudo git config --system log.date iso" in format_log(report)
    assert host.git_configs == {"system": {"log.date": ["iso"]}}


# --- worktree operation ------------------------------------------------------

# pyinfra's git.worktree renders ``cd <repo> && git worktree add ...`` to
# create, ``cd <worktree> && git worktree remove .`` to remove and
# ``cd <worktree> && git pull[...]`` to update. reemote5 shlex-quotes every
# operand (pyinfra uses raw QuoteString interpolation), so plain paths quote
# to themselves and command parity holds for ordinary values.
GIT_WORKTREE_ADD_SHELL = "cd /srv/app && git worktree add /srv/wt"
GIT_WORKTREE_REMOVE_SHELL = "cd /srv/wt && git worktree remove ."
GIT_WORKTREE_PULL_SHELL = "cd /srv/wt && git pull"


async def materialize_worktree(host: Host, **kwargs):
    """Run ``git.worktree`` against a fresh cache; return ``(ctx, commands)``."""
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in git.worktree(ctx, **kwargs)]
    return ctx, commands


def test_git_worktree_add_command_renders_flags():
    # pyinfra's argument order: -b <new_branch> (or --detach when no new
    # branch), then --force, then the worktree path, then the commitish.
    assert GitWorktreeAddCommand(repo="/srv/app", worktree="/srv/wt").shell() == (
        GIT_WORKTREE_ADD_SHELL
    )
    assert GitWorktreeAddCommand(
        repo="/srv/app", worktree="/srv/wt", new_branch="feature"
    ).shell() == "cd /srv/app && git worktree add -b feature /srv/wt"
    assert GitWorktreeAddCommand(
        repo="/srv/app", worktree="/srv/wt", detached=True
    ).shell() == "cd /srv/app && git worktree add --detach /srv/wt"
    assert GitWorktreeAddCommand(
        repo="/srv/app", worktree="/srv/wt", force=True
    ).shell() == "cd /srv/app && git worktree add --force /srv/wt"
    assert GitWorktreeAddCommand(
        repo="/srv/app", worktree="/srv/wt", commitish="v1.0"
    ).shell() == "cd /srv/app && git worktree add /srv/wt v1.0"
    # new_branch wins over detached, and --force plus a commitish stack on top.
    assert GitWorktreeAddCommand(
        repo="/srv/app",
        worktree="/srv/wt",
        new_branch="feature",
        detached=True,
        force=True,
        commitish="main",
    ).shell() == "cd /srv/app && git worktree add -b feature --force /srv/wt main"


def test_git_worktree_remove_and_pull_commands_render():
    assert GitWorktreeRemoveCommand(worktree="/srv/wt").shell() == (
        GIT_WORKTREE_REMOVE_SHELL
    )
    assert GitWorktreeRemoveCommand(worktree="/srv/wt", force=True).shell() == (
        "cd /srv/wt && git worktree remove . --force"
    )
    assert GitPullCommand(worktree="/srv/wt").shell() == GIT_WORKTREE_PULL_SHELL
    assert GitPullCommand(worktree="/srv/wt", rebase=True).shell() == (
        "cd /srv/wt && git pull --rebase"
    )
    # A tracking-driven pull renders bare ``git pull`` (no operands); only an
    # explicit from_remote_branch names the remote and branch, as pyinfra does.
    assert GitPullCommand(
        worktree="/srv/wt", from_remote_branch=("origin", "main")
    ).shell() == "cd /srv/wt && git pull origin main"


def test_git_worktree_commands_quote_operands_needing_it():
    # A repo, worktree, branch or commitish carrying shell metacharacters is
    # shlex-quoted so it is read literally, not executed.
    assert GitWorktreeAddCommand(
        repo="/my repo; whoami", worktree="/srv/wt"
    ).shell() == "cd '/my repo; whoami' && git worktree add /srv/wt"
    assert GitWorktreeAddCommand(
        repo="/srv/app", worktree="/srv/my wt", new_branch="fea ture"
    ).shell() == "cd /srv/app && git worktree add -b 'fea ture' '/srv/my wt'"
    assert GitPullCommand(
        worktree="/srv/wt", from_remote_branch=("my remote", "my branch")
    ).shell() == "cd /srv/wt && git pull 'my remote' 'my branch'"


def test_git_worktree_command_elevates_as_a_whole_under_sudo():
    # command_shell prefixes the whole rendered shell with sudo, exactly as it
    # does for any descriptor.
    assert command_shell(
        GitWorktreeAddCommand(repo="/srv/app", worktree="/srv/wt"), sudo=True
    ) == f"sudo {GIT_WORKTREE_ADD_SHELL}"


async def test_git_worktree_adds_a_missing_tree():
    # Create: the repository's .git directory is present and the worktree path
    # is absent -> one add command, with pyinfra's change recorded.
    host = SimulatedHost(name="h", directory_stats={"/srv/app/.git": {"mode": 755}})
    ctx, commands = await materialize_worktree(
        host, worktree="/srv/wt", repo="/srv/app"
    )
    assert ctx.changed
    assert ctx.reason == "worktree /srv/wt missing, will add"
    assert commands == [GitWorktreeAddCommand(repo="/srv/app", worktree="/srv/wt")]


async def test_git_worktree_create_carries_branch_and_force_options():
    host = SimulatedHost(name="h", directory_stats={"/srv/app/.git": {"mode": 755}})
    _ctx, commands = await materialize_worktree(
        host,
        worktree="/srv/wt",
        repo="/srv/app",
        new_branch="feature",
        force=True,
        commitish="main",
    )
    assert commands == [
        GitWorktreeAddCommand(
            repo="/srv/app",
            worktree="/srv/wt",
            new_branch="feature",
            force=True,
            commitish="main",
        )
    ]


async def test_git_worktree_assume_repo_exists_skips_the_git_dir_probe():
    # No .git directory is seeded, yet assume_repo_exists=True suppresses the
    # repo/.git validity check (pyinfra's files.Directory gate) and the tree is
    # still added.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize_worktree(
        host, worktree="/srv/wt", repo="/srv/app", assume_repo_exists=True
    )
    assert ctx.changed
    assert commands == [GitWorktreeAddCommand(repo="/srv/app", worktree="/srv/wt")]


async def test_git_worktree_removes_an_existing_tree():
    # present=False with the tree present -> one remove command; repo is unused
    # on this branch.
    host = SimulatedHost(name="h", directory_stats={"/srv/wt": {"mode": 755}})
    ctx, commands = await materialize_worktree(
        host, worktree="/srv/wt", present=False
    )
    assert ctx.changed
    assert ctx.reason == "worktree /srv/wt present, will remove"
    assert commands == [GitWorktreeRemoveCommand(worktree="/srv/wt")]


async def test_git_worktree_remove_forces_an_unclean_tree():
    host = SimulatedHost(name="h", directory_stats={"/srv/wt": {"mode": 755}})
    _ctx, commands = await materialize_worktree(
        host, worktree="/srv/wt", present=False, force=True
    )
    assert commands == [GitWorktreeRemoveCommand(worktree="/srv/wt", force=True)]


async def test_git_worktree_noop_when_absent_and_not_wanted():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize_worktree(
        host, worktree="/srv/wt", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "worktree /srv/wt already absent"
    assert commands == []


async def test_git_worktree_noop_when_pull_is_disabled():
    # The tree exists and is wanted, but pull=False -> nothing to send.
    host = SimulatedHost(name="h", directory_stats={"/srv/wt": {"mode": 755}})
    ctx, commands = await materialize_worktree(
        host, worktree="/srv/wt", repo="/srv/app", pull=False
    )
    assert not ctx.changed
    assert ctx.reason == "pull is disabled"
    assert commands == []


async def test_git_worktree_noop_when_not_linked_to_a_tracking_branch():
    # Exists, wanted, pull on, but neither a tracking branch nor a
    # from_remote_branch -> pyinfra's "not linked to a tracking branch" no-op.
    host = SimulatedHost(name="h", directory_stats={"/srv/wt": {"mode": 755}})
    ctx, commands = await materialize_worktree(
        host, worktree="/srv/wt", repo="/srv/app"
    )
    assert not ctx.changed
    assert ctx.reason == "worktree /srv/wt is not linked to a tracking branch"
    assert commands == []


async def test_git_worktree_pulls_a_stale_tree():
    # Exists + a tracking branch, and the local HEAD differs from the remote
    # tip -> pyinfra's pull (skip_pull not taken).
    host = SimulatedHost(
        name="h",
        directory_stats={"/srv/wt": {"mode": 755}},
        git_tracking_branches={"/srv/wt": "origin/main"},
        git_local_commits={"/srv/wt": {"HEAD": SHA40}},
        git_remote_commits={"/srv/wt": {"origin": {"main": SHA64}}},
    )
    ctx, commands = await materialize_worktree(
        host, worktree="/srv/wt", repo="/srv/app"
    )
    assert ctx.changed
    assert ctx.reason == "worktree /srv/wt will be pulled"
    # A tracking-driven pull yields a bare GitPullCommand (no operands).
    assert commands == [GitPullCommand(worktree="/srv/wt")]


async def test_git_worktree_pulls_with_rebase_and_from_remote_branch():
    # No tracking branch is needed when from_remote_branch names the source;
    # it drives the pull and is carried onto the descriptor.
    host = SimulatedHost(
        name="h",
        directory_stats={"/srv/wt": {"mode": 755}},
        git_local_commits={"/srv/wt": {"HEAD": SHA40}},
        git_remote_commits={"/srv/wt": {"upstream": {"dev": SHA64}}},
    )
    ctx, commands = await materialize_worktree(
        host,
        worktree="/srv/wt",
        repo="/srv/app",
        rebase=True,
        from_remote_branch=("upstream", "dev"),
    )
    assert ctx.changed
    assert ctx.reason == "worktree /srv/wt will be pulled"
    assert commands == [
        GitPullCommand(
            worktree="/srv/wt", rebase=True, from_remote_branch=("upstream", "dev")
        )
    ]


async def test_git_worktree_skips_pull_when_already_up_to_date():
    # local HEAD == remote tip -> pyinfra's skip_pull short-circuit, a no-op.
    host = SimulatedHost(
        name="h",
        directory_stats={"/srv/wt": {"mode": 755}},
        git_tracking_branches={"/srv/wt": "origin/main"},
        git_local_commits={"/srv/wt": {"HEAD": SHA40}},
        git_remote_commits={"/srv/wt": {"origin": {"main": SHA40}}},
    )
    ctx, commands = await materialize_worktree(
        host, worktree="/srv/wt", repo="/srv/app"
    )
    assert not ctx.changed
    assert ctx.reason == "worktree /srv/wt is already up to date"
    assert commands == []


async def test_git_worktree_absorbs_unknown_kwargs():
    # **kwargs mirrors pyinfra's global-arguments slot; op() already strips
    # name/sudo/su, the rest is ignored.
    host = SimulatedHost(name="h", directory_stats={"/srv/app/.git": {"mode": 755}})
    _ctx, commands = await materialize_worktree(
        host, worktree="/srv/wt", repo="/srv/app", future_flag=True
    )
    assert commands == [GitWorktreeAddCommand(repo="/srv/app", worktree="/srv/wt")]


async def test_git_worktree_requires_repo_when_creating():
    # Create with no repo -> ValueError (reemote5's argument-validation idiom,
    # raised before the repo/.git probe).
    host = SimulatedHost(name="h")
    with pytest.raises(ValueError, match="repo must be specified"):
        await materialize_worktree(host, worktree="/srv/wt")


async def test_git_worktree_rejects_an_invalid_repository():
    # Create with a repo whose .git directory is absent -> OperationError
    # carrying pyinfra's message.
    host = SimulatedHost(name="h")
    with pytest.raises(OperationError, match="not a valid GIT repository"):
        await materialize_worktree(host, worktree="/srv/wt", repo="/srv/app")


async def test_git_worktree_rejects_a_malformed_from_remote_branch():
    # A present, wanted, pulling tree given a from_remote_branch that is not a
    # 2-tuple -> ValueError.
    host = SimulatedHost(name="h", directory_stats={"/srv/wt": {"mode": 755}})
    with pytest.raises(ValueError, match="2-tuple"):
        await materialize_worktree(
            host,
            worktree="/srv/wt",
            repo="/srv/app",
            from_remote_branch=("only",),
        )


async def test_git_worktree_simulated_seam_mutates_state():
    # send_command maps each descriptor onto the shared directory_stats / git_*
    # seams so a repeat operation reads the new state back.
    host = SimulatedHost(name="h")
    await send_command(host, GitWorktreeAddCommand(repo="/srv/app", worktree="/srv/wt"))
    assert host.directory_stats["/srv/wt"] == {"mode": 755}
    # A pull advances the tree's HEAD to the remote tip via its tracking branch.
    host.git_tracking_branches["/srv/wt"] = "origin/main"
    host.git_remote_commits["/srv/wt"] = {"origin": {"main": SHA40}}
    await send_command(host, GitPullCommand(worktree="/srv/wt"))
    assert host.git_local_commits["/srv/wt"]["HEAD"] == SHA40
    # A removal drops the directory so a repeat add would create it again.
    await send_command(host, GitWorktreeRemoveCommand(worktree="/srv/wt"))
    assert "/srv/wt" not in host.directory_stats


async def test_git_worktree_end_to_end_add_then_noop():
    # Through run(): the first apply registers the tree in directory_stats; a
    # second run sees it present and, with no tracking branch, is a no-op.

    def deploy(host):
        yield op(
            git.worktree,
            name="Add a worktree",
            worktree="/srv/wt",
            repo="/srv/app",
            assume_repo_exists=True,
        )

    host = SimulatedHost(name="h")

    def factory(entry):
        return host  # reuse ONE host so the second run sees the first's add

    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], deploy, host_factory=factory
    )
    assert plans["h"][0].changed
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (
        GitWorktreeAddCommand(repo="/srv/app", worktree="/srv/wt"),
    )
    assert host.directory_stats["/srv/wt"] == {"mode": 755}
    assert "$ cd /srv/app && git worktree add /srv/wt" in format_log(report)

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert not entry2.changed
    assert entry2.reason == "worktree /srv/wt is not linked to a tracking branch"


async def test_git_worktree_dry_run_sends_nothing():
    # dry=True materializes the plan (changed, one command) but adds no
    # directory to the simulated state -- the write is only planned, never sent.

    def deploy(host):
        yield op(
            git.worktree,
            name="Add a worktree",
            worktree="/srv/wt",
            repo="/srv/app",
            assume_repo_exists=True,
        )

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        deploy,
        dry=True,
        host_factory=lambda entry: host,
    )
    assert report is None
    assert plans["h"][0].changed
    assert plans["h"][0].commands == (
        GitWorktreeAddCommand(repo="/srv/app", worktree="/srv/wt"),
    )
    assert "/srv/wt" not in host.directory_stats
    assert "changed" in format_plan(plans)


async def test_git_worktree_privilege_sudo_flows_into_the_log():
    # Adding/removing a worktree writes under the repository's .git, often
    # root-owned, so sudo=True prefixes the whole shell in the apply log. The
    # credential comes from the inventory privilege, never the operation.

    def deploy(host):
        yield op(
            git.worktree,
            name="Add a worktree",
            worktree="/srv/wt",
            repo="/srv/app",
            assume_repo_exists=True,
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], deploy)
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.changed
    assert "$ sudo cd /srv/app && git worktree add /srv/wt" in format_log(report)
    assert host.directory_stats["/srv/wt"] == {"mode": 755}


# --- repo operation ----------------------------------------------------------

# pyinfra's git.repo renders ``cd <dest> && git clone <src> ... .`` because it
# first mkdir -p's ``dest`` and clones into ``.``. reemote5 deliberately drops
# the ensure-directory step (there is no files.directory to delegate to), so
# the clone descriptor names ``dest`` itself and needs no ``cd`` prefix. Every
# other verb keeps pyinfra's ``cd <dest> &&`` shape, and pyinfra's final
# ``chown -R <user>`` mutation and its ``ssh_keyscan`` preflight are dropped
# too (see the operation's docstring), so the parity assertions below compare
# only the git-side commands pyinfra still emits.
GIT_CLONE_SHELL = "git clone myrepo /home/myrepo"
GIT_REMOTE_SET_URL_SHELL = "cd /home/myrepo && git remote set-url origin mynewremote"
GIT_FETCH_SHELL = "cd /home/myrepo && git fetch"
GIT_CHECKOUT_SHELL = "cd /home/myrepo && git checkout mybranch"
GIT_REPO_PULL_SHELL = "cd /home/myrepo && git pull"
GIT_SUBMODULE_SHELL = "cd /home/myrepo && git submodule update --init"


async def materialize_repo(host: Host, **kwargs):
    """Run ``git.repo`` against a fresh cache; return ``(ctx, commands)``."""
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in git.repo(ctx, **kwargs)]
    return ctx, commands


def test_git_clone_command_renders_flags():
    # pyinfra's clone.json / clone_depth.json / branch_pull.json fixtures
    # order the flags ``--depth N --branch B <dest>`` after the src, exactly
    # as reemote5 does -- but without pyinfra's leading ``cd <dest> &&``
    # because the ensure-directory step is dropped.
    assert GitCloneCommand(src="myrepo", dest="/home/myrepo").shell() == (
        GIT_CLONE_SHELL
    )
    assert GitCloneCommand(
        src="myrepo", dest="/home/myrepo", branch="mybranch"
    ).shell() == "git clone myrepo --branch mybranch /home/myrepo"
    assert GitCloneCommand(
        src="myrepo", dest="/home/myrepo", branch="mybranch", depth=1
    ).shell() == "git clone myrepo --depth 1 --branch mybranch /home/myrepo"


def test_git_repo_reconcile_fetch_checkout_pull_commands_render():
    # Each verb's flag order matches pyinfra's own rendered fixtures: --depth
    # first, then --tags (fetch only), then --rebase (pull only), then
    # --force.
    assert GitRemoteSetUrlCommand(
        dest="/home/myrepo", url="mynewremote"
    ).shell() == GIT_REMOTE_SET_URL_SHELL
    assert GitFetchCommand(dest="/home/myrepo").shell() == GIT_FETCH_SHELL
    assert GitFetchCommand(
        dest="/home/myrepo", depth=1, tags=True
    ).shell() == "cd /home/myrepo && git fetch --depth 1 --tags"
    assert GitFetchCommand(
        dest="/home/myrepo", tags=True, force=True
    ).shell() == "cd /home/myrepo && git fetch --tags --force"
    assert GitCheckoutCommand(
        dest="/home/myrepo", branch="mybranch"
    ).shell() == GIT_CHECKOUT_SHELL
    assert GitCheckoutCommand(
        dest="/home/myrepo", branch="mybranch", force=True
    ).shell() == "cd /home/myrepo && git checkout mybranch --force"
    assert GitRepoPullCommand(dest="/home/myrepo").shell() == GIT_REPO_PULL_SHELL
    assert GitRepoPullCommand(dest="/home/myrepo", depth=1).shell() == (
        "cd /home/myrepo && git pull --depth 1"
    )
    assert GitRepoPullCommand(dest="/home/myrepo", rebase=True).shell() == (
        "cd /home/myrepo && git pull --rebase"
    )
    # force=True and branch=... stack: branch is seam-only, never rendered.
    assert GitRepoPullCommand(
        dest="/home/myrepo", branch="mybranch", force=True
    ).shell() == "cd /home/myrepo && git pull --force"


def test_git_submodule_update_command_renders_recursion():
    assert GitSubmoduleUpdateCommand(dest="/home/myrepo").shell() == (
        GIT_SUBMODULE_SHELL
    )
    assert GitSubmoduleUpdateCommand(
        dest="/home/myrepo", recursive=True
    ).shell() == "cd /home/myrepo && git submodule update --init --recursive"


def test_git_repo_commands_quote_operands_needing_it():
    # pyinfra's clone_src_needs_quotes / clone_branch_needs_quotes /
    # checkout_branch_needs_quotes fixtures: an operand carrying a shell
    # metacharacter is read literally, never executed. Plain URLs and paths
    # quote to themselves so command parity holds for ordinary values.
    assert GitCloneCommand(
        src="https://example.com/repo; cat /etc/passwd", dest="/home/myrepo"
    ).shell() == (
        "git clone 'https://example.com/repo; cat /etc/passwd' /home/myrepo"
    )
    assert GitCloneCommand(
        src="myrepo", dest="/home/myrepo", branch="main; rm -rf /"
    ).shell() == "git clone myrepo --branch 'main; rm -rf /' /home/myrepo"
    assert GitCheckoutCommand(
        dest="/home/myrepo", branch="feature/evil; rm -rf /"
    ).shell() == "cd /home/myrepo && git checkout 'feature/evil; rm -rf /'"
    assert GitRepoPullCommand(
        dest="/my repo; whoami"
    ).shell() == "cd '/my repo; whoami' && git pull"


def test_git_repo_command_elevates_as_a_whole_under_sudo():
    # command_shell prefixes the whole rendered shell with sudo, exactly as it
    # does for any descriptor.
    assert command_shell(
        GitCloneCommand(src="myrepo", dest="/home/myrepo"), sudo=True
    ) == f"sudo {GIT_CLONE_SHELL}"


async def test_git_repo_clones_a_missing_repository():
    # clone.json: dest/.git is absent -> one GitCloneCommand carrying the
    # requested branch; the reason names both the missing-repo state and src.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize_repo(
        host, src="myrepo", dest="/home/myrepo", branch="mybranch", pull=False
    )
    assert ctx.changed
    assert ctx.reason == (
        "/home/myrepo is not a repository, will clone from myrepo"
    )
    assert commands == [
        GitCloneCommand(src="myrepo", dest="/home/myrepo", branch="mybranch"),
    ]


async def test_git_repo_clone_carries_depth_and_branch():
    # clone_depth.json -- reemote5 drops the trailing chown -R <user> that
    # pyinfra emits after the clone, so only the clone is yielded.
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize_repo(
        host,
        src="myrepo",
        dest="/home/myrepo",
        branch="mybranch",
        depth=1,
        pull=False,
    )
    assert commands == [
        GitCloneCommand(
            src="myrepo", dest="/home/myrepo", branch="mybranch", depth=1
        ),
    ]


async def test_git_repo_pulls_when_the_branch_differs():
    # branch_pull.json: origin already points at src, current_branch "master"
    # differs from "mybranch", local commit is None -> fetch (with tags),
    # checkout, then pull. All three reasons concatenate with "; ".
    host = SimulatedHost(
        name="h",
        directory_stats={"/home/myrepo/.git": {"mode": 755}},
        git_configs={"/home/myrepo": {"remote.origin.url": ["myrepo"]}},
        git_branches={"/home/myrepo": "master"},
        git_remote_commits={
            "/home/myrepo": {"origin": {"mybranch": "b" * 40}}
        },
    )
    ctx, commands = await materialize_repo(
        host, src="myrepo", dest="/home/myrepo", branch="mybranch", fetch_tags=True
    )
    assert ctx.changed
    assert ctx.reason == (
        "on master, will switch to mybranch; "
        "git repository /home/myrepo will be pulled"
    )
    assert commands == [
        GitFetchCommand(dest="/home/myrepo", tags=True),
        GitCheckoutCommand(dest="/home/myrepo", branch="mybranch"),
        GitRepoPullCommand(dest="/home/myrepo", branch="mybranch"),
    ]


async def test_git_repo_branch_switch_skips_pull_when_local_matches_remote():
    # branch_switch_up_to_date.json: the branch differs so fetch+checkout
    # still run, but the local commit already equals the remote tip -> the
    # skip_pull short-circuit suppresses the pull.
    host = SimulatedHost(
        name="h",
        directory_stats={"/home/myrepo/.git": {"mode": 755}},
        git_configs={"/home/myrepo": {"remote.origin.url": ["myrepo"]}},
        git_branches={"/home/myrepo": "master"},
        git_local_commits={"/home/myrepo": {"mybranch": SHA40}},
        git_remote_commits={"/home/myrepo": {"origin": {"mybranch": SHA40}}},
    )
    ctx, commands = await materialize_repo(
        host, src="myrepo", dest="/home/myrepo", branch="mybranch"
    )
    assert ctx.changed
    assert ctx.reason == (
        "on master, will switch to mybranch; "
        "git repository /home/myrepo is already up to date"
    )
    assert commands == [
        GitFetchCommand(dest="/home/myrepo"),
        GitCheckoutCommand(dest="/home/myrepo", branch="mybranch"),
    ]


async def test_git_repo_change_src_resets_the_remote_and_pulls():
    # change_src.json: an existing repo whose origin URL differs from the
    # requested src -> reset the remote, then pull. pyinfra always pulls in
    # that case (skip_pull is short-circuited by remote_changed), even though
    # the seeded local/remote SHAs match, because the cached remote tip was
    # resolved against the old origin.
    host = SimulatedHost(
        name="h",
        directory_stats={"/home/myrepo/.git": {"mode": 755}},
        git_configs={"/home/myrepo": {"remote.origin.url": ["myoldremote"]}},
        git_branches={"/home/myrepo": "master"},
        git_local_commits={"/home/myrepo": {"master": "a" * 40}},
        git_remote_commits={"/home/myrepo": {"origin": {"master": "a" * 40}}},
    )
    ctx, commands = await materialize_repo(
        host, src="mynewremote", dest="/home/myrepo"
    )
    assert ctx.changed
    assert ctx.reason == (
        "origin url will be set to mynewremote; "
        "git repository /home/myrepo will be pulled"
    )
    assert commands == [
        GitRemoteSetUrlCommand(dest="/home/myrepo", url="mynewremote"),
        GitRepoPullCommand(dest="/home/myrepo", branch="master"),
    ]


async def test_git_repo_noop_when_already_up_to_date():
    # up_to_date.json: origin matches src, no branch switch, local == remote
    # -> no commands, no ctx.changed, and pyinfra's verbatim noop_description.
    host = SimulatedHost(
        name="h",
        directory_stats={"/home/myrepo/.git": {"mode": 755}},
        git_configs={"/home/myrepo": {"remote.origin.url": ["myrepo"]}},
        git_branches={"/home/myrepo": "master"},
        git_local_commits={"/home/myrepo": {"master": SHA40}},
        git_remote_commits={"/home/myrepo": {"origin": {"master": SHA40}}},
    )
    ctx, commands = await materialize_repo(host, src="myrepo", dest="/home/myrepo")
    assert not ctx.changed
    assert ctx.reason == "git repository /home/myrepo is already up to date"
    assert commands == []


async def test_git_repo_rebase_pull():
    # rebase.json: no branch switch, local != remote -> one pull --rebase.
    host = SimulatedHost(
        name="h",
        directory_stats={"/home/myrepo/.git": {"mode": 755}},
        git_configs={"/home/myrepo": {"remote.origin.url": ["myrepo"]}},
        git_branches={"/home/myrepo": "master"},
        git_local_commits={"/home/myrepo": {"master": "a" * 40}},
        git_remote_commits={"/home/myrepo": {"origin": {"master": "b" * 40}}},
    )
    ctx, commands = await materialize_repo(
        host, src="myrepo", dest="/home/myrepo", rebase=True
    )
    assert ctx.changed
    assert ctx.reason == "git repository /home/myrepo will be pulled"
    assert commands == [
        GitRepoPullCommand(dest="/home/myrepo", branch="master", rebase=True),
    ]


async def test_git_repo_pull_carries_depth_and_fetch_tags():
    # branch_pull_depth.json: the depth applies to fetch AND pull.
    host = SimulatedHost(
        name="h",
        directory_stats={"/home/myrepo/.git": {"mode": 755}},
        git_configs={"/home/myrepo": {"remote.origin.url": ["myrepo"]}},
        git_branches={"/home/myrepo": "master"},
        git_remote_commits={"/home/myrepo": {"origin": {"mybranch": "b" * 40}}},
    )
    _ctx, commands = await materialize_repo(
        host,
        src="myrepo",
        dest="/home/myrepo",
        branch="mybranch",
        depth=1,
        fetch_tags=True,
    )
    assert commands == [
        GitFetchCommand(dest="/home/myrepo", depth=1, tags=True),
        GitCheckoutCommand(dest="/home/myrepo", branch="mybranch"),
        GitRepoPullCommand(dest="/home/myrepo", branch="mybranch", depth=1),
    ]


async def test_git_repo_force_and_recursive_submodules_flow_to_every_verb():
    # branch_pull_force_tags.json: force lands on fetch, checkout and pull,
    # --recursive on the submodule update, and the submodules line always
    # appends last.
    host = SimulatedHost(
        name="h",
        directory_stats={"/home/myrepo/.git": {"mode": 755}},
        git_configs={"/home/myrepo": {"remote.origin.url": ["myrepo"]}},
        git_branches={"/home/myrepo": "master"},
        git_remote_commits={"/home/myrepo": {"origin": {"mybranch": "b" * 40}}},
    )
    ctx, commands = await materialize_repo(
        host,
        src="myrepo",
        dest="/home/myrepo",
        branch="mybranch",
        fetch_tags=True,
        force=True,
        update_submodules=True,
        recursive_submodules=True,
    )
    assert ctx.changed
    assert ctx.reason == (
        "on master, will switch to mybranch; "
        "git repository /home/myrepo will be pulled; "
        "/home/myrepo submodules will be updated recursively"
    )
    assert commands == [
        GitFetchCommand(dest="/home/myrepo", tags=True, force=True),
        GitCheckoutCommand(dest="/home/myrepo", branch="mybranch", force=True),
        GitRepoPullCommand(dest="/home/myrepo", branch="mybranch", force=True),
        GitSubmoduleUpdateCommand(dest="/home/myrepo", recursive=True),
    ]


async def test_git_repo_update_submodules_appends_after_pull():
    # update_submodules.json: a differing local/remote SHA -> pull, then the
    # non-recursive submodule update.
    host = SimulatedHost(
        name="h",
        directory_stats={"/home/myrepo/.git": {"mode": 755}},
        git_configs={"/home/myrepo": {"remote.origin.url": ["myrepo"]}},
        git_branches={"/home/myrepo": "master"},
        git_local_commits={"/home/myrepo": {"master": "a" * 40}},
        git_remote_commits={"/home/myrepo": {"origin": {"master": "b" * 40}}},
    )
    ctx, commands = await materialize_repo(
        host, src="myrepo", dest="/home/myrepo", update_submodules=True
    )
    assert ctx.changed
    assert ctx.reason == (
        "git repository /home/myrepo will be pulled; "
        "/home/myrepo submodules will be updated"
    )
    assert commands == [
        GitRepoPullCommand(dest="/home/myrepo", branch="master"),
        GitSubmoduleUpdateCommand(dest="/home/myrepo"),
    ]


async def test_git_repo_submodules_run_even_when_nothing_else_changed():
    # The submodule step is independent: an up-to-date repo still yields the
    # update --init, mirroring pyinfra's unconditional append.
    host = SimulatedHost(
        name="h",
        directory_stats={"/home/myrepo/.git": {"mode": 755}},
        git_configs={"/home/myrepo": {"remote.origin.url": ["myrepo"]}},
        git_branches={"/home/myrepo": "master"},
        git_local_commits={"/home/myrepo": {"master": SHA40}},
        git_remote_commits={"/home/myrepo": {"origin": {"master": SHA40}}},
    )
    ctx, commands = await materialize_repo(
        host, src="myrepo", dest="/home/myrepo", update_submodules=True
    )
    assert ctx.changed
    assert ctx.reason == (
        "git repository /home/myrepo is already up to date; "
        "/home/myrepo submodules will be updated"
    )
    assert commands == [GitSubmoduleUpdateCommand(dest="/home/myrepo")]


async def test_git_repo_tag_branch_detaches_and_skips_pull():
    # The is_tag branch: the requested name matches a tag -> a second
    # checkout runs and the pull is suppressed. The initial branch switch is
    # skipped when the operation reads the current branch as the tag name
    # itself (already "on" it), leaving only the detach checkout.
    host = SimulatedHost(
        name="h",
        directory_stats={"/home/myrepo/.git": {"mode": 755}},
        git_configs={"/home/myrepo": {"remote.origin.url": ["myrepo"]}},
        git_branches={"/home/myrepo": "v1.0"},
        git_tags={"/home/myrepo": [{"name": "v1.0"}]},
    )
    ctx, commands = await materialize_repo(
        host, src="myrepo", dest="/home/myrepo", branch="v1.0", pull=False
    )
    assert ctx.changed
    assert ctx.reason == "v1.0 is a tag, will check it out"
    assert commands == [GitCheckoutCommand(dest="/home/myrepo", branch="v1.0")]


async def test_git_repo_absorbs_unknown_kwargs():
    # **kwargs mirrors pyinfra's global-arguments slot; op() already strips
    # name/sudo/su, the rest is ignored.
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize_repo(
        host, src="myrepo", dest="/home/myrepo", future_flag=True
    )
    assert commands == [GitCloneCommand(src="myrepo", dest="/home/myrepo")]


async def test_git_repo_simulated_seam_clone_and_pull_mutate_state():
    # send_command maps each descriptor onto the shared directory_stats /
    # git_* seams so a repeat operation reads the new state back: clone
    # materialises the repo and its .git, seeds origin, sets the branch;
    # set-url rewrites the origin; checkout advances GitBranch; pull
    # fast-forwards the local commit to the remote tip at the branch key.
    host = SimulatedHost(name="h")
    await send_command(
        host,
        GitCloneCommand(src="myrepo", dest="/home/myrepo", branch="mybranch"),
    )
    assert host.directory_stats["/home/myrepo"] == {"mode": 755}
    assert host.directory_stats["/home/myrepo/.git"] == {"mode": 755}
    assert "/home/myrepo" in host.git_repos
    assert host.git_configs["/home/myrepo"]["remote.origin.url"] == ["myrepo"]
    assert host.git_branches["/home/myrepo"] == "mybranch"

    await send_command(
        host, GitRemoteSetUrlCommand(dest="/home/myrepo", url="mynewremote")
    )
    assert host.git_configs["/home/myrepo"]["remote.origin.url"] == ["mynewremote"]

    await send_command(
        host, GitCheckoutCommand(dest="/home/myrepo", branch="other")
    )
    assert host.git_branches["/home/myrepo"] == "other"

    # A pull fast-forwards only when the remote tip is seeded at that key.
    host.git_remote_commits["/home/myrepo"] = {"origin": {"mybranch": SHA64}}
    await send_command(
        host, GitRepoPullCommand(dest="/home/myrepo", branch="mybranch")
    )
    assert host.git_local_commits["/home/myrepo"]["mybranch"] == SHA64

    # fetch and submodule update have no observable effect: they touch state
    # no migrated fact reads back.
    await send_command(host, GitFetchCommand(dest="/home/myrepo", tags=True))
    await send_command(
        host, GitSubmoduleUpdateCommand(dest="/home/myrepo", recursive=True)
    )


async def test_git_repo_end_to_end_clone_then_noop():
    # Through run(): the first apply clones into the simulated state; a
    # second run sees the .git directory present, origin reconciled and the
    # branch already checked out (pull=False keeps the flow to the clone and
    # the following no-op), so no commands are sent.

    def deploy(host):
        yield op(
            git.repo,
            name="Clone the app repo",
            src="myrepo",
            dest="/home/myrepo",
            branch="mybranch",
            pull=False,
        )

    host = SimulatedHost(name="h")

    def factory(entry):
        return host  # reuse ONE host so the second run sees the first's clone

    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], deploy, host_factory=factory
    )
    assert plans["h"][0].changed
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (
        GitCloneCommand(src="myrepo", dest="/home/myrepo", branch="mybranch"),
    )
    assert host.directory_stats["/home/myrepo/.git"] == {"mode": 755}
    assert "$ git clone myrepo --branch mybranch /home/myrepo" in format_log(report)

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert not entry2.changed
    assert entry2.commands == ()


async def test_git_repo_dry_run_sends_nothing():
    # dry=True materialises the plan (changed, one clone command) but mutates
    # no simulated state -- the clone is only planned, never sent.

    def deploy(host):
        yield op(
            git.repo,
            name="Clone the app repo",
            src="myrepo",
            dest="/home/myrepo",
            pull=False,
        )

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        deploy,
        dry=True,
        host_factory=lambda entry: host,
    )
    assert report is None
    assert plans["h"][0].changed
    assert plans["h"][0].commands == (
        GitCloneCommand(src="myrepo", dest="/home/myrepo"),
    )
    assert host.directory_stats == {}
    assert "changed" in format_plan(plans)


async def test_git_repo_privilege_sudo_flows_into_the_log():
    # Cloning into /srv (root-owned) needs sudo=True; the whole shell is
    # prefixed in the apply log. The credential comes from the inventory
    # privilege, never the operation, and never appears in the log.

    def deploy(host):
        yield op(
            git.repo,
            name="Clone into /srv",
            src="myrepo",
            dest="/srv/app",
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], deploy)
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.changed
    assert "$ sudo git clone myrepo /srv/app" in format_log(report)
    assert host.directory_stats["/srv/app/.git"] == {"mode": 755}
