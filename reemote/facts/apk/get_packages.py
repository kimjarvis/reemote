from reemote.operations.server.shell import Shell
from reemote.execute import execute

def parse_apk_list_installed(output):
    packages = []
    lines = output.strip().split('\n')
    for line in lines:
        if not line.strip():
            continue
        # Split on the last hyphen that precedes the version number
        # Version format is typically like "2.3.2-r1", "3.22.1-r0", etc.
        # We look for the last hyphen before the version pattern
        parts = line.rsplit('-', 2)
        if len(parts) >= 3:
            # Reconstruct name from all but the last two parts (in case name has hyphens)
            name = '-'.join(parts[:-2])
            # Last two parts form the version (e.g., '2.3.2' and 'r1' -> '2.3.2-r1')
            version = parts[-2] + '-' + parts[-1]
            packages.append({"name": name, "version": version})
        else:
            # Fallback: if splitting doesn't work as expected, treat everything before last hyphen as name
            last_hyphen = line.rfind('-')
            if last_hyphen != -1:
                name = line[:last_hyphen]
                version = line[last_hyphen+1:]
                packages.append({"name": name, "version": version})
            else:
                # If no hyphen found, treat whole line as name with empty version
                packages.append({"name": line, "version": ""})
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
        r = yield Shell("apk info -v")
        r.cp.stdout = parse_apk_list_installed(r.cp.stdout)
        # print(r.cp.stdout)
