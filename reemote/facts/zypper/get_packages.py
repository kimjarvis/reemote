# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.operations.server.shell import Shell
from reemote.execute import execute


def parse_rpm_list_installed(output):
    """Parses the raw string output of `rpm -qa` into a structured list.

    This function processes the multiline string returned by the `rpm -qa`
    command, which lists all installed RPM packages. It intelligently
    separates each line into a package name and its corresponding version string.

    The parsing logic is designed to handle package names that may contain
    hyphens by identifying the version string, which typically starts
    just before the architecture part of the full package identifier.

    Args:
        output (str): The raw stdout from the `rpm -qa` command.

    Returns:
        list[dict]: A list of dictionaries, where each dictionary
            represents an installed package and contains 'name' and
            'version' keys.
    """
    packages = []
    lines = output.strip().splitlines()

    # Skip header line(s) - look for first line that doesn't start with "Installed"
    for line in lines:
        # Split the string by hyphens
        parts = line.split('-')

        # The version starts after the last numeric part before the architecture
        # Find the index where the version starts
        for i in range(len(parts) - 1, 0, -1):
            if parts[i].replace('.', '').isdigit() or parts[i].startswith('p'):
                break

        # Reconstruct the name by joining all parts before the version
        name = '-'.join(parts[:i])

        # Reconstruct the version by joining all parts from the version onward
        version = '-'.join(parts[i:])

        packages.append({"name": name, "version": version})

    return packages

class Get_packages:
    """
    Returns a dictionary of installed packages.

    **Examples:**

    .. code:: python

        yield Get_packages()

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r = yield Shell("rpm -qa")
        r.cp.stdout = parse_rpm_list_installed(r.cp.stdout)
        # print(r.cp.stdout)
