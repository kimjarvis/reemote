# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.operations.server.shell import Shell
from reemote.execute import execute


def parse_pip_list_installed(output):
    """Parses the stdout from 'pip list' into a list of packages.

    This function processes the raw string output from the `pip list`
    command. It skips the header lines and parses each subsequent line
    to extract the package name and its version.

    The function is designed to handle the specific two-column format
    of the `pip list` command, correctly separating the package name
    from the version string.

    Args:
        output (str): The raw string output from a `pip list` command.

    Returns:
        list[dict]: A list of dictionaries, where each dictionary
        contains the 'name' and 'version' of an installed package.
    """
    packages = []

    lines = output.strip().splitlines()

    # Skip header lines (first two)
    for line in lines[2:]:
        line = line.strip()
        if not line:
            continue

        # Split by whitespace, but keep version as everything after first word
        parts = line.split()
        if len(parts) < 2:
            continue  # skip malformed lines

        name = parts[0]
        version = " ".join(parts[1:])

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
        r = yield Shell("pip list")
        r.cp.stdout = parse_pip_list_installed(r.cp.stdout)
        # print(r.cp.stdout)
