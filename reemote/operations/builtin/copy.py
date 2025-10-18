# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class Copy:
    """
    A class to encapsulate the functionality of copying files to remote locations.
    It allows users to specify source and destination paths, along with various
    file system meta-information settings like permissions, ownership, and SELinux contexts.

    Attributes:
        src (str): Local path to a file to copy to the remote server.
        dest (str): Remote absolute path where the file should be copied to.
        content (str): When used instead of src, sets the contents of a file directly.
        backup (bool): Create a backup file including timestamp information.
        force (bool): Influence whether the remote file must always be replaced.
        mode (str): The permissions of the destination file or directory.
        owner (str): Name of the user that should own the filesystem object.
        group (str): Name of the group that should own the filesystem object.
        directory_mode (str): Set access permissions of newly created directories.
        remote_src (bool): Influence whether src needs to be transferred or already present remotely.
        follow (bool): Follow filesystem links in the destination.
        local_follow (bool): Follow filesystem links in the source tree.
        decrypt (bool): Control auto-decryption of source files using vault.
        unsafe_writes (bool): Allow unsafe writes when atomic operations fail.
        validate (str): Validation command to run before copying the file.
        seuser (str): SELinux user context.
        serole (str): SELinux role context.
        setype (str): SELinux type context.
        selevel (str): SELinux level context.
        attributes (str): File attributes to set on the resulting filesystem object.
        checksum (str): SHA1 checksum of the file being transferred.
        guard (bool): If `False` the commands will not be executed.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        # Copy file with owner and permissions
        r = yield Copy(src="/srv/myfiles/foo.conf", dest="/etc/foo.conf",
                      owner="foo", group="foo", mode="0644")
        print(r.cp.dest)

        # Copy using inline content
        r = yield Copy(content="# This file was moved to /etc/other.conf",
                      dest="/etc/mine.conf")
        print(r.cp.dest)

        # Copy file with backup
        r = yield Copy(src="/mine/ntp.conf", dest="/etc/ntp.conf",
                      owner="root", group="root", mode="0644", backup=True)
        print(r.cp.backup_file)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the various parameters and flags.
        - For Windows targets, use the appropriate Windows copy module instead.
    """

    def __init__(self,
                 src: str = None,
                 dest: str = None,
                 content: str = None,
                 backup: bool = False,
                 force: bool = True,
                 mode: str = None,
                 owner: str = None,
                 group: str = None,
                 directory_mode: str = None,
                 remote_src: bool = False,
                 follow: bool = False,
                 local_follow: bool = True,
                 decrypt: bool = True,
                 unsafe_writes: bool = False,
                 validate: str = None,
                 seuser: str = None,
                 serole: str = None,
                 setype: str = None,
                 selevel: str = None,
                 attributes: str = None,
                 checksum: str = None,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):

        self.src = src
        self.dest = dest
        self.content = content
        self.backup = backup
        self.force = force
        self.mode = mode
        self.owner = owner
        self.group = group
        self.directory_mode = directory_mode
        self.remote_src = remote_src
        self.follow = follow
        self.local_follow = local_follow
        self.decrypt = decrypt
        self.unsafe_writes = unsafe_writes
        self.validate = validate
        self.seuser = seuser
        self.serole = serole
        self.setype = setype
        self.selevel = selevel
        self.attributes = attributes
        self.checksum = checksum
        self.guard = guard
        self.sudo = sudo
        self.su = su

    def __repr__(self):
        return (f"Copy(src={self.src!r}, dest={self.dest!r}, "
                f"content={self.content!r}, backup={self.backup!r}, "
                f"force={self.force!r}, mode={self.mode!r}, "
                f"owner={self.owner!r}, group={self.group!r}, "
                f"directory_mode={self.directory_mode!r}, "
                f"remote_src={self.remote_src!r}, follow={self.follow!r}, "
                f"local_follow={self.local_follow!r}, decrypt={self.decrypt!r}, "
                f"unsafe_writes={self.unsafe_writes!r}, validate={self.validate!r}, "
                f"seuser={self.seuser!r}, serole={self.serole!r}, "
                f"setype={self.setype!r}, selevel={self.selevel!r}, "
                f"attributes={self.attributes!r}, checksum={self.checksum!r}, "
                f"guard={self.guard!r}, sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        # Build the command arguments
        args = []

        if self.src:
            args.append(f"src={self.src}")
        if self.dest:
            args.append(f"dest={self.dest}")
        if self.content:
            args.append(f"content={self.content!r}")
        if self.backup:
            args.append("backup=yes")
        if not self.force:
            args.append("force=no")
        if self.mode:
            args.append(f"mode={self.mode}")
        if self.owner:
            args.append(f"owner={self.owner}")
        if self.group:
            args.append(f"group={self.group}")
        if self.directory_mode:
            args.append(f"directory_mode={self.directory_mode}")
        if self.remote_src:
            args.append("remote_src=yes")
        if self.follow:
            args.append("follow=yes")
        if not self.local_follow:
            args.append("local_follow=no")
        if not self.decrypt:
            args.append("decrypt=no")
        if self.unsafe_writes:
            args.append("unsafe_writes=yes")
        if self.validate:
            args.append(f"validate={self.validate!r}")
        if self.seuser:
            args.append(f"seuser={self.seuser}")
        if self.serole:
            args.append(f"serole={self.serole}")
        if self.setype:
            args.append(f"setype={self.setype}")
        if self.selevel:
            args.append(f"selevel={self.selevel}")
        if self.attributes:
            args.append(f"attributes={self.attributes}")
        if self.checksum:
            args.append(f"checksum={self.checksum}")

        # Construct the ansible command
        cmd_args = " ".join(args)
        cmd = f"ansible.builtin.copy {cmd_args}"

        # Execute the command
        r = yield Command(cmd, guard=self.guard, sudo=self.sudo, su=self.su)
        r.changed = True