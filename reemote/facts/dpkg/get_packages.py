from reemote.operations.server.shell import Shell
from reemote.execute import execute

def parse_dpkg_list_installed(output):
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
    """
    Returns a dictionary of installed packages.

    **Examples:**

    .. code:: python

        yield Get_packages()

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r = yield Shell("dpkg-query -W")
        r.cp.stdout = parse_dpkg_list_installed(r.cp.stdout)
        # print(r.cp.stdout)
