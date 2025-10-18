# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class Pip:
    """
    A class to encapsulate the functionality of managing Python library dependencies
    using pip in Unix-like operating systems. This class allows users to install,
    uninstall, and manage Python packages with various options such as virtual
    environments, version specifications, and extra arguments.

    Attributes:
        name (list): The name(s) of Python library(ies) to install or the URL(s) of remote packages.
        requirements (str): The path to a pip requirements file.
        state (str): The state of the module (present, absent, latest, forcereinstall).
        version (str): The version number to install of the Python library.
        executable (str): The explicit executable or pathname for the pip executable.
        umask (str): The system umask to apply before installing the pip package.
        chdir (str): Directory to cd into before running the command.
        virtualenv (str): Path to a virtualenv directory to install into.
        virtualenv_command (str): Command to create the virtual environment.
        virtualenv_python (str): Python executable used for creating the virtual environment.
        virtualenv_site_packages (bool): Whether the virtual environment inherits global site-packages.
        extra_args (str): Extra arguments passed to pip.
        editable (bool): Pass the editable flag.
        break_system_packages (bool): Allow pip to modify externally-managed Python installations.
        guard (bool): If `False` the commands will not be executed.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        # Install a Python package
        r = yield Pip(name="bottle")
        print(r.cp.stdout)

        # Install a specific version of a package
        r = yield Pip(name="bottle", version="0.11")
        print(r.cp.stdout)

        # Install packages from a requirements file
        r = yield Pip(requirements="/my_app/requirements.txt")
        print(r.cp.stdout)

        # Install package in a virtual environment
        r = yield Pip(name="bottle", virtualenv="/my_app/venv")
        print(r.cp.stdout)

        # Install package with extra arguments
        r = yield Pip(name="bottle", extra_args="--user")
        print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the provided parameters and flags.
        - Virtual environments will be created if they don't exist.
        - The pip module shells out to run the actual pip command.
    """

    def __init__(self,
                 name=None,
                 requirements=None,
                 state="present",
                 version=None,
                 executable=None,
                 umask=None,
                 chdir=None,
                 virtualenv=None,
                 virtualenv_command="virtualenv",
                 virtualenv_python=None,
                 virtualenv_site_packages=False,
                 extra_args=None,
                 editable=False,
                 break_system_packages=False,
                 guard=True,
                 sudo=False,
                 su=False):

        self.name = name
        self.requirements = requirements
        self.state = state
        self.version = version
        self.executable = executable
        self.umask = umask
        self.chdir = chdir
        self.virtualenv = virtualenv
        self.virtualenv_command = virtualenv_command
        self.virtualenv_python = virtualenv_python
        self.virtualenv_site_packages = virtualenv_site_packages
        self.extra_args = extra_args
        self.editable = editable
        self.break_system_packages = break_system_packages
        self.guard = guard
        self.sudo = sudo
        self.su = su

    def __repr__(self):
        return (f"Pip(name={self.name!r}, "
                f"requirements={self.requirements!r}, "
                f"state={self.state!r}, "
                f"version={self.version!r}, "
                f"executable={self.executable!r}, "
                f"umask={self.umask!r}, "
                f"chdir={self.chdir!r}, "
                f"virtualenv={self.virtualenv!r}, "
                f"virtualenv_command={self.virtualenv_command!r}, "
                f"virtualenv_python={self.virtualenv_python!r}, "
                f"virtualenv_site_packages={self.virtualenv_site_packages!r}, "
                f"extra_args={self.extra_args!r}, "
                f"editable={self.editable!r}, "
                f"break_system_packages={self.break_system_packages!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        # Build the pip command
        cmd_parts = []

        # Handle umask
        if self.umask:
            cmd_parts.append(f"umask {self.umask} &&")

        # Handle chdir
        if self.chdir:
            cmd_parts.append(f"cd {self.chdir} &&")

        # Handle virtualenv creation if needed
        if self.virtualenv:
            # Check if virtualenv exists, if not create it
            cmd_parts.append(f"[ -d {self.virtualenv} ] || {self.virtualenv_command}")
            if self.virtualenv_python:
                cmd_parts.append(f"--python={self.virtualenv_python}")
            if self.virtualenv_site_packages:
                cmd_parts.append("--system-site-packages")
            cmd_parts.append(f"{self.virtualenv} &&")

        # Determine pip executable
        if self.executable:
            pip_cmd = self.executable
        elif self.virtualenv:
            pip_cmd = f"{self.virtualenv}/bin/pip"
        else:
            pip_cmd = "pip"

        cmd_parts.append(pip_cmd)

        # Handle state
        if self.state == "absent":
            cmd_parts.append("uninstall -y")
        elif self.state == "forcereinstall":
            cmd_parts.append("install --force-reinstall")
        elif self.state == "latest":
            cmd_parts.append("install --upgrade")
        else:  # present
            cmd_parts.append("install")

        # Handle editable flag
        if self.editable:
            cmd_parts.append("-e")

        # Handle break_system_packages
        if self.break_system_packages:
            cmd_parts.append("--break-system-packages")

        # Add package names or requirements file
        if self.name:
            if isinstance(self.name, list):
                cmd_parts.extend(self.name)
            else:
                cmd_parts.append(self.name)
        elif self.requirements:
            cmd_parts.append(f"-r {self.requirements}")

        # Add extra arguments
        if self.extra_args:
            cmd_parts.append(self.extra_args)

        # Join all parts into a single command
        final_cmd = " ".join(cmd_parts)

        # Execute the command
        r = yield Command(final_cmd, guard=self.guard, sudo=self.sudo, su=self.su)
        r.changed = True
```