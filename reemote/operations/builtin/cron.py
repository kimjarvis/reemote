# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class Cron:
    """
    A class to manage cron.d and crontab entries in Unix-like operating systems.
    This class allows you to create environment variables and named crontab entries,
    update, or delete them.

    When crontab jobs are managed: the module includes one line with the description
    of the crontab entry "#Ansible: <name>" corresponding to the name passed to the
    module, which is used by future ansible/module calls to find/check the state.
    The name parameter should be unique, and changing the name value will result in
    a new cron task being created (or a different one being removed).

    When environment variables are managed, no comment line is added, but, when the
    module needs to find/check the state, it uses the name parameter to find the
    environment variable definition line.

    When using symbols such as %, they must be properly escaped.

    Attributes:
        name (str): Description of a crontab entry or, if env is set, the name of environment variable.
        user (str): The specific user whose crontab should be modified.
        minute (str): Minute when the job should run (0-59, *, */2, and so on).
        hour (str): Hour when the job should run (0-23, *, */2, and so on).
        day (str): Day of the month the job should run (1-31, *, */2, and so on).
        month (str): Month of the year the job should run (JAN-DEC or 1-12, *, */2, and so on).
        weekday (str): Day of the week that the job should run (SUN-SAT or 0-6, *, and so on).
        job (str): The command to execute or, if env is set, the value of environment variable.
        special_time (str): Special time specification nickname.
        disabled (bool): If the job should be disabled (commented out) in the crontab.
        env (bool): If set, manages a crontab's environment variable.
        state (str): Whether to ensure the job or environment variable is present or absent.
        backup (bool): If set, create a backup of the crontab before it is modified.
        cron_file (str): If specified, uses this file instead of an individual user's crontab.
        insertafter (str): Used with state=present and env. Insert after specified environment variable.
        insertbefore (str): Used with state=present and env. Insert before specified environment variable.

    **Examples:**

    .. code:: python

        # Ensure a job that runs at 2 and 5 exists
        r = yield Cron(
            name="check dirs",
            minute="0",
            hour="5,2",
            job="ls -alh > /dev/null"
        )

        # Ensure an old job is no longer present
        r = yield Cron(
            name="an old job",
            state="absent"
        )

        # Creates an entry like "@reboot /some/job.sh"
        r = yield Cron(
            name="a job for reboot",
            special_time="reboot",
            job="/some/job.sh"
        )

        # Creates an entry like "PATH=/opt/bin" on top of crontab
        r = yield Cron(
            name="PATH",
            env=True,
            job="/opt/bin"
        )

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - If you are experiencing permissions issues with cron and MacOS, you should see the official MacOS documentation for further information.
        - Requires cron (any 'vixie cron' conformant variant, like cronie)
    """

    def __init__(self,
                 name: str,
                 user: str = None,
                 minute: str = "*",
                 hour: str = "*",
                 day: str = "*",
                 month: str = "*",
                 weekday: str = "*",
                 job: str = None,
                 special_time: str = None,
                 disabled: bool = False,
                 env: bool = False,
                 state: str = "present",
                 backup: bool = False,
                 cron_file: str = None,
                 insertafter: str = None,
                 insertbefore: str = None):

        self.name = name
        self.user = user
        self.minute = minute
        self.hour = hour
        self.day = day
        self.month = month
        self.weekday = weekday
        self.job = job
        self.special_time = special_time
        self.disabled = disabled
        self.env = env
        self.state = state
        self.backup = backup
        self.cron_file = cron_file
        self.insertafter = insertafter
        self.insertbefore = insertbefore

    def __repr__(self):
        return (f"Cron(name={self.name!r}, "
                f"user={self.user!r}, "
                f"minute={self.minute!r}, "
                f"hour={self.hour!r}, "
                f"day={self.day!r}, "
                f"month={self.month!r}, "
                f"weekday={self.weekday!r}, "
                f"job={self.job!r}, "
                f"special_time={self.special_time!r}, "
                f"disabled={self.disabled!r}, "
                f"env={self.env!r}, "
                f"state={self.state!r}, "
                f"backup={self.backup!r}, "
                f"cron_file={self.cron_file!r}, "
                f"insertafter={self.insertafter!r}, "
                f"insertbefore={self.insertbefore!r})")

    def execute(self):
        # Build the command arguments
        cmd_args = ["ansible.builtin.cron"]

        # Add required name parameter
        cmd_args.append(f"name={self.name}")

        # Add optional parameters
        if self.user:
            cmd_args.append(f"user={self.user}")
        if self.minute != "*":
            cmd_args.append(f"minute={self.minute}")
        if self.hour != "*":
            cmd_args.append(f"hour={self.hour}")
        if self.day != "*":
            cmd_args.append(f"day={self.day}")
        if self.month != "*":
            cmd_args.append(f"month={self.month}")
        if self.weekday != "*":
            cmd_args.append(f"weekday={self.weekday}")
        if self.job:
            cmd_args.append(f"job={self.job}")
        if self.special_time:
            cmd_args.append(f"special_time={self.special_time}")
        if self.disabled:
            cmd_args.append("disabled=yes")
        if self.env:
            cmd_args.append("env=yes")
        if self.state != "present":
            cmd_args.append(f"state={self.state}")
        if self.backup:
            cmd_args.append("backup=yes")
        if self.cron_file:
            cmd_args.append(f"cron_file={self.cron_file}")
        if self.insertafter:
            cmd_args.append(f"insertafter={self.insertafter}")
        if self.insertbefore:
            cmd_args.append(f"insertbefore={self.insertbefore}")

        # Construct the full command
        cmd = " ".join(cmd_args)

        # Execute the command
        r = yield Command(cmd)
        r.changed = True