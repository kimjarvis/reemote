# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.operations.server.shell import Shell
from reemote.execute import execute


def parse_dnf_list_installed(output):
    """Parses the output of the 'dnf list installed' command.

    This function processes the raw string output from `dnf list installed`
    and converts it into a structured list of packages. It is designed to
    handle the specific format of the DNF command's output.

    Key transformations include:

    - Skipping the header lines (e.g., "Installed Packages").
    - Correctly parsing lines where package names may contain spaces.
    - Identifying the package name and version by working backward from
      the repository field, which is expected to start with an '@' symbol.

    Args:
        output (str): The raw string stdout from a `dnf list installed` command.

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
    """Gets a list of installed DNF packages from a remote server.

    This reemote operation executes `dnf list installed` on the target
    host to retrieve a list of all installed packages. It then parses
    the command's output into a structured list of dictionaries.

    The final result is stored in the `stdout` attribute of the
    completed process object returned by the operation.

    Returns:
        list[dict]: A list of dictionaries, where each dictionary
                    represents a package and contains 'name' and
                    'version' keys.

    Examples:
        .. code:: python

            # To be used within a reemote execution plan. The parsed
            # list will be in the `stdout` of the result.
            result = yield Get_packages()
            packages = result.stdout
    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r = yield Shell("dnf list installed")
        r.cp.stdout = parse_dnf_list_installed(r.cp.stdout)
        # print(r.cp.stdout)
