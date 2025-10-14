# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.operations.server.shell import Shell
from reemote.execute import execute

def parse_apt_list_installed(output):
    """Parses the output of 'apt list --installed' into a list of dictionaries.

    This helper function processes the raw string output from the
    `apt list --installed` command. It iterates through each line,
    skipping the "Listing..." header and any empty lines. For each valid
    package line, it accurately extracts the package name and its
    corresponding version number.

    Args:
        output (str): The raw string output from the `apt list --installed`
            command.

    Returns:
        list[dict]: A list of dictionaries, where each dictionary
            represents an installed package and contains 'name' and 'version'
            keys. Example: `[{'name': 'zlib1g', 'version': '1:1.2.11.dfsg-2'}]`.
    """
    lines = output.strip().split('\n')
    packages = []

    for line in lines:
        line = line.strip()
        if not line or line.startswith('Listing...'):
            continue

        # Split package name from the rest using first '/'
        if '/' not in line:
            continue

        name_part, rest = line.split('/', 1)
        name = name_part.strip()

        # Find the first space — version starts right after it
        space_index = rest.find(' ')
        if space_index == -1:
            continue

        # Extract everything after the first space
        after_space = rest[space_index + 1:]

        # Version is everything until the next space or '['
        version = after_space.split(' ', 1)[0].split('[', 1)[0].rstrip(',')

        packages.append({"name": name, "version": version})

    return packages

class Get_packages:
    """A reemote fact that gathers installed APT packages from a Debian-based system.

    When executed by the reemote framework, this operation runs the
    `apt list --installed` command on the target server. It then parses the
    command's output to create a structured list of all installed packages,
    including their names and versions.

    The final result is a list of dictionaries, which is attached to the
    `stdout` attribute of the completed process object. This operation is
    read-only and will always have `changed=False`.

    **Examples:**

    .. code:: python

        yield Get_packages()


    .. code:: bash

        reemote -i ~/reemote/inventory-proxmox-debian.py -s reemote/facts/apt/get_packages.py -c Get_packages
    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r = yield Shell("apt list --installed")
        r.cp.stdout = parse_apt_list_installed(r.cp.stdout)
        r.changed = False
        # print(r.cp.stdout)
