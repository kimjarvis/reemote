# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class Assemble:
    """
    A class to encapsulate the functionality of assembling configuration files from fragments.
    This mimics the behavior of the ansible.builtin.assemble module, which concatenates
    files from a source directory to create a destination file.

    Often a particular program will take a single configuration file and does not support
    a conf.d style structure where it is easy to build up the configuration from multiple sources.
    Assemble will take a directory of files that can be local or have already been transferred
    to the system, and concatenate them together to produce a destination file.

    Files are assembled in string sorting order.

    Attributes:
        src (str): An already existing directory full of source files.
        dest (str): A file to create using the concatenation of all of the source files.
        delimiter (str): A delimiter to separate the file contents.
        remote_src (bool): If False, it will search for src at originating/master machine.
                          If True, it will go to the remote/target machine for the src.
        regexp (str): Assemble files only if the given regular expression matches the filename.
        ignore_hidden (bool): A boolean that controls if files that start with a . will be included or not.
        backup (bool): Create a backup file (if true), including the timestamp information.
        decrypt (bool): This option controls the auto-decryption of source files using vault.
        validate (str): The validation command to run before copying into place.
        owner (str): Name of the user that should own the filesystem object.
        group (str): Name of the group that should own the filesystem object.
        mode (str): The permissions the resulting filesystem object should have.
        attributes (str): The attributes the resulting filesystem object should have.
        seuser (str): The user part of the SELinux filesystem object context.
        serole (str): The role part of the SELinux filesystem object context.
        setype (str): The type part of the SELinux filesystem object context.
        selevel (str): The level part of the SELinux filesystem object context.
        unsafe_writes (bool): Influence when to use atomic operation to prevent data corruption.
        guard (bool): If `False` the commands will not be executed.

    **Examples:**

    .. code:: python

        # Assemble from fragments from a directory
        r = yield Assemble(src="/etc/someapp/fragments", dest="/etc/someapp/someapp.conf")

        # Insert the provided delimiter between fragments
        r = yield Assemble(
            src="/etc/someapp/fragments",
            dest="/etc/someapp/someapp.conf",
            delimiter="### START FRAGMENT ###"
        )

        # Assemble a new "sshd_config" file into place, after passing validation with sshd
        r = yield Assemble(
            src="/etc/ssh/conf.d/",
            dest="/etc/ssh/sshd_config",
            validate="/usr/sbin/sshd -t -f %s"
        )

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Files are assembled in string sorting order.
        - The command construction handles various file operations and validation.
    """

    def __init__(self,
                 src: str,
                 dest: str,
                 delimiter: str = None,
                 remote_src: bool = True,
                 regexp: str = None,
                 ignore_hidden: bool = False,
                 backup: bool = False,
                 decrypt: bool = True,
                 validate: str = None,
                 owner: str = None,
                 group: str = None,
                 mode: str = None,
                 attributes: str = None,
                 seuser: str = None,
                 serole: str = None,
                 setype: str = None,
                 selevel: str = None,
                 unsafe_writes: bool = False,
                 guard: bool = True):

        self.src = src
        self.dest = dest
        self.delimiter = delimiter
        self.remote_src = remote_src
        self.regexp = regexp
        self.ignore_hidden = ignore_hidden
        self.backup = backup
        self.decrypt = decrypt
        self.validate = validate
        self.owner = owner
        self.group = group
        self.mode = mode
        self.attributes = attributes
        self.seuser = seuser
        self.serole = serole
        self.setype = setype
        self.selevel = selevel
        self.unsafe_writes = unsafe_writes
        self.guard = guard

    def __repr__(self):
        return (f"Assemble(src={self.src!r}, "
                f"dest={self.dest!r}, "
                f"delimiter={self.delimiter!r}, "
                f"remote_src={self.remote_src!r}, "
                f"regexp={self.regexp!r}, "
                f"ignore_hidden={self.ignore_hidden!r}, "
                f"backup={self.backup!r}, "
                f"decrypt={self.decrypt!r}, "
                f"validate={self.validate!r}, "
                f"owner={self.owner!r}, "
                f"group={self.group!r}, "
                f"mode={self.mode!r}, "
                f"attributes={self.attributes!r}, "
                f"seuser={self.seuser!r}, "
                f"serole={self.serole!r}, "
                f"setype={self.setype!r}, "
                f"selevel={self.selevel!r}, "
                f"unsafe_writes={self.unsafe_writes!r}, "
                f"guard={self.guard!r})")

    def execute(self):
        # Build the assemble command
        cmd_parts = ["assemble"]

        # Add required parameters
        cmd_parts.extend([f"src={self.src}", f"dest={self.dest}"])

        # Add optional parameters
        if self.delimiter is not None:
            cmd_parts.append(f"delimiter={self.delimiter}")
        if not self.remote_src:
            cmd_parts.append("remote_src=false")
        if self.regexp is not None:
            cmd_parts.append(f"regexp={self.regexp}")
        if self.ignore_hidden:
            cmd_parts.append("ignore_hidden=true")
        if self.backup:
            cmd_parts.append("backup=true")
        if not self.decrypt:
            cmd_parts.append("decrypt=false")
        if self.validate is not None:
            cmd_parts.append(f"validate={self.validate}")
        if self.owner is not None:
            cmd_parts.append(f"owner={self.owner}")
        if self.group is not None:
            cmd_parts.append(f"group={self.group}")
        if self.mode is not None:
            cmd_parts.append(f"mode={self.mode}")
        if self.attributes is not None:
            cmd_parts.append(f"attributes={self.attributes}")
        if self.seuser is not None:
            cmd_parts.append(f"seuser={self.seuser}")
        if self.serole is not None:
            cmd_parts.append(f"serole={self.serole}")
        if self.setype is not None:
            cmd_parts.append(f"setype={self.setype}")
        if self.selevel is not None:
            cmd_parts.append(f"selevel={self.selevel}")
        if self.unsafe_writes:
            cmd_parts.append("unsafe_writes=true")

        cmd = " ".join(cmd_parts)

        # Execute the command
        r = yield Command(cmd, guard=self.guard)
        r.changed = True