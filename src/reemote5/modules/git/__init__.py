# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``git`` module namespace.

Re-exports everything from ``git.py`` so the facts
(``git.GitBranch``, ``git.GitConfig``, ``git.GitHeadDetached``,
``git.GitIsRepo``, ``git.GitLocalCommit``, ``git.GitRemoteBranchCommit``,
``git.GitRemoteUrl``, ``git.GitSshKeyConfigured``, ``git.GitStatus``,
``git.GitSubmodule``, ``git.GitTag``, ``git.GitTrackingBranch``) and
their TypedDict result shapes (``git.GitConfigItem``,
``git.GitStatusSummary``, ``git.GitSubmoduleStatus``, ``git.GitTagInfo``),
the ``git.config``, ``git.repo`` and ``git.worktree``
operations and their typed command descriptors (``git.GitCheckoutCommand``,
``git.GitCloneCommand``, ``git.GitConfigCommand``, ``git.GitFetchCommand``,
``git.GitPullCommand``, ``git.GitRemoteSetUrlCommand``,
``git.GitRepoPullCommand``, ``git.GitSubmoduleUpdateCommand``,
``git.GitWorktreeAddCommand``, ``git.GitWorktreeRemoveCommand``) are
reachable through the package name alone.
"""

from . import git
from .git import (
    GitBranch,
    GitCheckoutCommand,
    GitCloneCommand,
    GitConfig,
    GitConfigCommand,
    GitConfigItem,
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
    GitStatusSummary,
    GitSubmodule,
    GitSubmoduleStatus,
    GitSubmoduleUpdateCommand,
    GitTag,
    GitTagInfo,
    GitTrackingBranch,
    GitWorktreeAddCommand,
    GitWorktreeRemoveCommand,
    config,
    repo,
    worktree,
)

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
    "git",
    "repo",
    "worktree",
]
