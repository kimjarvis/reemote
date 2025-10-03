from reemote.operations.server.shell import Shell
from reemote.execute import execute

def parse_apt_list_installed(output):
    """
    Parse the output of 'apt list --installed' into a list of dictionaries.
    Each dictionary has 'name' and 'version' keys.
    """
    lines = output.strip().split('\n')
    packages = []

    for line in lines:
        line = line.strip()
        if not line or line.startswith('Listing...'):
            continue

        # Split package name from the rest using first '/'
        if '/' not in line:
            continue

        name_part, rest = line.split('/', 1)
        name = name_part.strip()

        # Find the first space — version starts right after it
        space_index = rest.find(' ')
        if space_index == -1:
            continue

        # Extract everything after the first space
        after_space = rest[space_index + 1:]

        # Version is everything until the next space or '['
        version = after_space.split(' ', 1)[0].split('[', 1)[0].rstrip(',')

        packages.append({"name": name, "version": version})

    return packages

class Get_packages:
    """
    Returns a dictionary of installed packages.

    **Examples:**

    .. code:: python

        yield Get_packages()


    .. code:: bash

        reemote -i ~/reemote/inventory-proxmox-debian.py -s reemote/facts/apt/get_packages.py -c Get_packages

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r = yield Shell("apt list --installed")
        r.cp.stdout = parse_apt_list_installed(r.cp.stdout)
        # print(r.cp.stdout)
