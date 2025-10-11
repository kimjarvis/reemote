from reemote.command import Command


class Apt:
    """
    A class to encapsulate the functionality of apt package management in Debian/Ubuntu systems.
    It generates appropriate apt commands that can be passed to the Shell class for execution.

    Attributes:
        name (list): A list of package names, like ['foo'], or package specifier with version, like ['foo=1.0'].
        state (str): Indicates the desired package state. Choices: "absent", "build-dep", "latest", "present", "fixed".
        allow_change_held_packages (bool): Allows changing the version of a package which is on the apt hold list.
        allow_downgrade (bool): Corresponds to the --allow-downgrades option for apt.
        allow_unauthenticated (bool): Ignore if packages cannot be authenticated.
        auto_install_module_deps (bool): Automatically install dependencies required to run this module.
        autoclean (bool): If true, cleans the local repository of retrieved package files.
        autoremove (bool): If true, remove unused dependency packages.
        cache_valid_time (int): Update the apt cache if it is older than this time (in seconds).
        clean (bool): Run the equivalent of apt-get clean to clear out the local repository.
        deb (str): Path to a .deb package on the remote machine.
        default_release (str): Corresponds to the -t option for apt and sets pin priorities.
        dpkg_options (str): Add dpkg options to apt command.
        fail_on_autoremove (bool): Corresponds to the --no-remove option for apt.
        force (bool): Corresponds to the --force-yes to apt-get (destructive operation).
        force_apt_get (bool): Force usage of apt-get instead of aptitude.
        install_recommends (bool): Whether to install recommended packages.
        lock_timeout (int): How many seconds to wait to acquire a lock on the apt db.
        only_upgrade (bool): Only upgrade a package if it is already installed.
        policy_rc_d (int): Force the exit code of /usr/sbin/policy-rc.d.
        purge (bool): Will force purging of configuration files if state=absent or autoremove=yes.
        update_cache (bool): Run the equivalent of apt-get update before the operation.
        update_cache_retries (int): Amount of retries if the cache update fails.
        update_cache_retry_max_delay (int): Max delay for exponential backoff during cache update retries.
        upgrade (str): Type of upgrade to perform. Choices: "dist", "full", "no", "safe", "yes".
        guard (bool): If `False` the commands will not be executed.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        # Install apache httpd (state=present is optional)
        yield Apt(
            name="apache2",
            state="present",
        )

        # Update repositories cache and install "foo" package
        yield Apt(
            name="foo",
            update_cache=True,
        )

        # Remove "foo" package
        yield Apt(
            name="foo",
            state="absent",
        )

        # Install the package "foo"
        yield Apt(
            name="foo",
        )

        # Install a list of packages
        yield Apt(
            pkg=["foo", "foo-tools"],
        )

        # Install the version '1.00' of package "foo"
        yield Apt(
            name="foo=1.00",
        )

        # Update the repository cache and update package "nginx" to latest version using default release squeeze-backport
        yield Apt(
            name="nginx",
            state="latest",
            default_release="squeeze-backports",
            update_cache=True,
        )

        # Install the version '1.18.0' of package "nginx" and allow potential downgrades
        yield Apt(
            name="nginx=1.18.0",
            state="present",
            allow_downgrade=True,
        )

        # Install zfsutils-linux with ensuring conflicted packages (e.g. zfs-fuse) will not be removed.
        yield Apt(
            name="zfsutils-linux",
            state="latest",
            fail_on_autoremove=True,
        )

        # Install latest version of "openjdk-6-jdk" ignoring "install-recommends"
        yield Apt(
            name="openjdk-6-jdk",
            state="latest",
            install_recommends=False,
        )

        # Update all packages to their latest version
        yield Apt(
            name="*",
            state="latest",
        )

        # Upgrade the OS (apt-get dist-upgrade)
        yield Apt(
            upgrade="dist",
        )

        # Run the equivalent of "apt-get update" as a separate step
        yield Apt(
            update_cache=True,
        )

        # Only run "update_cache=yes" if the last one is more than 3600 seconds ago
        yield Apt(
            update_cache=True,
            cache_valid_time=3600,
        )

        # Pass options to dpkg on run
        yield Apt(
            upgrade="dist",
            update_cache=True,
            dpkg_options="force-confold,force-confdef",
        )

        # Install a .deb package
        yield Apt(
            deb="/tmp/mypackage.deb",
        )

        # Install the build dependencies for package "foo"
        yield Apt(
            pkg="foo",
            state="build-dep",
        )

        # Install a .deb package from the internet
        yield Apt(
            deb="https://example.com/python-ppq_0.1-1_all.deb",
        )

        # Remove useless packages from the cache
        yield Apt(
            autoclean=True,
        )

        # Remove dependencies that are no longer required
        yield Apt(
            autoremove=True,
        )

        # Remove dependencies that are no longer required and purge their configuration files
        yield Apt(
            autoremove=True,
            purge=True,
        )

        # Run the equivalent of "apt-get clean" as a separate step
        yield Apt(
            clean=True,
        )

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the various apt options and flags.
        - Most operations require elevated privileges (sudo/su).

    """

    def __init__(self,
                 name: list = None,
                 state: str = "present",
                 allow_change_held_packages: bool = False,
                 allow_downgrade: bool = False,
                 allow_unauthenticated: bool = False,
                 auto_install_module_deps: bool = True,
                 autoclean: bool = False,
                 autoremove: bool = False,
                 cache_valid_time: int = 0,
                 clean: bool = False,
                 deb: str = None,
                 default_release: str = None,
                 dpkg_options: str = "force-confdef,force-confold",
                 fail_on_autoremove: bool = False,
                 force: bool = False,
                 force_apt_get: bool = False,
                 install_recommends: bool = None,
                 lock_timeout: int = 60,
                 only_upgrade: bool = False,
                 policy_rc_d: int = None,
                 purge: bool = False,
                 update_cache: bool = False,
                 update_cache_retries: int = 5,
                 update_cache_retry_max_delay: int = 12,
                 upgrade: str = "no",
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):

        self.name = name
        self.state = state
        self.allow_change_held_packages = allow_change_held_packages
        self.allow_downgrade = allow_downgrade
        self.allow_unauthenticated = allow_unauthenticated
        self.auto_install_module_deps = auto_install_module_deps
        self.autoclean = autoclean
        self.autoremove = autoremove
        self.cache_valid_time = cache_valid_time
        self.clean = clean
        self.deb = deb
        self.default_release = default_release
        self.dpkg_options = dpkg_options
        self.fail_on_autoremove = fail_on_autoremove
        self.force = force
        self.force_apt_get = force_apt_get
        self.install_recommends = install_recommends
        self.lock_timeout = lock_timeout
        self.only_upgrade = only_upgrade
        self.policy_rc_d = policy_rc_d
        self.purge = purge
        self.update_cache = update_cache
        self.update_cache_retries = update_cache_retries
        self.update_cache_retry_max_delay = update_cache_retry_max_delay
        self.upgrade = upgrade
        self.guard = guard
        self.sudo = sudo
        self.su = su

    def __repr__(self):
        return (f"Apt(name={self.name!r}, state={self.state!r}, "
                f"allow_change_held_packages={self.allow_change_held_packages!r}, "
                f"allow_downgrade={self.allow_downgrade!r}, "
                f"allow_unauthenticated={self.allow_unauthenticated!r}, "
                f"auto_install_module_deps={self.auto_install_module_deps!r}, "
                f"autoclean={self.autoclean!r}, autoremove={self.autoremove!r}, "
                f"cache_valid_time={self.cache_valid_time!r}, clean={self.clean!r}, "
                f"deb={self.deb!r}, default_release={self.default_release!r}, "
                f"dpkg_options={self.dpkg_options!r}, "
                f"fail_on_autoremove={self.fail_on_autoremove!r}, force={self.force!r}, "
                f"force_apt_get={self.force_apt_get!r}, "
                f"install_recommends={self.install_recommends!r}, "
                f"lock_timeout={self.lock_timeout!r}, only_upgrade={self.only_upgrade!r}, "
                f"policy_rc_d={self.policy_rc_d!r}, purge={self.purge!r}, "
                f"update_cache={self.update_cache!r}, "
                f"update_cache_retries={self.update_cache_retries!r}, "
                f"update_cache_retry_max_delay={self.update_cache_retry_max_delay!r}, "
                f"upgrade={self.upgrade!r}, guard={self.guard!r}, "
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        # Build the apt command based on the provided parameters
        cmd_parts = []

        # Determine which apt tool to use
        apt_tool = "apt-get" if self.force_apt_get else "apt"

        # Handle state-specific commands
        if self.state == "absent":
            cmd_parts.append(apt_tool)
            cmd_parts.append("remove")
            if self.purge:
                cmd_parts.append("--purge")
        elif self.state == "build-dep":
            cmd_parts.append(apt_tool)
            cmd_parts.append("build-dep")
        elif self.state == "latest":
            cmd_parts.append(apt_tool)
            cmd_parts.append("install")
        elif self.state == "present":
            cmd_parts.append(apt_tool)
            cmd_parts.append("install")
        elif self.state == "fixed":
            cmd_parts.append(apt_tool)
            cmd_parts.append("install")
            cmd_parts.append("--fix-broken")

        # Add package names or .deb file
        if self.deb:
            cmd_parts.append(self.deb)
        elif self.name:
            cmd_parts.extend(self.name)

        # Add various options
        if self.allow_change_held_packages:
            cmd_parts.append("--allow-change-held-packages")

        if self.allow_downgrade:
            cmd_parts.append("--allow-downgrades")

        if self.allow_unauthenticated:
            cmd_parts.append("--allow-unauthenticated")

        if self.autoclean:
            cmd_parts.append("autoclean")

        if self.autoremove:
            cmd_parts.append("autoremove")

        if self.clean:
            cmd_parts.append("clean")

        if self.default_release:
            cmd_parts.extend(["-t", self.default_release])

        # Add dpkg options
        if self.dpkg_options:
            options = self.dpkg_options.split(",")
            for option in options:
                cmd_parts.extend(["-o", f"Dpkg::Options::=--{option}"])

        if self.fail_on_autoremove:
            cmd_parts.append("--no-remove")

        if self.force:
            cmd_parts.append("--force-yes")

        if self.install_recommends is not None:
            if self.install_recommends:
                cmd_parts.append("--install-recommends")
            else:
                cmd_parts.append("--no-install-recommends")

        if self.only_upgrade:
            cmd_parts.append("--only-upgrade")

        if self.purge and self.state != "absent":
            cmd_parts.append("--purge")

        # Handle upgrade separately as it's a different operation
        if self.upgrade != "no" and self.state not in ["absent", "present", "latest"]:
            if self.upgrade in ["safe", "yes"]:
                cmd_parts = [apt_tool, "safe-upgrade"]
            elif self.upgrade == "full":
                cmd_parts = [apt_tool, "full-upgrade"]
            elif self.upgrade == "dist":
                cmd_parts = [apt_tool, "dist-upgrade"]

        # Handle cache update
        if self.update_cache:
            update_cmd = " ".join([apt_tool, "update"])
            # First execute cache update
            yield Command(update_cmd, guard=self.guard, sudo=self.sudo, su=self.su)

        # Build the final command
        cmd = " ".join(cmd_parts)

        # Execute the main apt command
        r = yield Command(cmd, guard=self.guard, sudo=self.sudo, su=self.su)
        r.changed = True