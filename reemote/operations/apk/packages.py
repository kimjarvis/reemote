from reemote.printers import print_ssh_completed_process

class Packages:
    def __init__(self,
                 packages: list[str],
                 present: bool,
                 sudo: bool = False,
                 su: bool = False):
        self.packages = packages
        self.present = present
        self.sudo = sudo
        self.su = su

        op = []
        # op.append(command)
        op.extend(self.packages)

        self.op = " ".join(op)

    def __repr__(self):
        return (f"Packages(packages={self.packages!r}, present={self.present!r},"
                f"sudo={self.sudo!r},su={self.su!r})")

    def execute(self):
        r = yield f"echo {self}"
        _sudo = "sudo -S " if self.sudo else ""
        _su = "su -c " if self.su else ""

        # rt = yield f"{_su}"
        # print_ssh_completed_process(rt.cp)

        r1 = yield f"{_sudo}apk info"

        if self.present:
            r2 = yield f"{_sudo}{_su}'apk add {self.op}'"
        else:
            r2 = yield f"{_sudo}{_su}'apk del {self.op}'"

        r3 = yield f"{_sudo}apk info"

        # Set changed flag if the output differs
        if r1.cp.stdout != r3.cp.stdout:
            r2.changed = True
