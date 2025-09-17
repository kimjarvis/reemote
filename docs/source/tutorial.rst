Tutorial
========

Order of execution
------------------

Lets execute this class on two servers.

.. code-block:: python

    class Hello_world:
        def execute(self):
            from reemote.operations.server.shell import Shell
            from reemote.result import Result
            r0 : Results = yield Shell("echo 'Hello'")
            print(r0.cp.stdout)
            r1 : Result = yield Shell("echo 'World!'")
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

The Shell command echo 'Hello' is sent to both servers. The results are received from both servers before the next Shell command, echo 'World!', is executed.

Reemote ensures that each operation is executed on all servers before proceeding to the next operation. After executing an operation, a Result object (here represented as R0, R1) is generated and made available in the class.

Each Result object contains the following key fields:

    * changed: Indicates whether the operation modified anything on the host.
    * executed: Specifies where the operation was executed.


Additionally, the Result object provides access to the following subprocess details:

    * cp.returncode: The return code of the executed command.
    * cp.stdout: The standard output produced by the command.
    * cp.stderr: The standard error output, if any, produced by the command.

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


The ``Packages`` class is a **declarative** and **idempotent** resource.
Its purpose is to ensure the ``wget`` package is either present on or absent from the server,
depending on the specified option.

The class declares a desired state (e.g., ``wget`` must be present).
Executing the class multiple times will not change the final outcome, making it idempotent.
Whether the package was already in the desired state or not is irrelevant.

The **Executed** flag for the ``Packages`` class is set to ``True``, indicating that the
class logic ran.

The **Changed** flag indicates whether the system's state was modified.

*   The result of the ``apt list --installed`` check after the commands are executed
    is compared to the initial state.
*   If the lists are unequal (e.g., the ``wget`` package was missing and needed to be installed),
    the Changed flag is set to ``True`` for
    both the specific install operation and the overall ``Packages`` class.


Reemote does not wrap shell commands
------------------------------------

Simple shell commands, such as the "which wget" in the example above are not wrapped in Classes to make them
delarative and idempotent.  In some cases, they could be.  But in general, Reemote takes the approach that it is
better to be clear what is going on, rather than obfuscate simple operations behind wrappers.  Shell commands are
assumed to change the host.  In the case of the "which wget" command no changes occur on the host.

You can, of course, set the changed flag manually, like this:

.. code-block:: python

    class Which_wget:
        def execute(self):
            from reemote.operations.server.shell import Shell
            r0 = yield Shell("which wget")
            r0.changed = False

.. code-block:: bash

    reemote --cli -i ~/inventory3.py -s examples/documentation/tutorial.py -c Which_wget
    +------------+----------------------------+---------------------------+
    | Command    | 192.168.122.143 Executed   | 192.168.122.143 Changed   |
    +============+============================+===========================+
    | which wget | True                       | False                     |
    +------------+----------------------------+---------------------------+

Reemote does not execute in phases
----------------------------------

Configuration management tools, such as Ansible execute in phases.  Reemote does not do phases.  When an Ansible
Playbook (script) is run it tries all of the operations and creates a report on which operations changed anything on the hosts.
The user is then prompted whether to go ahead and apply the changes in the Playbook (script) to the hosts.

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

Configuration management tools, such as Ansible, use **facts**---immutable
values gathered about a remote system at the start of an execution run.

These facts are used to make decisions within playbooks.
A common example is detecting the operating system to determine the package
manager (e.g., ``apt``, ``yum``, ``dnf``)
to use for installing software.

Unlike Ansible, Remote does not implement a dedicated
class-based system for fact gathering. As demonstrated previously,
it is straightforward to gather these values by parsing the output of shell commands.

However, because it is also simple to create classes that
return structured fact data, this guidance is often ignored, as the examples below will illustrate.

Reemote is composable
---------------------

Reemote classes are composable.  A Reemote class can yield another class and all of the operations in that Class are
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

The Get_OS class now returns the name of the OS in stdout.

.. code-block:: bash

    reemote --cli -i ~/inventory3.py -s examples/documentation/tutorial.py -c Show_OS
    Debian 13 (trixie)
    +---------------------+----------------------------+---------------------------+
    | Command             | 192.168.122.143 Executed   | 192.168.122.143 Changed   |
    +=====================+============================+===========================+
    | cat /etc/os-release | True                       | True                      |
    +---------------------+----------------------------+---------------------------+

Callbacks
---------

Callbacks are asynchronous python functions.  They are especially usefull when a python
function should only execute for one host.

.. code-block:: python

    async def callable_function(host_info, sudo_info, command, cp, caller):
        if host_info["host"] == caller.host:
            print(f"callback called for host {caller.host}")

    class Demonstrate_callback:
        def execute(self):
            from reemote.operations.server.callback import Callback
            from reemote.operations.server.shell import Shell
            r = yield Shell("echo 'Hello World!'")
            print(r.cp.stdout)
            yield Callback(host="10.156.135.16", callback=callable_function)

In this example, the function callable_function runs twice once for each host.  An If statement ensures that it only
runs for one host.  It is often convenient to restrict exectuion to the first host in the inventory ``inventory()[0][0]['host']``.

When the callback is run we see:

.. code-block:: bash

    reemote --cli -i ~/inventory10.py -s examples/documentation/tutorial.py -c Demonstrate_callback
    Hello World!
    callback called for host 10.156.135.16
    Hello World!
    +--------------------------------------------------------------------------+--------------------------+-------------------------+--------------------------+-------------------------+
    | Command                                                                  | 10.156.135.16 Executed   | 10.156.135.16 Changed   | 10.156.135.19 Executed   | 10.156.135.19 Changed   |
    +==========================================================================+==========================+=========================+==========================+=========================+
    | echo 'Hello World!'                                                      | True                     | True                    | True                     | True                    |
    +--------------------------------------------------------------------------+--------------------------+-------------------------+--------------------------+-------------------------+
    | Callback(host='10.156.135.16', guard=True, callback='callable_function') | True                     | True                    | True                     | True                    |
    +--------------------------------------------------------------------------+--------------------------+-------------------------+--------------------------+-------------------------+

In particular, the message from the callback is only printed once.  The callback function is asynchronous so
its output may appear before or after the "Hello World!" message from each host.

A callable callback function must have the signature:

.. code-block:: python

    async def callable_function(
        host_info: dict,
        sudo_info: dict,
        command: str,
        cp: object,
        caller: str
    ) -> None:
