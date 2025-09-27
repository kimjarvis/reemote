from reemote.operations.server.shell import Shell
from reemote.execute import execute


def parse_dnf_list_installed(output):
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
