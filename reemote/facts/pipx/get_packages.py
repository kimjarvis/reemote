# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.operations.server.shell import Shell
from reemote.execute import execute


def parse_pipx_list_installed(output):
    """Parses the raw text output from the 'pipx list' command.

    This function processes the standard output of 'pipx list' to extract
    a structured list of installed packages and their versions. It is
    designed to handle the specific format of the 'pipx list' command,
    including skipping the header and parsing each package line.

    Key transformations include:

    - Stripping leading/trailing whitespace from the input and individual lines.
    - Skipping the first two header lines of the output.
    - Splitting each line to separate the package name from its version string.
    - Ignoring empty or malformed lines.

    Args:
        output (str): The raw string output from the 'pipx list' command.

    Returns:
        list[dict]: A list of dictionaries, where each dictionary
            represents an installed package and contains 'name' and 'version'
            keys.
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
        r = yield Shell("pipx list")
        r.cp.stdout = parse_pipx_list_installed(r.cp.stdout)
        # print(r.cp.stdout)
