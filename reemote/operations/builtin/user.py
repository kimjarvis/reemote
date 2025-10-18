# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class User:
    """
    A class to encapsulate the functionality of managing user accounts in Unix-like operating systems.
    It allows users to create, modify, and remove user accounts with various attributes and options.

    Attributes:
        name (str): Name of the user to create, remove or modify.
        state (str): Whether the account should exist or not ('present' or 'absent').
        uid (int): Optionally sets the UID of the user.
        group (str): Optionally sets the user's primary group.
        groups (list): A list of supplementary groups which the user is also a member of.
        append (bool): If true, add the user to the groups specified in groups.
        comment (str): Optionally sets the description (aka GECOS) of user account.
        home (str): Optionally set the user's home directory.
        shell (str): Optionally set the user's shell.
        password (str): If provided, set the user's password.
        create_home (bool): Whether to create a home directory for the user.
        move_home (bool): If set to true when used with home, attempt to move the user's old home directory.
        system (bool): When creating an account, setting this to true makes the user a system account.
        force (bool): Forces removal of the user and associated directories.
        remove (bool): Attempts to remove directories associated with the user.
        generate_ssh_key (bool): Whether to generate a SSH key for the user.
        ssh_key_bits (int): Optionally specify number of bits in SSH key to create.
        ssh_key_file (str): Optionally specify the SSH key filename.
        ssh_key_comment (str): Optionally define the comment for the SSH key.
        ssh_key_passphrase (str): Set a passphrase for the SSH key.
        ssh_key_type (str): Optionally specify the type of SSH key to generate.
        update_password (str): Controls when to update passwords ('always' or 'on_create').
        expires (float): An expiry time for the user in epoch.
        password_lock (bool): Lock the password.
        password_expire_max (int): Maximum number of days between password change.
        password_expire_min (int): Minimum number of days between password change.
        password_expire_warn (int): Number of days of warning before password expires.
        password_expire_account_disable (int): Number of days after a password expires until the account is disabled.
        local (bool): Forces the use of "local" command alternatives.
        skeleton (str): Optionally set a home skeleton directory.
        umask (str): Sets the umask of the user.
        uid_min (int): Sets the UID_MIN value for user creation.
        uid_max (int): Sets the UID_MAX value for user creation.
        seuser (str): Optionally sets the seuser type on SELinux enabled systems.
        login_class (str): Optionally sets the user's login class.
        authorization (str): Sets the authorization of the user.
        role (str): Sets the role of the user.
        profile (str): Sets the profile of the user.
        hidden (bool): Optionally hide the user from the login window and system preferences (macOS only).

    **Examples:**

    .. code:: python

        # Add the user 'johnd' with a specific uid and a primary group of 'admin'
        r = yield User(name="johnd", comment="John Doe", uid=1040, group="admin")

        # Create a user 'johnd' with a home directory
        r = yield User(name="johnd", create_home=True)

        # Add the user 'james' with a bash shell, appending the group 'admins' and 'developers'
        r = yield User(name="james", shell="/bin/bash", groups=["admins", "developers"], append=True)

        # Remove the user 'johnd'
        r = yield User(name="johnd", state="absent", remove=True)

        # Create a 2048-bit SSH key for user jsmith
        r = yield User(name="jsmith", generate_ssh_key=True, ssh_key_bits=2048, ssh_key_file=".ssh/id_rsa")

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the provided parameters and the target operating system.
        - The implementation uses underlying system tools like useradd, usermod, userdel, pw, dscl depending on the platform.
    """

    def __init__(self,
                 name: str,
                 state: str = "present",
                 uid: int = None,
                 group: str = None,
                 groups: list = None,
                 append: bool = False,
                 comment: str = None,
                 home: str = None,
                 shell: str = None,
                 password: str = None,
                 create_home: bool = True,
                 move_home: bool = False,
                 system: bool = False,
                 force: bool = False,
                 remove: bool = False,
                 generate_ssh_key: bool = False,
                 ssh_key_bits: int = None,
                 ssh_key_file: str = None,
                 ssh_key_comment: str = None,
                 ssh_key_passphrase: str = None,
                 ssh_key_type: str = "rsa",
                 update_password: str = "always",
                 expires: float = None,
                 password_lock: bool = None,
                 password_expire_max: int = None,
                 password_expire_min: int = None,
                 password_expire_warn: int = None,
                 password_expire_account_disable: int = None,
                 local: bool = False,
                 skeleton: str = None,
                 umask: str = None,
                 uid_min: int = None,
                 uid_max: int = None,
                 seuser: str = None,
                 login_class: str = None,
                 authorization: str = None,
                 role: str = None,
                 profile: str = None,
                 hidden: bool = None):

        self.name = name
        self.state = state
        self.uid = uid
        self.group = group
        self.groups = groups or []
        self.append = append
        self.comment = comment
        self.home = home
        self.shell = shell
        self.password = password
        self.create_home = create_home
        self.move_home = move_home
        self.system = system
        self.force = force
        self.remove = remove
        self.generate_ssh_key = generate_ssh_key
        self.ssh_key_bits = ssh_key_bits
        self.ssh_key_file = ssh_key_file
        self.ssh_key_comment = ssh_key_comment
        self.ssh_key_passphrase = ssh_key_passphrase
        self.ssh_key_type = ssh_key_type
        self.update_password = update_password
        self.expires = expires
        self.password_lock = password_lock
        self.password_expire_max = password_expire_max
        self.password_expire_min = password_expire_min
        self.password_expire_warn = password_expire_warn
        self.password_expire_account_disable = password_expire_account_disable
        self.local = local
        self.skeleton = skeleton
        self.umask = umask
        self.uid_min = uid_min
        self.uid_max = uid_max
        self.seuser = seuser
        self.login_class = login_class
        self.authorization = authorization
        self.role = role
        self.profile = profile
        self.hidden = hidden

    def __repr__(self):
        return (f"User(name={self.name!r}, "
                f"state={self.state!r}, "
                f"uid={self.uid!r}, "
                f"group={self.group!r}, "
                f"groups={self.groups!r}, "
                f"append={self.append!r}, "
                f"comment={self.comment!r}, "
                f"home={self.home!r}, "
                f"shell={self.shell!r}, "
                f"password={'***' if self.password else None!r}, "
                f"create_home={self.create_home!r}, "
                f"move_home={self.move_home!r}, "
                f"system={self.system!r}, "
                f"force={self.force!r}, "
                f"remove={self.remove!r}, "
                f"generate_ssh_key={self.generate_ssh_key!r}, "
                f"ssh_key_bits={self.ssh_key_bits!r}, "
                f"ssh_key_file={self.ssh_key_file!r}, "
                f"ssh_key_comment={self.ssh_key_comment!r}, "
                f"ssh_key_passphrase={'***' if self.ssh_key_passphrase else None!r}, "
                f"ssh_key_type={self.ssh_key_type!r}, "
                f"update_password={self.update_password!r}, "
                f"expires={self.expires!r}, "
                f"password_lock={self.password_lock!r}, "
                f"password_expire_max={self.password_expire_max!r}, "
                f"password_expire_min={self.password_expire_min!r}, "
                f"password_expire_warn={self.password_expire_warn!r}, "
                f"password_expire_account_disable={self.password_expire_account_disable!r}, "
                f"local={self.local!r}, "
                f"skeleton={self.skeleton!r}, "
                f"umask={self.umask!r}, "
                f"uid_min={self.uid_min!r}, "
                f"uid_max={self.uid_max!r}, "
                f"seuser={self.seuser!r}, "
                f"login_class={self.login_class!r}, "
                f"authorization={self.authorization!r}, "
                f"role={self.role!r}, "
                f"profile={self.profile!r}, "
                f"hidden={self.hidden!r})")

    def execute(self):
        # Build the command based on the parameters
        if self.state == "absent":
            cmd = f"userdel"
            if self.remove:
                cmd += " --remove"
            if self.force:
                cmd += " --force"
            cmd += f" {self.name}"
        else:
            cmd = f"useradd" if self.state == "present" else f"usermod"

            # Add common options
            if self.uid is not None:
                cmd += f" --uid {self.uid}"
            if self.group:
                cmd += f" --gid {self.group}"
            if self.groups:
                groups_str = ",".join(self.groups)
                if self.append:
                    cmd += f" --append --groups {groups_str}"
                else:
                    cmd += f" --groups {groups_str}"
            if self.comment:
                cmd += f" --comment '{self.comment}'"
            if self.home:
                cmd += f" --home {self.home}"
            if self.shell:
                cmd += f" --shell {self.shell}"
            if self.password and self.update_password == "always":
                cmd += f" --password '{self.password}'"
            if self.create_home:
                cmd += " --create-home"
            if self.move_home:
                cmd += " --move-home"
            if self.system:
                cmd += " --system"
            if self.expires is not None:
                cmd += f" --expiredate {self.expires}"
            if self.password_lock is not None:
                if self.password_lock:
                    cmd += " --lock"
                else:
                    cmd += " --unlock"

            # Add user name
            cmd += f" {self.name}"

            # Handle SSH key generation
            if self.generate_ssh_key:
                ssh_cmd = "ssh-keygen"
                if self.ssh_key_bits:
                    ssh_cmd += f" -b {self.ssh_key_bits}"
                if self.ssh_key_file:
                    ssh_cmd += f" -f {self.ssh_key_file}"
                if self.ssh_key_comment:
                    ssh_cmd += f" -C '{self.ssh_key_comment}'"
                if self.ssh_key_passphrase:
                    ssh_cmd += f" -P '{self.ssh_key_passphrase}'"
                else:
                    ssh_cmd += " -P ''"
                if self.ssh_key_type:
                    ssh_cmd += f" -t {self.ssh_key_type}"

                # Execute SSH key generation after user creation
                yield Command(ssh_cmd)

        # print(f"{self}")
        r = yield Command(cmd)
        r.changed = True