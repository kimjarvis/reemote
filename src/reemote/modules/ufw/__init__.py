# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``ufw`` module namespace.

Re-exports everything from ``ufw.py`` so the facts (``ufw.UfwStatus``,
``ufw.UfwRules``, ``ufw.UfwApplications``), the ``ufw.state``,
``ufw.default_policy``, ``ufw.logging``, ``ufw.reset`` and ``ufw.rule``
operations and their typed command descriptors (``ufw.UfwStateCommand``,
``ufw.UfwDefaultPolicyCommand``, ``ufw.UfwLoggingCommand``,
``ufw.UfwResetCommand``, ``ufw.UfwRuleCommand``) are reachable through the
package name alone.
"""

from . import ufw
from .ufw import (
    UfwApplications,
    UfwDefaultPolicyCommand,
    UfwLoggingCommand,
    UfwResetCommand,
    UfwRule,
    UfwRuleCommand,
    UfwRules,
    UfwStateCommand,
    UfwStatus,
    UfwStatusInfo,
    default_policy,
    logging,
    reset,
    rule,
    state,
)

__all__ = [
    "UfwApplications",
    "UfwDefaultPolicyCommand",
    "UfwLoggingCommand",
    "UfwResetCommand",
    "UfwRule",
    "UfwRuleCommand",
    "UfwRules",
    "UfwStateCommand",
    "UfwStatus",
    "UfwStatusInfo",
    "default_policy",
    "logging",
    "reset",
    "rule",
    "state",
    "ufw",
]