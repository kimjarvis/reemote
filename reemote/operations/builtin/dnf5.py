# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class Dnf5:
    """
    A class to manage packages with the dnf5 package manager on Unix-like operating systems.
    It allows users to install, upgrade, remove, and list packages and groups with various options.

    Attributes:
        name (list): A package name or package specifier with version.
        state (str): Whether to install (present, latest), or remove (absent) a package.
        allow_downgrade (bool): Allow downgrading packages.
        allowerasing (bool): Allow erasing of installed packages to resolve dependencies.
        auto_install_module_deps (bool): Automatically install dependencies required to run this module.
        autoremove (bool): Remove "leaf" packages that are no longer required.
        best (bool): Use package with highest version available or fail.
        bugfix (bool): Install only bugfix updates when state=latest.
        cacheonly (bool): Run entirely from system cache.
        conf_file (str): Remote dnf configuration file to use.
        disable_excludes (str): Disable excludes defined in DNF config files.
        disable_gpg_check (bool): Disable GPG checking of signatures.
        disable_plugin (list): Plugin names to disable for the operation.
        disablerepo (list): Repository IDs to disable for the operation.
        download_dir (str): Directory to store downloaded packages.
        download_only (bool): Only download packages, do not install.
        enable_plugin (list): Plugin names to enable for the operation.
        enablerepo (list): Repository IDs to enable for the operation.
        exclude (list): Package names to exclude when installing/updating.
        install_repoquery (bool): Deprecated option (no-op in DNF).
        install_weak_deps (bool): Install packages linked by weak dependency relation.
        installroot (str): Alternative installroot path.
        list (str): Non-idempotent commands for usage with ansible.
        lock_timeout (int): Time to wait for dnf lockfile (currently no-op).
        nobest (bool): Opposite of best option for backwards compatibility.
        releasever (str): Alternative release version for installation.
        security (bool): Install only security updates when state=latest.
        skip_broken (bool): Skip unavailable packages with broken dependencies.
        sslverify (bool): Disable SSL validation of repository server.
        update_cache (bool): Force dnf to check if cache is out of date.
        update_only (bool): Only update installed packages, don't install new ones.
        validate_certs (bool): No-op parameter for compatibility.
        guard (bool): If `False` the commands will not be executed.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.

    **Examples:**

    .. code:: python

        # Install the latest version of Apache
        r = yield Dnf5(name="httpd", state="latest")
        print(r.cp.stdout)

        # Install Apache >= 2.4
        r = yield Dnf5(name="httpd >= 2.4", state="present")
        print(r.cp.stdout)

        # Remove the Apache package
        r = yield Dnf5(name="httpd", state="absent")
        print(r.cp.stdout)

        # Upgrade all packages
        r = yield Dnf5(name="*", state="latest")
        print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the various parameters and flags.
        - Requires python3-libdnf5 on the target system.
    """

    def __init__(self,
                 name=None,
                 state=None,
                 allow_downgrade=False,
                 allowerasing=False,
                 auto_install_module_deps=True,
                 autoremove=False,
                 best=None,
                 bugfix=False,
                 cacheonly=False,
                 conf_file=None,
                 disable_excludes=None,
                 disable_gpg_check=False,
                 disable_plugin=None,
                 disablerepo=None,
                 download_dir=None,
                 download_only=False,
                 enable_plugin=None,
                 enablerepo=None,
                 exclude=None,
                 install_repoquery=True,
                 install_weak_deps=True,
                 installroot="/",
                 list=None,
                 lock_timeout=30,
                 nobest=None,
                 releasever=None,
                 security=False,
                 skip_broken=False,
                 sslverify=True,
                 update_cache=False,
                 update_only=False,
                 validate_certs=True,
                 guard=True,
                 sudo=True):

        self.name = name or []
        self.state = state
        self.allow_downgrade = allow_downgrade
        self.allowerasing = allowerasing
        self.auto_install_module_deps = auto_install_module_deps
        self.autoremove = autoremove
        self.best = best
        self.bugfix = bugfix
        self.cacheonly = cacheonly
        self.conf_file = conf_file
        self.disable_excludes = disable_excludes
        self.disable_gpg_check = disable_gpg_check
        self.disable_plugin = disable_plugin or []
        self.disablerepo = disablerepo or []
        self.download_dir = download_dir
        self.download_only = download_only
        self.enable_plugin = enable_plugin or []
        self.enablerepo = enablerepo or []
        self.exclude = exclude or []
        self.install_repoquery = install_repoquery
        self.install_weak_deps = install_weak_deps
        self.installroot = installroot
        self.list = list
        self.lock_timeout = lock_timeout
        self.nobest = nobest
        self.releasever = releasever
        self.security = security
        self.skip_broken = skip_broken
        self.sslverify = sslverify
        self.update_cache = update_cache
        self.update_only = update_only
        self.validate_certs = validate_certs
        self.guard = guard
        self.sudo = sudo

    def __repr__(self):
        return (f"Dnf5(name={self.name!r}, "
                f"state={self.state!r}, "
                f"allow_downgrade={self.allow_downgrade!r}, "
                f"allowerasing={self.allowerasing!r}, "
                f"auto_install_module_deps={self.auto_install_module_deps!r}, "
                f"autoremove={self.autoremove!r}, "
                f"best={self.best!r}, "
                f"bugfix={self.bugfix!r}, "
                f"cacheonly={self.cacheonly!r}, "
                f"conf_file={self.conf_file!r}, "
                f"disable_excludes={self.disable_excludes!r}, "
                f"disable_gpg_check={self.disable_gpg_check!r}, "
                f"disable_plugin={self.disable_plugin!r}, "
                f"disablerepo={self.disablerepo!r}, "
                f"download_dir={self.download_dir!r}, "
                f"download_only={self.download_only!r}, "
                f"enable_plugin={self.enable_plugin!r}, "
                f"enablerepo={self.enablerepo!r}, "
                f"exclude={self.exclude!r}, "
                f"install_repoquery={self.install_repoquery!r}, "
                f"install_weak_deps={self.install_weak_deps!r}, "
                f"installroot={self.installroot!r}, "
                f"list={self.list!r}, "
                f"lock_timeout={self.lock_timeout!r}, "
                f"nobest={self.nobest!r}, "
                f"releasever={self.releasever!r}, "
                f"security={self.security!r}, "
                f"skip_broken={self.skip_broken!r}, "
                f"sslverify={self.sslverify!r}, "
                f"update_cache={self.update_cache!r}, "
                f"update_only={self.update_only!r}, "
                f"validate_certs={self.validate_certs!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r})")

    def _build_command(self):
        """Build the dnf5 command based on the provided parameters."""
        cmd_parts = ["dnf5"]

        # Add boolean flags
        if self.allow_downgrade:
            cmd_parts.append("--allow-downgrade")
        if self.allowerasing:
            cmd_parts.append("--allowerasing")
        if self.autoremove:
            cmd_parts.append("--autoremove")
        if self.best is True:
            cmd_parts.append("--best")
        elif self.best is False:
            cmd_parts.append("--no-best")
        if self.bugfix:
            cmd_parts.append("--bugfix")
        if self.cacheonly:
            cmd_parts.append("--cacheonly")
        if self.disable_gpg_check:
            cmd_parts.append("--nogpgcheck")
        if self.download_only:
            cmd_parts.append("--downloadonly")
        if self.security:
            cmd_parts.append("--security")
        if self.skip_broken:
            cmd_parts.append("--skip-broken")
        if self.sslverify is False:
            cmd_parts.append("--nosslverify")
        if self.update_cache:
            cmd_parts.append("--refresh")
        if self.update_only:
            cmd_parts.append("--upgrade-ignore-installed")

        # Add string/numeric options
        if self.conf_file:
            cmd_parts.extend(["--config", self.conf_file])
        if self.disable_excludes:
            cmd_parts.extend(["--disableexcludes", self.disable_excludes])
        if self.download_dir:
            cmd_parts.extend(["--downloaddir", self.download_dir])
        if self.installroot != "/":
            cmd_parts.extend(["--installroot", self.installroot])
        if self.list:
            cmd_parts.extend(["--list", self.list])
        if self.releasever:
            cmd_parts.extend(["--releasever", self.releasever])

        # Add list options
        for plugin in self.disable_plugin:
            cmd_parts.extend(["--disableplugin", plugin])
        for repo in self.disablerepo:
            cmd_parts.extend(["--disablerepo", repo])
        for plugin in self.enable_plugin:
            cmd_parts.extend(["--enableplugin", plugin])
        for repo in self.enablerepo:
            cmd_parts.extend(["--enablerepo", repo])
        for pkg in self.exclude:
            cmd_parts.extend(["--exclude", pkg])

        # Handle state and name
        if self.state == "absent" or self.state == "removed":
            cmd_parts.append("remove")
        elif self.state == "latest":
            cmd_parts.append("upgrade")
        elif self.state == "present" or self.state == "installed":
            cmd_parts.append("install")
        else:
            cmd_parts.append("list")  # Default action

        # Add package names
        if isinstance(self.name, list):
            cmd_parts.extend(self.name)
        elif self.name:
            cmd_parts.append(self.name)

        return " ".join(cmd_parts)

    def execute(self):
        """Execute the dnf5 command."""
        cmd = self._build_command()
        # print(f"Executing: {cmd}")
        r = yield Command(cmd, guard=self.guard, sudo=self.sudo)
        r.changed = True