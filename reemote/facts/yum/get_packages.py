# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.operations.server.shell import Shell
from reemote.execute import execute


def parse_dnf_list_installed(output):
    """Parses the output of 'dnf list installed' into a structured list.

    This function processes the raw string output from commands like
    `dnf list installed` or `yum list installed`. It extracts the package
    name and version for each entry, producing a clean, structured list.

    Key transformations include:

    - Skipping header lines (e.g., "Installed Packages") and empty lines.
    - Handling package names that may contain spaces.
    - Identifying the version by locating the repository field, which starts
      with an '@' symbol, to reliably separate it from the name.
    - Assembling a list of dictionaries, each with 'name' and 'version' keys.

    Args:
        output (str): The raw standard output from the dnf/yum command.

    Returns:
        list[dict]: A list of dictionaries, where each dictionary represents
          an installed package and contains 'name' and 'version' keys.
    """
    packages = []
    lines = output.strip().splitlines()

    # Skip header line(s) - look for first line that doesn't start with "Installed"
    start_processing = False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("Installed Packages"):
            start_processing = True
            continue
        if not start_processing:
            continue

        # Split line by whitespace, but we need to be careful because name and version can have spaces
        # The format is: NAME VERSION REPO
        # We know repo starts with @, so we can find the last field that starts with @
        parts = line.split()
        if len(parts) < 3:
            continue

        # Find the index where the repo (starting with @) begins
        repo_index = -1
        for i in range(len(parts) - 1, -1, -1):
            if parts[i].startswith('@'):
                repo_index = i
                break

        if repo_index == -1:
            continue

        # Version is the part just before the repo
        version = parts[repo_index - 1]
        # Name is everything from the start up to (but not including) the version
        name_parts = parts[:repo_index - 1]
        name = ' '.join(name_parts) if len(name_parts) > 1 else name_parts[0]

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
        r = yield Shell("yum list installed")
        r.cp.stdout = parse_dnf_list_installed(r.cp.stdout)
        # print(r.cp.stdout)
