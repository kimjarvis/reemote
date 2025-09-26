from reemote.operations.server.shell import Shell
from reemote.execute import execute


def parse_rpm_list_installed(output):
    packages = []
    lines = output.strip().splitlines()

    # Skip header line(s) - look for first line that doesn't start with "Installed"
    for line in lines:
        """
        Parses a package string into its name and version components.
    
        Args:
            package_str (str): The package string in the format "name-version-release.arch".
    
        Returns:
            tuple: A tuple containing the name and version as strings.
        """
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
