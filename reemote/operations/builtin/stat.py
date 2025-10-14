# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command

class Stat:
    """
    A class to encapsulate the functionality of retrieving file or file system status,
    similar to the Linux/Unix stat command. This class allows users to specify a file path
    and various options to control what information is retrieved about the file.

    Attributes:
        path (str): The full path of the file/object to get the facts of.
        checksum_algorithm (str): Algorithm to determine checksum of file. Choices: "md5", "sha1", "sha224", "sha256", "sha384", "sha512".
        follow (bool): Whether to follow symlinks.
        get_attributes (bool): Get file attributes using lsattr tool if present.
        get_checksum (bool): Whether to return a checksum of the file.
        get_mime (bool): Use file magic and return data about the nature of the file.
        guard (bool): If `False` the commands will not be executed.

    **Examples:**

    .. code:: python

        # Obtain the stats of /etc/foo.conf
        r = yield Stat(path="/etc/foo.conf")
        # Check if file exists and belongs to 'root'
        if r.cp.stat.exists and r.cp.stat.pw_name != 'root':
            print("File ownership has changed")

        # Check if a path is a symlink
        r = yield Stat(path="/path/to/something")
        if r.cp.stat.islnk is not defined:
            print("Path doesn't exist")
        elif r.cp.stat.islnk:
            print("Path exists and is a symlink")
        else:
            print("Path exists and isn't a symlink")

        # Check if a path is a directory
        r = yield Stat(path="/path/to/something")
        if r.cp.stat.isdir is defined and r.cp.stat.isdir:
            print("Path exists and is a directory")

        # Do not calculate the checksum
        r = yield Stat(path="/path/to/myhugefile", get_checksum=False)

        # Use sha256 to calculate the checksum
        r = yield Stat(path="/path/to/something", checksum_algorithm="sha256")

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - For Windows targets, use the ansible.windows.win_stat module instead.
        - Commands are constructed based on the provided parameters.
    """
    
    def __init__(self,
                 path: str,
                 checksum_algorithm: str = "sha1",
                 follow: bool = False,
                 get_attributes: bool = True,
                 get_checksum: bool = True,
                 get_mime: bool = True,
                 guard: bool = True):
        
        self.path = path
        self.checksum_algorithm = checksum_algorithm
        self.follow = follow
        self.get_attributes = get_attributes
        self.get_checksum = get_checksum
        self.get_mime = get_mime
        self.guard = guard

    def __repr__(self):
        return (f"Stat(path={self.path!r}, "
                f"checksum_algorithm={self.checksum_algorithm!r}, "
                f"follow={self.follow!r}, "
                f"get_attributes={self.get_attributes!r}, "
                f"get_checksum={self.get_checksum!r}, "
                f"get_mime={self.get_mime!r}, "
                f"guard={self.guard!r})")

    def execute(self):
        # Build the command arguments
        args = []
        
        # Add path argument
        args.append(f"path={self.path}")
        
        # Add optional arguments
        if self.checksum_algorithm != "sha1":  # sha1 is default
            args.append(f"checksum_algorithm={self.checksum_algorithm}")
            
        if self.follow:
            args.append("follow=yes")
            
        if not self.get_attributes:  # default is True
            args.append("get_attributes=no")
            
        if not self.get_checksum:  # default is True
            args.append("get_checksum=no")
            
        if not self.get_mime:  # default is True
            args.append("get_mime=no")
        
        # Construct the command
        cmd_args = ",".join(args)
        cmd = f"stat {cmd_args}"
        
        # Execute the command
        r = yield Command(cmd, guard=self.guard)
        r.changed = False  # stat commands don't change system state