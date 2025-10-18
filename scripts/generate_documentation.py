# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from generate_autodoc import generate_sphinx_documentation

def main():
    # Utilities
    generate_sphinx_documentation(
        "reemote/utilities",
        "docs/source/utilities.rst",
        "utilities",
        "reemote"
    )
    # Commands
    generate_sphinx_documentation(
        "reemote/commands/apk",
        "docs/source/commands/apk.rst",
        "apk commands",
        "reemote.commands"
    )
    generate_sphinx_documentation(
        "reemote/commands/apt",
        "docs/source/commands/apt.rst",
        "apt commands",
        "reemote.commands"
    )
    generate_sphinx_documentation(
        "reemote/commands/dnf",
        "docs/source/commands/dnf.rst",
        "dnf commands",
        "reemote.commands"
    )
    generate_sphinx_documentation(
        "reemote/commands/dpkg",
        "docs/source/commands/dpkg.rst",
        "dpkg commands",
        "reemote.commands"
    )
    generate_sphinx_documentation(
        "reemote/commands/pacman",
        "docs/source/commands/pacman.rst",
        "pacman commands",
        "reemote.commands"
    )
    generate_sphinx_documentation(
        "reemote/commands/pip",
        "docs/source/commands/pip.rst",
        "pip commands",
        "reemote.commands"
    )
    generate_sphinx_documentation(
        "reemote/commands/pipx",
        "docs/source/commands/pipx.rst",
        "pipx commands",
        "reemote.commands"
    )
    generate_sphinx_documentation(
        "reemote/commands/yum",
        "docs/source/commands/yum.rst",
        "yum commands",
        "reemote.commands"
    )
    generate_sphinx_documentation(
        "reemote/commands/zypper",
        "docs/source/commands/zypper.rst",
        "zypper commands",
        "reemote.commands"
    )
    # Operations
    generate_sphinx_documentation(
        "reemote/operations/apt",
        "docs/source/operations/apt.rst",
        "apt operations",
        "reemote.operations"
    )
    generate_sphinx_documentation(
        "reemote/operations/apk",
        "docs/source/operations/apk.rst",
        "apk operations",
        "reemote.operations"
    )
    generate_sphinx_documentation(
        "reemote/operations/dnf",
        "docs/source/operations/dnf.rst",
        "dnf operations",
        "reemote.operations"
    )
    generate_sphinx_documentation(
        "reemote/operations/dpkg",
        "docs/source/operations/dpkg.rst",
        "dpkg operations",
        "reemote.operations"
    )
    generate_sphinx_documentation(
        "reemote/operations/builtin",
        "docs/source/operations/builtin.rst",
        "builtin operations",
        "reemote.operations"
    )
    generate_sphinx_documentation(
        "reemote/operations/filesystem",
        "docs/source/operations/filesystem.rst",
        "filesystem operations",
        "reemote.operations"
    )
    generate_sphinx_documentation(
        "reemote/operations/pacman",
        "docs/source/operations/pacman.rst",
        "pacman operations",
        "reemote.operations"
    )
    generate_sphinx_documentation(
        "reemote/operations/pip",
        "docs/source/operations/pip.rst",
        "pip operations",
        "reemote.operations"
    )
    generate_sphinx_documentation(
        "reemote/operations/pipx",
        "docs/source/operations/pipx.rst",
        "pipx operations",
        "reemote.operations"
    )
    generate_sphinx_documentation(
        "reemote/operations/scp",
        "docs/source/operations/scp.rst",
        "scp operations",
        "reemote.operations"
    )
    generate_sphinx_documentation(
        "reemote/operations/server",
        "docs/source/operations/server.rst",
        "server operations",
        "reemote.operations"
    )
    generate_sphinx_documentation(
        "reemote/operations/sftp",
        "docs/source/operations/sftp.rst",
        "sftp operations",
        "reemote.operations"
    )
    generate_sphinx_documentation(
        "reemote/operations/users",
        "docs/source/operations/users.rst",
        "users operations",
        "reemote.operations"
    )
    generate_sphinx_documentation(
        "reemote/operations/yum",
        "docs/source/operations/yum.rst",
        "yum operations",
        "reemote.operations"
    )
    generate_sphinx_documentation(
        "reemote/operations/zypper",
        "docs/source/operations/zypper.rst",
        "zypper operations",
        "reemote.operations"
    )
    # Facts
    generate_sphinx_documentation(
        "reemote/facts/apt",
        "docs/source/facts/apt.rst",
        "apt facts",
        "reemote.facts"
    )
    generate_sphinx_documentation(
        "reemote/facts/apk",
        "docs/source/facts/apk.rst",
        "apk facts",
        "reemote.facts"
    )
    generate_sphinx_documentation(
        "reemote/facts/command",
        "docs/source/facts/command.rst",
        "command facts",
        "reemote.facts"
    )
    generate_sphinx_documentation(
        "reemote/facts/dnf",
        "docs/source/facts/dnf.rst",
        "dnf facts",
        "reemote.facts"
    )
    generate_sphinx_documentation(
        "reemote/facts/dpkg",
        "docs/source/facts/dpkg.rst",
        "dpkg facts",
        "reemote.facts"
    )
    generate_sphinx_documentation(
        "reemote/facts/inventory",
        "docs/source/facts/inventory.rst",
        "inventory facts",
        "reemote.facts"
    )
    generate_sphinx_documentation(
        "reemote/facts/lxc",
        "docs/source/facts/lxc.rst",
        "lxc facts",
        "reemote.facts"
    )
    generate_sphinx_documentation(
        "reemote/facts/pacman",
        "docs/source/facts/pacman.rst",
        "pacman facts",
        "reemote.facts"
    )
    generate_sphinx_documentation(
        "reemote/facts/pip",
        "docs/source/facts/pip.rst",
        "pip facts",
        "reemote.facts"
    )
    generate_sphinx_documentation(
        "reemote/facts/pipx",
        "docs/source/facts/pipx.rst",
        "pipx facts",
        "reemote.facts"
    )
    generate_sphinx_documentation(
        "reemote/facts/server",
        "docs/source/facts/server.rst",
        "server facts",
        "reemote.facts"
    )
    generate_sphinx_documentation(
        "reemote/facts/sftp",
        "docs/source/facts/sftp.rst",
        "sftp facts",
        "reemote.facts"
    )
    generate_sphinx_documentation(
        "reemote/facts/yum",
        "docs/source/facts/yum.rst",
        "yum facts",
        "reemote.facts"
    )
    generate_sphinx_documentation(
        "reemote/facts/zypper",
        "docs/source/facts/zypper.rst",
        "zypper facts",
        "reemote.facts"
    )
    # Deployments
    generate_sphinx_documentation(
        "reemote/deployments/builtin",
        "docs/source/deployments/builtin.rst",
        "builtin deployments",
        "reemote.deployments"
    )
    generate_sphinx_documentation(
        "reemote/deployments/lxc",
        "docs/source/deployments/lxc.rst",
        "lxc deployments",
        "reemote.deployments"
    )
    generate_sphinx_documentation(
        "reemote/deployments/nginx",
        "docs/source/deployments/nginx.rst",
        "nginx deployments",
        "reemote.deployments"
    )
    generate_sphinx_documentation(
        "reemote/deployments/rust",
        "docs/source/deployments/rust.rst",
        "rust deployments",
        "reemote.deployments"
    )
    # Callbacks
    generate_sphinx_documentation(
        "reemote/callbacks/progress",
        "docs/source/callbacks/progress.rst",
        "progress callbacks",
        "reemote.callbacks"
    )



# Example usage
if __name__ == "__main__":
    main()