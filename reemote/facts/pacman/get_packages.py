from reemote.operations.server.shell import Shell
from reemote.execute import execute


def parse_pip_list_installed(output):
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
    Returns a dictionary of installed pacman packages.

    **Examples:**

    .. code:: python

        yield Get_packages()

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r = yield Shell("pacman -Q")
        r.cp.stdout = parse_pip_list_installed(r.cp.stdout)
        # print(r.cp.stdout)
