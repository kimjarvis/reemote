from reemote.operations.filesystem.chown import Chown
from reemote.result import Result


class Directory:
    """
        A class to manage directory states on a filesystem.

        Attributes:
            path (str): The absolute or relative path of the directory to manage. This is the target directory whose state will be checked or modified.
            present (bool): Indicates whether the directory should exist (`True`) or not (`False`) on the system. If `True`, the directory will be created if it does not exist. If `False`, the directory will be removed if it exists.
            user (Optional[str]): The new user owner. Defaults to `None`.
            group (Optional[str]): The new group owner. Defaults to `None`.
            guard (bool): If `False` the commands will not be executed.
            sudo (bool): If `True`, the commands will be executed with `sudo` privileges. Defaults to `False`.
            su (bool): If `True`, the commands will be executed with `su` privileges.

        Usage:
            This class is designed to be used in a generator-based workflow where commands are yielded for execution.
            It supports creating or removing directories based on the `present` flag and allows privilege escalation via `sudo`.

        Notes:
            - Commands are constructed based on the `present` and `sudo` flags.
            - The `changed` flag is set if the directory state changes after execution.
    """
    def __init__(self,
                 path: str,
                 present: bool,
                 user: str = None,
                 group: str = None,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False
                 ):
        self.path = path
        self.present = present
        self.user = user
        self.group = group
        self.guard = guard
        self.sudo = sudo
        self.su = su

    def __repr__(self):
        return (f"Directory(path={self.path!r}, present={self.present!r}, "
                f"user={self.user!r}, group={self.group!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        r0 = yield f"composite {self}"
        r0.executed = self.guard
        _sudo: str = "sudo -S " if self.sudo else ""
        _su: str = "su" if self.su else ""

        # Check whether the directory exists
        r1: Result = yield self.guard, f"{_sudo}[ -d {self.path} ]"
        # # print(r1)
        #
        # # Directory should be present, but it does not exist, so create it
        mguard = self.present and r1.cp.returncode != 0
        r2 = yield mguard and self.guard, f'{_sudo}{_su}"mkdir -p {self.path}"'
        # print(r2)
        if self.guard:
            r2.changed = r2.executed
        #
        # # Directory should not be present, but it exists, so remove it
        rguard = (not self.present) and r1.cp.returncode == 0
        r3 = yield rguard and self.guard, f'{_sudo}{_su}"rmdir -p {self.path}"'
        # print(r3)
        if self.guard:
            r3.changed = r3.executed
            r0.changed = r2.changed or r3.changed
        #
        # This is contingent on either a user or group being specified
        # Does not yield a result, because the component operations have not been executed yet, so we cannot tell whether it changed anything
        # Cannot be guarded because its action is to push to the stack, if it didn't do that the operations would not always be executed.
        # The operation is dependent on static values, the parameters of Directory() these will be the same on all hosts
        # so, we can use if here.
        #
        if self.present and (self.group is not None or self.user is not None):
            yield Chown(guard=self.guard, path=self.path, user=self.user, group=self.group, sudo=self.sudo, su=self.su)
