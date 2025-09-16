Tutorial
========

Order of execution
------------------

Lets execute this class on two servers.

.. code-block:: python

    class Hello_world:
        from reemote.operations.server.shell import Shell
        def execute(self):
            r0 = yield Shell("echo 'Hello'")
            print(r0.cp.stdout)
            r1 = yield Shell("echo 'World!'")
            print(r1.cp.stdout)

This is the result of the execution.

.. code-block:: bash

    reemote --cli -i ~/inventory.py -s examples/documentation/tutorial.py -c Hello_world
    Hello
    Hello
    World!
    World!
    +--------------+---------------------------+--------------------------+---------------------------+--------------------------+
    | Command      | 192.168.122.24 Executed   | 192.168.122.24 Changed   | 192.168.122.47 Executed   | 192.168.122.47 Changed   |
    +==============+===========================+==========================+===========================+==========================+
    | echo 'Hello' | True                      | True                     | True                      | True                     |
    +--------------+---------------------------+--------------------------+---------------------------+--------------------------+
    | echo 'World' | True                      | True                     | True                      | True                     |
    +--------------+---------------------------+--------------------------+---------------------------+--------------------------+

Crucially, we don't see this:

.. code-block:: bash

    Hello
    World!
    Hello
    World!

The Shell command "echo 'Hello'" is sent to both servers.  The result is received from both servers before the next shell
command echo 'World!' is executed.

Delarative and Idempotent operation
-----------------------------------

Lets execute this class on a Debian server.

.. code-block:: python

    class Install_wget:
        def execute(self):
            from reemote.operations.apt.packages import Packages
            from reemote.operations.server.shell import Shell
            r0 = yield Packages(packages=["wget"], present=True, sudo=True)
            r1 = yield Shell("which wget")
            print(r1.cp.stdout)

This is the result of the execution.

.. code-block:: bash

    reemote --cli -i ~/inventory.py -s examples/documentation/tutorial.py -c Install_wget
    /usr/bin/wget
    +---------------------------------------------------------------------------+----------------------------+---------------------------+
    | Command                                                                   | 192.168.122.143 Executed   | 192.168.122.143 Changed   |
    +===========================================================================+============================+===========================+
    | Packages(packages=['wget'], present=True,guard=True, sudo=True, su=False) | True                       | True                      |
    +---------------------------------------------------------------------------+----------------------------+---------------------------+
    | apt list --installed                                                      | True                       | False                     |
    +---------------------------------------------------------------------------+----------------------------+---------------------------+
    | apt-get install -y wget                                                   | True                       | True                      |
    +---------------------------------------------------------------------------+----------------------------+---------------------------+
    | apt-get remove -y wget                                                    | False                      | False                     |
    +---------------------------------------------------------------------------+----------------------------+---------------------------+
    | apt list --installed                                                      | True                       | False                     |
    +---------------------------------------------------------------------------+----------------------------+---------------------------+
    | which wget                                                                | True                       | True                      |
    +---------------------------------------------------------------------------+----------------------------+---------------------------+

The Packages class is a declarative.  After it is executed the package is present, or absent depending on the option.
Whether the package was already installed or not does not matter at all.

The declartive nature of the Packages class makes it idempotent.  We can run the class as many times as we like without
affecting the outcom.

The Packages class yeilds "apt-get" operations to install the wget package and to remove it.
The present flag indicates that only the install is executed.  We can see this in the Executed column.
The Executed flag of the Packages is also set to True.  This indicates that the Class was exectued.
The Packates class yeilds "apt list --installed" operations to list all of the installed packages.
It compares the results of these two operations to set the Changed flags.
The lists of packages is unequal, becase the wget pacakge has been installed.  This causes the chagned flag to be set
to True for the install operation.  It also causes the changed flag to be set to True for the Packages class.
The wget package is installed and the "which wget" finds it at "/usr/bin/wget".

Reemote does not wrap shell commands
------------------------------------

Simple shell commands, such as the "which wget" in the example above are not wrapped in Classes to make them
delarative and idempotent.  In some cases, they could be.  But in general, reemote takse the approach that it is
better to be clear what is going on, rather than obfuscate simple operations behind wrappers.  Shell commans are
assumed to change the host.  In the case of the "which wget" command no changes occur on the host.

Reemote does not execute in phases
----------------------------------

Configuration management tools, such as Ansible execute in phases.  Reemote does not do phases.  When an Ansible
playbook is run it tries all of the operations and creates a report on which operations changed anything on the hosts.
The user is then prompted whether to go ahead with the script.

Our observation is that the changes report, which is only a guess, is highly unreliable.  Reemote does away with
this aproach.  It goes ahead and performs the operations, giving a reliable report of what happed after the fact.

Reemote does not gather facts
-----------------------------

Lets find out which OS a server is running.

.. code-block:: python

    class Get_OS:
        def execute(self):
            from reemote.operations.server.shell import Shell
            import re
            r0 = yield Shell("cat /etc/os-release")
            # Extract OS name and version
            os_name_match = re.search(r'PRETTY_NAME="([^"]+)"', r0.cp.stdout)
            os_version_match = re.search(r'VERSION="([^"]+)"', r0.cp.stdout)

            if os_name_match and os_version_match:
                os_name = os_name_match.group(1).split()[0]  # Extract "Debian" from "Debian GNU/Linux"
                os_version = os_version_match.group(1)       # Extract "13 (trixie)"
                print(f"OS Name: {os_name} {os_version}")
            else:
                print("Failed to extract OS details.")

.. code-block:: bash

    reemote --cli -i ~/inventory.py -s examples/documentation/tutorial.py -c Get_OS
    OS Name: Debian 13 (trixie)
    +---------------------+----------------------------+---------------------------+
    | Command             | 192.168.122.143 Executed   | 192.168.122.143 Changed   |
    +=====================+============================+===========================+
    | cat /etc/os-release | True                       | True                      |
    +---------------------+----------------------------+---------------------------+

Configuration management tools, such as Ansible facts are imutalbe values gathered at the start of the execution.
Facts are used to make descisions in Ansible playbooks, such as, deciding which packages manager to use.
Reemote does not implement Classes to gather facts.  It is simple enough to gather fact values from the output
of Shell commands.

Reemote is composable
---------------------

Reemote classes are composable.  A reemote class can yield another class and all of the operations in that Class are
executed.  Lets modify the example above to create, what we said we wouldn't, that is, a class that returns a fact.

.. code-block:: python

    class Get_OS:
        def execute(self):
            from reemote.operations.server.shell import Shell
            import re
            r0 = yield Shell("cat /etc/os-release")
            # Extract OS name and version
            os_name_match = re.search(r'PRETTY_NAME="([^"]+)"', r0.cp.stdout)
            os_version_match = re.search(r'VERSION="([^"]+)"', r0.cp.stdout)

            if os_name_match and os_version_match:
                os_name = os_name_match.group(1).split()[0]  # Extract "Debian" from "Debian GNU/Linux"
                os_version = os_version_match.group(1)       # Extract "13 (trixie)"
                r0.cp.stdout = f"{os_name} {os_version}"
            else:
                r0.cp.stdout = "Failed to extract OS details."


    class Show_OS:
        def execute(self):
            r0 = yield Get_OS()
            print(r0.cp.stdout)

The Get_OS class now returns the name of the running OS in stdout.

.. code-block:: bash

    Debian 13 (trixie)
    +---------------------+----------------------------+---------------------------+
    | Command             | 192.168.122.143 Executed   | 192.168.122.143 Changed   |
    +=====================+============================+===========================+
    | cat /etc/os-release | True                       | True                      |
    +---------------------+----------------------------+---------------------------+

