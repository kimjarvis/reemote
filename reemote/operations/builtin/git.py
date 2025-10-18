# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class Git:
    """
    A class to encapsulate the functionality of git operations in Unix-like operating systems.
    It allows users to manage git checkouts of repositories to deploy files or software.

    This class provides a Python interface to common git operations such as cloning,
    checking out specific versions, handling submodules, and creating archives.

    Attributes:
        repo (str): Git, SSH, or HTTP(S) protocol address of the git repository.
        dest (str): The path of where the repository should be checked out.
        version (str): What version of the repository to check out.
        accept_hostkey (bool): Will ensure -o StrictHostKeyChecking=no is present as an ssh option.
        accept_newhostkey (bool): Use -o StrictHostKeyChecking=accept-new as an ssh option.
        archive (str): Specify archive file path with extension.
        archive_prefix (str): Specify a prefix to add to each file path in archive.
        bare (bool): If true, repository will be created as a bare repo.
        clone (bool): If false, do not clone the repository even if it does not exist locally.
        depth (int): Create a shallow clone with a history truncated to the specified number.
        executable (str): Path to git executable to use.
        force (bool): If true, any modified files in the working repository will be discarded.
        gpg_allowlist (list): List of trusted GPG fingerprints.
        key_file (str): Specify an optional private key file path.
        recursive (bool): If false, repository will be cloned without the --recursive option.
        reference (str): Reference repository.
        refspec (str): Add an additional refspec to be fetched.
        remote (str): Name of the remote.
        separate_git_dir (str): The path to place the cloned repository.
        single_branch (bool): Clone only the history leading to the tip of the specified revision.
        ssh_opts (str): Options git will pass to ssh when used as protocol.
        track_submodules (bool): If true, submodules will track the latest commit.
        umask (str): The umask to set before doing any checkouts.
        update (bool): If false, do not retrieve new revisions from the origin repository.
        verify_commit (bool): If true, verify the signature of a GPG signed commit.
        guard (bool): If `False` the commands will not be executed.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        # Git checkout
        r = yield Git(
            repo='https://github.com/ansible/ansible.git',
            dest='/tmp/checkout',
            version='release-0.22'
        )
        print(r.cp.stdout)

        # Read-write git checkout from github
        r = yield Git(
            repo='[email protected]:ansible/ansible.git',
            dest='/tmp/checkout'
        )

        # Just ensuring the repo checkout exists
        r = yield Git(
            repo='https://github.com/ansible/ansible.git',
            dest='/tmp/checkout',
            update=False
        )

        # Create git archive from repo
        r = yield Git(
            repo='[email protected]:ansible/ansible.git',
            dest='/tmp/checkout',
            archive='/tmp/ansible.zip'
        )

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the various git options and flags.
        - If the task seems to be hanging, first verify remote host is in known_hosts.
    """

    def __init__(self,
                 repo: str,
                 dest: str,
                 version: str = "HEAD",
                 accept_hostkey: bool = False,
                 accept_newhostkey: bool = False,
                 archive: str = None,
                 archive_prefix: str = None,
                 bare: bool = False,
                 clone: bool = True,
                 depth: int = None,
                 executable: str = None,
                 force: bool = False,
                 gpg_allowlist: list = None,
                 key_file: str = None,
                 recursive: bool = True,
                 reference: str = None,
                 refspec: str = None,
                 remote: str = "origin",
                 separate_git_dir: str = None,
                 single_branch: bool = False,
                 ssh_opts: str = None,
                 track_submodules: bool = False,
                 umask: str = None,
                 update: bool = True,
                 verify_commit: bool = False,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):

        self.repo = repo
        self.dest = dest
        self.version = version
        self.accept_hostkey = accept_hostkey
        self.accept_newhostkey = accept_newhostkey
        self.archive = archive
        self.archive_prefix = archive_prefix
        self.bare = bare
        self.clone = clone
        self.depth = depth
        self.executable = executable
        self.force = force
        self.gpg_allowlist = gpg_allowlist or []
        self.key_file = key_file
        self.recursive = recursive
        self.reference = reference
        self.refspec = refspec
        self.remote = remote
        self.separate_git_dir = separate_git_dir
        self.single_branch = single_branch
        self.ssh_opts = ssh_opts
        self.track_submodules = track_submodules
        self.umask = umask
        self.update = update
        self.verify_commit = verify_commit
        self.guard = guard
        self.sudo = sudo
        self.su = su

    def __repr__(self):
        return (f"Git(repo={self.repo!r}, "
                f"dest={self.dest!r}, "
                f"version={self.version!r}, "
                f"accept_hostkey={self.accept_hostkey!r}, "
                f"accept_newhostkey={self.accept_newhostkey!r}, "
                f"archive={self.archive!r}, "
                f"archive_prefix={self.archive_prefix!r}, "
                f"bare={self.bare!r}, "
                f"clone={self.clone!r}, "
                f"depth={self.depth!r}, "
                f"executable={self.executable!r}, "
                f"force={self.force!r}, "
                f"gpg_allowlist={self.gpg_allowlist!r}, "
                f"key_file={self.key_file!r}, "
                f"recursive={self.recursive!r}, "
                f"reference={self.reference!r}, "
                f"refspec={self.refspec!r}, "
                f"remote={self.remote!r}, "
                f"separate_git_dir={self.separate_git_dir!r}, "
                f"single_branch={self.single_branch!r}, "
                f"ssh_opts={self.ssh_opts!r}, "
                f"track_submodules={self.track_submodules!r}, "
                f"umask={self.umask!r}, "
                f"update={self.update!r}, "
                f"verify_commit={self.verify_commit!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r}, "
                f"su={self.su!r})")

    def execute(self):
        # Build the git command
        cmd_parts = ["git"]

        if self.executable:
            cmd_parts[0] = self.executable

        # Add global options
        if self.umask:
            cmd_parts.extend(["-c", f"umask={self.umask}"])

        cmd_parts.append("clone" if self.clone else "fetch")

        # Add clone-specific options
        if self.clone:
            if self.bare:
                cmd_parts.append("--bare")
            if self.depth and self.depth >= 1:
                cmd_parts.extend(["--depth", str(self.depth)])
            if self.recursive:
                cmd_parts.append("--recursive")
            if self.single_branch:
                cmd_parts.append("--single-branch")
            if self.reference:
                cmd_parts.extend(["--reference", self.reference])
            if self.separate_git_dir:
                cmd_parts.extend(["--separate-git-dir", self.separate_git_dir])
            if self.refspec:
                cmd_parts.extend(["--refspec", self.refspec])

        # Add repository and destination
        cmd_parts.extend([self.repo, self.dest])

        # Build the full command string
        cmd = " ".join(cmd_parts)

        # print(f"{self}")
        r = yield Command(cmd, guard=self.guard, sudo=self.sudo, su=self.su)
        r.changed = True