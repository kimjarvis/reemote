# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.operations.server.shell import Shell
from reemote.execute import execute

def parse_dpkg_list_installed(output):
    """Parses the raw string output of `dpkg-query -W`.

    This function processes the multiline string returned by the
    `dpkg-query -W` command, which lists installed Debian packages. It
    converts this raw text into a structured list of dictionaries.

    Each line of the input is expected to contain the package name followed
    by its version, separated by whitespace. Lines that are empty or
    malformed are skipped.

    Args:
        output (str): The raw standard output from the `dpkg-query -W`
            command.

    Returns:
        list[dict]: A list of dictionaries, where each dictionary
            represents an installed package and contains 'name' and
            'version' keys.
    """
    packages = []
    lines = output.strip().split('\n')
    for line in lines:
        if line.strip():  # Skip empty lines
            # Split by whitespace and take the first part as name, rest as version
            parts = line.split()
            if len(parts) >= 2:
                name = parts[0]
                # Join remaining parts as version (in case version contains spaces)
                version = ' '.join(parts[1:])
                packages.append({"name": name, "version": version})
    return packages

class Get_packages:
    """Retrieves a list of installed Debian packages.

    This operation executes `dpkg-query -W` on the remote host and parses
    the output. The result is a structured list of installed packages,
    which replaces the `stdout` in the final result object.

    Each package is represented as a dictionary with 'name' and 'version'
    keys.

    **Examples:**

    .. code:: python

        # In a reemote task function, get a list of all installed packages
        packages_result = yield Get_packages()

        # The parsed list is in the stdout attribute of the result
        print(packages_result.stdout)
        # Outputs: [{'name': 'wget', 'version': '1.20.3-1ubuntu1'}, ...]
    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r = yield Shell("dpkg-query -W")
        print(r)
        r.cp.stdout = parse_dpkg_list_installed(r.cp.stdout)
        # print(r.cp.stdout)
