# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.operations.server.shell import Shell
from reemote.execute import execute


def parse_pacman_list_installed(output):
    """Parses the raw output of 'pacman -Q' into a list of packages.

    This function processes the standard output from the `pacman -Q`
    command, which lists all installed packages and their versions. It
    is designed to handle the specific format of this command's output.

    The parsing logic involves:

    - Splitting the output into lines.
    - Skipping the first two header lines.
    - Extracting the package name (the first word) and the version
      (the rest of the line) for each package.

    Args:
        output (str): The raw string output from a 'pacman -Q' command.

    Returns:
        list[dict]: A list of dictionaries, where each dictionary
            represents an installed package and contains 'name' and
            'version' keys.
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
    """Retrieves a list of installed pacman packages from a host.

    This Reemote operation executes `pacman -Q` on the target host to
    get a list of all installed packages. It then parses this output
    into a structured list of dictionaries.

    The final result of this operation will have its `cp.stdout`
    attribute replaced with the parsed list. Each item in the list is a
    dictionary containing the package 'name' and 'version'.

    Examples:
        .. code:: python

            # Yield the operation within a Reemote task
            result = yield Get_packages()

            # The parsed list is in result.cp.stdout
            for package in result.cp.stdout:
                print(f"{package['name']}: {package['version']}")
    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r = yield Shell("pacman -Q")
        r.cp.stdout = parse_pacman_list_installed(r.cp.stdout)
        # print(r.cp.stdout)
