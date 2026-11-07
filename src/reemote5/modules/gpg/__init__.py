# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``gpg`` module namespace.

Re-exports everything from ``gpg.py`` so the facts (``gpg.GpgKey``,
``gpg.GpgKeyDetails``, ``gpg.GpgKeyrings``, ``gpg.GpgKeys``,
``gpg.GpgSecretKeys``, ``gpg.GpgTrust``, ``gpg.GpgVerify``), the
operations (``gpg.key``, ``gpg.dearmor``, ``gpg.import_key``,
``gpg.export_key``, ``gpg.delete_key``, ``gpg.gen_key``, ``gpg.trust``,
``gpg.refresh_keys``) with their command descriptors
(``gpg.GpgKeyUploadCommand``, ``gpg.GpgKeyInstallCommand``,
``gpg.GpgKeyRemoveCommand``, ``gpg.GpgKeyDeleteKeysCommand``,
``gpg.GpgKeyEmptyCleanupCommand``, ``gpg.GpgDearmorCommand``,
``gpg.GpgImportUploadCommand``, ``gpg.GpgImportCommand``,
``gpg.GpgExportCommand``, ``gpg.GpgDeleteKeyCommand``,
``gpg.GpgGenKeyCommand``, ``gpg.GpgTrustCommand``,
``gpg.GpgRefreshKeysCommand``) and the
``gpg.GpgKeyInfo`` / ``gpg.GpgKeyMetadata`` / ``gpg.GpgKeyTrust`` /
``gpg.GpgKeyring`` / ``gpg.GpgVerification`` entry types are reachable
through the package name alone.
"""

from . import gpg
from .gpg import (
    GpgDearmorCommand,
    GpgDeleteKeyCommand,
    GpgExportCommand,
    GpgGenKeyCommand,
    GpgImportCommand,
    GpgImportUploadCommand,
    GpgKey,
    GpgKeyDeleteKeysCommand,
    GpgKeyDetails,
    GpgKeyEmptyCleanupCommand,
    GpgKeyInfo,
    GpgKeyInstallCommand,
    GpgKeyMetadata,
    GpgKeyRemoveCommand,
    GpgKeyring,
    GpgKeyrings,
    GpgKeys,
    GpgKeyTrust,
    GpgKeyUploadCommand,
    GpgRefreshKeysCommand,
    GpgSecretKeys,
    GpgTrust,
    GpgTrustCommand,
    GpgVerification,
    GpgVerify,
    dearmor,
    delete_key,
    export_key,
    gen_key,
    import_key,
    key,
    refresh_keys,
    trust,
)

__all__ = [
    "GpgDearmorCommand",
    "GpgDeleteKeyCommand",
    "GpgExportCommand",
    "GpgGenKeyCommand",
    "GpgImportCommand",
    "GpgImportUploadCommand",
    "GpgKey",
    "GpgKeyDeleteKeysCommand",
    "GpgKeyDetails",
    "GpgKeyEmptyCleanupCommand",
    "GpgKeyInfo",
    "GpgKeyInstallCommand",
    "GpgKeyMetadata",
    "GpgKeyRemoveCommand",
    "GpgKeyTrust",
    "GpgKeyUploadCommand",
    "GpgKeyring",
    "GpgKeyrings",
    "GpgKeys",
    "GpgRefreshKeysCommand",
    "GpgSecretKeys",
    "GpgTrust",
    "GpgTrustCommand",
    "GpgVerification",
    "GpgVerify",
    "dearmor",
    "delete_key",
    "export_key",
    "gen_key",
    "gpg",
    "import_key",
    "key",
    "refresh_keys",
    "trust",
]