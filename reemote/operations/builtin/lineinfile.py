# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class LineInFile:
    """
    A class to encapsulate the functionality of managing lines in text files,
    similar to Ansible's lineinfile module. This class ensures a particular
    line is present or absent in a file, with support for regular expressions,
    backups, and various file attributes.

    Attributes:
        path (str): The file to modify.
        line (str): The line to insert/replace into the file.
        state (str): Whether the line should be there or not ("present" or "absent").
        regexp (str): The regular expression to look for in every line of the file.
        search_string (str): The literal string to look for in every line of the file.
        insertafter (str): Insert line after the last match of specified regular expression.
        insertbefore (str): Insert line before the last match of specified regular expression.
        create (bool): If specified, the file will be created if it does not already exist.
        backup (bool): Create a backup file including the timestamp information.
        firstmatch (bool): Work with the first line that matches the given regular expression.
        backrefs (bool): If set, line can contain backreferences that will get populated.
        owner (str): Name of the user that should own the filesystem object.
        group (str): Name of the group that should own the filesystem object.
        mode (str): The permissions the resulting filesystem object should have.
        attributes (str): The attributes the resulting filesystem object should have.
        seuser (str): The user part of the SELinux filesystem object context.
        serole (str): The role part of the SELinux filesystem object context.
        setype (str): The type part of the SELinux filesystem object context.
        selevel (str): The level part of the SELinux filesystem object context.
        unsafe_writes (bool): Influence when to use atomic operation.
        validate (str): The validation command to run before copying the updated file.
        guard (bool): If `False` the commands will not be executed.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        # Ensure SELinux is set to enforcing mode
        r = yield LineInFile(
            path="/etc/selinux/config",
            regexp="^SELINUX=",
            line="SELINUX=enforcing"
        )

        # Make sure group wheel is not in the sudoers configuration
        r = yield LineInFile(
            path="/etc/sudoers",
            state="absent",
            regexp="^%wheel"
        )

        # Replace a localhost entry with our own
        r = yield LineInFile(
            path="/etc/hosts",
            regexp="^127\\.0\\.0\\.1",
            line="127.0.0.1 localhost",
            owner="root",
            group="root",
            mode="0644"
        )

        # Ensure the default Apache port is 8080
        r = yield LineInFile(
            path="/etc/httpd/conf/httpd.conf",
            regexp="^Listen ",
            insertafter="^#Listen ",
            line="Listen 8080"
        )

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - This implementation mimics the behavior of Ansible's lineinfile module.
        - Commands are constructed based on the provided parameters and flags.
        - Mutually exclusive parameters (backrefs/regxp/search_string, insertafter/insertbefore) are handled appropriately.
    """

    def __init__(self,
                 path: str,
                 line: str = None,
                 state: str = "present",
                 regexp: str = None,
                 search_string: str = None,
                 insertafter: str = None,
                 insertbefore: str = None,
                 create: bool = False,
                 backup: bool = False,
                 firstmatch: bool = False,
                 backrefs: bool = False,
                 owner: str = None,
                 group: str = None,
                 mode: str = None,
                 attributes: str = None,
                 seuser: str = None,
                 serole: str = None,
                 setype: str = None,
                 selevel: str = None,
                 unsafe_writes: bool = False,
                 validate: str = None,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):

        self.path = path
        self.line = line
        self.state = state
        self.regexp = regexp
        self.search_string = search_string
        self.insertafter = insertafter
        self.insertbefore = insertbefore
        self.create = create
        self.backup = backup
        self.firstmatch = firstmatch
        self.backrefs = backrefs
        self.owner = owner
        self.group = group
        self.mode = mode
        self.attributes = attributes
        self.seuser = seuser
        self.serole = serole
        self.setype = setype
        self.selevel = selevel
        self.unsafe_writes = unsafe_writes
        self.validate = validate
        self.guard = guard
        self.sudo = sudo
        self.su = su

    def __repr__(self):
        return (f"LineInFile(path={self.path!r}, "
                f"line={self.line!r}, "
                f"state={self.state!r}, "
                f"regexp={self.regexp!r}, "
                f"search_string={self.search_string!r}, "
                f"insertafter={self.insertafter!r}, "
                f"insertbefore={self.insertbefore!r}, "
                f"create={self.create!r}, "
                f"backup={self.backup!r}, "
                f"firstmatch={self.firstmatch!r}, "
                f"backrefs={self.backrefs!r}, "
                f"owner={self.owner!r}, "
                f"group={self.group!r}, "
                f"mode={self.mode!r}, "
                f"attributes={self.attributes!r}, "
                f"seuser={self.seuser!r}, "
                f"serole={self.serole!r}, "
                f"setype={self.setype!r}, "
                f"selevel={self.selevel!r}, "
                f"unsafe_writes={self.unsafe_writes!r}, "
                f"validate={self.validate!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r}, "
                f"su={self.su!r})")

    def execute(self):
        # Build the command arguments
        args = []

        # Basic arguments
        args.append(f"path={self.path}")

        if self.line is not None:
            args.append(f"line='{self.line}'")

        args.append(f"state={self.state}")

        if self.regexp is not None:
            args.append(f"regexp='{self.regexp}'")

        if self.search_string is not None:
            args.append(f"search_string='{self.search_string}'")

        if self.insertafter is not None:
            args.append(f"insertafter='{self.insertafter}'")

        if self.insertbefore is not None:
            args.append(f"insertbefore='{self.insertbefore}'")

        if self.create:
            args.append("create=yes")

        if self.backup:
            args.append("backup=yes")

        if self.firstmatch:
            args.append("firstmatch=yes")

        if self.backrefs:
            args.append("backrefs=yes")

        if self.owner is not None:
            args.append(f"owner={self.owner}")

        if self.group is not None:
            args.append(f"group={self.group}")

        if self.mode is not None:
            args.append(f"mode={self.mode}")

        if self.attributes is not None:
            args.append(f"attributes={self.attributes}")

        if self.seuser is not None:
            args.append(f"seuser={self.seuser}")

        if self.serole is not None:
            args.append(f"serole={self.serole}")

        if self.setype is not None:
            args.append(f"setype={self.setype}")

        if self.selevel is not None:
            args.append(f"selevel={self.selevel}")

        if self.unsafe_writes:
            args.append("unsafe_writes=yes")

        if self.validate is not None:
            args.append(f"validate='{self.validate}'")

        # Construct the command
        cmd_args = " ".join(args)
        cmd = f"ansible.builtin.lineinfile {cmd_args}"

        # Execute the command
        r = yield Command(cmd, guard=self.guard, sudo=self.sudo, su=self.su)
        r.changed = True