class Chown:
    def __init__(self, target: str,
                 user: str | None = None,
                 group: str | None = None,
                 options=None,
                 sudo: bool = False):

        self.target = target
        self.user = user
        self.group = group
        self.options = options
        self.sudo = sudo

        if options is None:
            options = []

        command = "chown"
        user_group = None

        if user and group:
            user_group = f"{user}:{group}"
        elif user:
            user_group = user
        elif group:
            command = "chgrp"
            user_group = group
        else:
            raise ValueError("Either user or group must be specified")

        op = []
        op.append(command)
        op.extend(options)
        op.append(user_group)
        op.append(target)

        self.chown = " ".join(op)

    def __repr__(self):
        """
        Return an unambiguous string representation of the Chown object.
        This representation can be used to recreate the object.
        """
        return (f"Chown(target={self.target!r}, user={self.user!r}, "
                f"group={self.group!r}, options={self.options!r}, sudo={self.sudo!r})")

    def execute(self):
        yield f"echo {self}"
        _sudo = "sudo -S " if self.sudo else ""

        # Get initial file info
        r0 = yield f"{_sudo}ls -ld {self.target}"

        # Execute chown command
        r1 = yield f"{_sudo}{self.chown}"

        # Get final file info to check if changed
        r2 = yield f"{_sudo}ls -ld {self.target}"

        # Set changed flag if the output differs
        if r0.cp.stdout != r2.cp.stdout:
            r1.changed = True
