from reemote.operations.server.shell import Shell
from reemote.execute import execute


def parse_snap_list(output):
    """Parse the output of 'snap list' into a list of package dictionaries.

    The snap list output has columns:
    Name   Version             Rev    Tracking       Publisher   Notes

    We extract the package name from the first column and version from the second column.
    """
    lines = output.strip().split('\n')
    packages = []

    # Skip the header line (starts with "Name")
    for line in lines:
        line = line.strip()
        if not line or line.startswith('Name'):
            continue

        # Split by multiple spaces to handle the column-based format
        parts = line.split()
        if len(parts) < 2:
            continue

        # First column is name, second column is version
        name = parts[0]
        version = parts[1]

        packages.append({"name": name, "version": version})

    return packages


class Get_packages:
    """A reemote fact that gathers installed snap packages from a system.

    When executed by the reemote framework, this operation runs the
    `snap list` command on the target server. It then parses the
    command's output to create a structured list of all installed packages,
    including their names and versions.

    The final result is a list of dictionaries, which is attached to the
    `stdout` attribute of the completed process object. This operation is
    read-only and will always have `changed=False`.

    **Examples:**

    .. code:: python

        yield Get_packages()

    .. code:: bash

        reemote -i ~/reemote/inventory-proxmox-debian.py -s reemote/facts/snap/get_packages.py -c Get_packages
    """

    def execute(self):
        from reemote.operations.server.shell import Shell
        r = yield Shell("snap list")
        r.cp.stdout = parse_snap_list(r.cp.stdout)
        r.changed = False
        # print(r.cp.stdout)