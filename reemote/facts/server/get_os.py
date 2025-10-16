class Get_OS:
    def __init__(self, field: str = "PRETTY_NAME", guard: bool = True, sudo: bool = False, su: bool = False):
        self.field = field
        self.guard = guard
        self.sudo = sudo
        self.su = su

    def execute(self):
        from reemote.operations.server.shell import Shell
        import re

        # Yield the shell operation to the framework
        result = yield Shell("cat /etc/os-release", sudo=self.sudo, su=self.su)

        # Parse the result and store the value
        lines = result.cp.stdout.split('\n')

        for line in lines:
            line = line.strip()
            if line and line.startswith(f'{self.field}='):
                # Extract everything after the equals sign
                value = line.split('=', 1)[1].strip()

                # Remove surrounding quotes if present
                if value and ((value.startswith('"') and value.endswith('"')) or
                              (value.startswith("'") and value.endswith("'"))):
                    value = value[1:-1]

                # Store the parsed value
                result.cp.stdout = value
                break

    def __repr__(self):
        return (f"Get_OS("
                f"field={self.field!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r}, "
                f"su={self.su!r})")