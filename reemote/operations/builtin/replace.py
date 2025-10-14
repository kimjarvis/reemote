# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class Replace:
    """
    A class to encapsulate the functionality of the ansible.builtin.replace module.
    This module replaces all instances of a pattern within a file using regular expressions.

    Attributes:
        path (str): The file to modify.
        regexp (str): The regular expression to look for in the contents of the file.
        replace (str): The string to replace regexp matches. Default: ""
        after (str): If specified, only content after this match will be replaced/removed.
        before (str): If specified, only content before this match will be replaced/removed.
        backup (bool): Create a backup file including the timestamp information.
        encoding (str): The character encoding for reading and writing the file. Default: "utf-8"
        group (str): Name of the group that should own the filesystem object.
        mode (str): The permissions the resulting filesystem object should have.
        owner (str): Name of the user that should own the filesystem object.
        selevel (str): The level part of the SELinux filesystem object context.
        serole (str): The role part of the SELinux filesystem object context.
        setype (str): The type part of the SELinux filesystem object context.
        seuser (str): The user part of the SELinux filesystem object context.
        unsafe_writes (bool): Influence when to use atomic operation.
        validate (str): The validation command to run before copying the updated file.
        attributes (str): The attributes the resulting filesystem object should have.
        guard (bool): If `False` the commands will not be executed.

    **Examples:**

    .. code:: python

        # Replace old hostname with new hostname
        r = yield Replace(
            path="/etc/hosts",
            regexp=r'(\\s+)old\\.host\\.name(\\s+.*)?$',
            replace='\\1new.host.name\\2'
        )

        # Replace between expressions
        r = yield Replace(
            path="/etc/hosts",
            after=r'(?m)^<VirtualHost [*]>',
            before='</VirtualHost>',
            regexp=r'^(.+)$',
            replace='# \\1'
        )

        # Replace with file attributes
        r = yield Replace(
            path="/home/jdoe/.ssh/known_hosts",
            regexp=r'^old\\.host\\.name[^\\n]*\\n',
            owner="jdoe",
            group="jdoe",
            mode='0644'
        )

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - As of Ansible 2.3, the dest option has been changed to path as default.
        - As of Ansible 2.7.10, the combined use of before and after works properly.
        - Uses Python regular expressions with MULTILINE mode enabled.
    """

    def __init__(self,
                 path: str,
                 regexp: str,
                 replace: str = "",
                 after: str = None,
                 before: str = None,
                 backup: bool = False,
                 encoding: str = "utf-8",
                 group: str = None,
                 mode: str = None,
                 owner: str = None,
                 selevel: str = None,
                 serole: str = None,
                 setype: str = None,
                 seuser: str = None,
                 unsafe_writes: bool = False,
                 validate: str = None,
                 attributes: str = None,
                 guard: bool = True):

        self.path = path
        self.regexp = regexp
        self.replace = replace
        self.after = after
        self.before = before
        self.backup = backup
        self.encoding = encoding
        self.group = group
        self.mode = mode
        self.owner = owner
        self.selevel = selevel
        self.serole = serole
        self.setype = setype
        self.seuser = seuser
        self.unsafe_writes = unsafe_writes
        self.validate = validate
        self.attributes = attributes
        self.guard = guard

    def __repr__(self):
        return (f"Replace(path={self.path!r}, "
                f"regexp={self.regexp!r}, "
                f"replace={self.replace!r}, "
                f"after={self.after!r}, "
                f"before={self.before!r}, "
                f"backup={self.backup!r}, "
                f"encoding={self.encoding!r}, "
                f"group={self.group!r}, "
                f"mode={self.mode!r}, "
                f"owner={self.owner!r}, "
                f"selevel={self.selevel!r}, "
                f"serole={self.serole!r}, "
                f"setype={self.setype!r}, "
                f"seuser={self.seuser!r}, "
                f"unsafe_writes={self.unsafe_writes!r}, "
                f"validate={self.validate!r}, "
                f"attributes={self.attributes!r}, "
                f"guard={self.guard!r})")

    def execute(self):
        # Construct the command arguments
        args = {
            'path': self.path,
            'regexp': self.regexp,
            'replace': self.replace,
            'guard': self.guard
        }

        # Add optional arguments if they are specified
        if self.after is not None:
            args['after'] = self.after
        if self.before is not None:
            args['before'] = self.before
        if self.backup:
            args['backup'] = self.backup
        if self.encoding != "utf-8":
            args['encoding'] = self.encoding
        if self.group is not None:
            args['group'] = self.group
        if self.mode is not None:
            args['mode'] = self.mode
        if self.owner is not None:
            args['owner'] = self.owner
        if self.selevel is not None:
            args['selevel'] = self.selevel
        if self.serole is not None:
            args['serole'] = self.serole
        if self.setype is not None:
            args['setype'] = self.setype
        if self.seuser is not None:
            args['seuser'] = self.seuser
        if self.unsafe_writes:
            args['unsafe_writes'] = self.unsafe_writes
        if self.validate is not None:
            args['validate'] = self.validate
        if self.attributes is not None:
            args['attributes'] = self.attributes

        # For simplicity, we'll construct this as a command that would be executed
        # In a real implementation, this would interface with Ansible's replace module
        cmd_parts = ["ansible.builtin.replace"]
        for key, value in args.items():
            if isinstance(value, bool):
                if value:
                    cmd_parts.append(f"{key}={str(value).lower()}")
            else:
                cmd_parts.append(f"{key}={value!r}")

        cmd_string = " ".join(cmd_parts)
        r = yield Command(cmd_string, guard=self.guard)
        r.changed = True