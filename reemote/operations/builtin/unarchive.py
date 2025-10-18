# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class Unarchive:
    """
    A class to encapsulate the functionality of unpacking archives in Unix-like operating systems.
    It allows users to specify an archive file that is unpacked on all hosts, with various options
    for controlling the extraction process.

    Attributes:
        src (str): Path to the archive file to unpack.
        dest (str): Remote absolute path where the archive should be unpacked.
        copy (bool): If true, the file is copied from local controller to the managed node.
        remote_src (bool): Set to true to indicate the archived file is already on the remote system.
        creates (str): If the specified path already exists, this step will not be run.
        exclude (list): List of directory and file entries to exclude from extraction.
        extra_opts (list): Additional options to pass to the extraction command.
        include (list): List of directory and file entries to extract (only these will be extracted).
        keep_newer (bool): Do not replace existing files that are newer than files from the archive.
        list_files (bool): If set to True, return the list of files contained in the archive.
        owner (str): Name of the user that should own the extracted files.
        group (str): Name of the group that should own the extracted files.
        mode (str): Permissions for the extracted files.
        attributes (str): Filesystem attributes for the extracted files.
        seuser (str): SELinux user context.
        serole (str): SELinux role context.
        setype (str): SELinux type context.
        selevel (str): SELinux level context.
        unsafe_writes (bool): Allow unsafe writes if atomic operations fail.
        decrypt (bool): Control auto-decryption of source files using vault.
        validate_certs (bool): Validate SSL certificates when downloading from HTTPS URLs.
        io_buffer_size (int): Size of memory buffer for extracting files.

    **Examples:**

    .. code:: python

        # Extract a local archive file to a remote destination
        r = yield Unarchive(src="foo.tgz", dest="/var/lib/foo")
        print(r.dest)

        # Unarchive a file that is already on the remote machine
        r = yield Unarchive(src="/tmp/foo.zip", dest="/usr/local/bin", remote_src=True)

        # Unarchive with extra options
        r = yield Unarchive(
            src="/tmp/foo.zip",
            dest="/usr/local/bin",
            extra_opts=["--transform", "s/^xxx/yyy/"]
        )

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Requires zipinfo and gtar/unzip command on target host.
        - Can handle .zip, .tar, .tar.gz, .tar.bz2, .tar.xz, and .tar.zst files.
        - Does not handle compressed files that don't contain archives (.gz, .bz2, .xz, .zst without tar).
    """

    def __init__(self,
                 src: str,
                 dest: str,
                 copy: bool = True,
                 remote_src: bool = False,
                 creates: str = None,
                 exclude: list = None,
                 extra_opts: list = None,
                 include: list = None,
                 keep_newer: bool = False,
                 list_files: bool = False,
                 owner: str = None,
                 group: str = None,
                 mode: str = None,
                 attributes: str = None,
                 seuser: str = None,
                 serole: str = None,
                 setype: str = None,
                 selevel: str = None,
                 unsafe_writes: bool = False,
                 decrypt: bool = True,
                 validate_certs: bool = True,
                 io_buffer_size: int = 65536):

        self.src = src
        self.dest = dest
        self.copy = copy
        self.remote_src = remote_src
        self.creates = creates
        self.exclude = exclude or []
        self.extra_opts = extra_opts or []
        self.include = include or []
        self.keep_newer = keep_newer
        self.list_files = list_files
        self.owner = owner
        self.group = group
        self.mode = mode
        self.attributes = attributes
        self.seuser = seuser
        self.serole = serole
        self.setype = setype
        self.selevel = selevel
        self.unsafe_writes = unsafe_writes
        self.decrypt = decrypt
        self.validate_certs = validate_certs
        self.io_buffer_size = io_buffer_size

        # Validate mutually exclusive parameters
        if self.copy and self.remote_src:
            raise ValueError("copy and remote_src are mutually exclusive")
        if self.exclude and self.include:
            raise ValueError("exclude and include are mutually exclusive")

    def __repr__(self):
        return (f"Unarchive(src={self.src!r}, "
                f"dest={self.dest!r}, "
                f"copy={self.copy!r}, "
                f"remote_src={self.remote_src!r}, "
                f"creates={self.creates!r}, "
                f"exclude={self.exclude!r}, "
                f"extra_opts={self.extra_opts!r}, "
                f"include={self.include!r}, "
                f"keep_newer={self.keep_newer!r}, "
                f"list_files={self.list_files!r}, "
                f"owner={self.owner!r}, "
                f"group={self.group!r}, "
                f"mode={self.mode!r}, "
                f"attributes={self.attributes!r}, "
                f"seuser={self.seuser!r}, "
                f"serole={self.serole!r}, "
                f"setype={self.setype!r}, "
                f"selevel={self.selevel!r}, "
                f"unsafe_writes={self.unsafe_writes!r}, "
                f"decrypt={self.decrypt!r}, "
                f"validate_certs={self.validate_certs!r}, "
                f"io_buffer_size={self.io_buffer_size!r})")

    def execute(self):
        # Build the unarchive command
        # Note: This is a simplified implementation that would need to be expanded
        # to handle all the Ansible unarchive module features
        cmd_parts = ["unarchive"]

        # Add basic parameters
        cmd_parts.extend([f"src={self.src}", f"dest={self.dest}"])

        if not self.copy:
            cmd_parts.append("copy=false")
        if self.remote_src:
            cmd_parts.append("remote_src=true")
        if self.creates:
            cmd_parts.append(f"creates={self.creates}")
        if self.exclude:
            cmd_parts.append(f"exclude={','.join(self.exclude)}")
        if self.extra_opts:
            cmd_parts.append(f"extra_opts={','.join(self.extra_opts)}")
        if self.include:
            cmd_parts.append(f"include={','.join(self.include)}")
        if self.keep_newer:
            cmd_parts.append("keep_newer=true")
        if self.list_files:
            cmd_parts.append("list_files=true")
        if self.owner:
            cmd_parts.append(f"owner={self.owner}")
        if self.group:
            cmd_parts.append(f"group={self.group}")
        if self.mode:
            cmd_parts.append(f"mode={self.mode}")
        if self.attributes:
            cmd_parts.append(f"attributes={self.attributes}")
        if self.seuser:
            cmd_parts.append(f"seuser={self.seuser}")
        if self.serole:
            cmd_parts.append(f"serole={self.serole}")
        if self.setype:
            cmd_parts.append(f"setype={self.setype}")
        if self.selevel:
            cmd_parts.append(f"selevel={self.selevel}")
        if self.unsafe_writes:
            cmd_parts.append("unsafe_writes=true")
        if not self.decrypt:
            cmd_parts.append("decrypt=false")
        if not self.validate_certs:
            cmd_parts.append("validate_certs=false")
        if self.io_buffer_size != 65536:
            cmd_parts.append(f"io_buffer_size={self.io_buffer_size}")

        cmd = " ".join(cmd_parts)
        r = yield Command(cmd)
        r.changed = True