Using the Reemote CLI
=====================

The Reemote CLI
---------------

The reemote CLI is a handy way to get your Reemote deployments running quickly.

.. code-block:: bash

    usage: usage: reemote [-h] -i INVENTORY_FILE -s SOURCE_FILE -c CLASS_NAME -k KWARGS [-- COMMAND]

    CLI tool with inventory, source, class, and command options.

    positional arguments:
      command               Command to execute (everything after --)

    options:
      -h, --help            show this help message and exit
      -i, --inventory INVENTORY
                            Path to the inventory Python file (.py extension required)
      -s, --source SOURCE   Path to the source Python file (.py extension required)
      -c, --class _CLASS    Name of the deployment class in source file that has an execute(self) method
      -k, --kwargs KWARGS   Path to the kwargs file for the deployment class constructor
      -o, --output OUTPUT_FILE
                            Path to the output file where results will be saved
      -t, --type TYPE       Output format type: "grid", "json", or "rst"

            Example:
              reemote -i ~/inventory_alpine1.py -s reemote/operations/users/add_user.py -c Add_user -k user='abc',password='def' -o output.txt -t json

Ad-hoc commands with reemote
----------------------------

You can use the shell operation to run Ad-hoc shell commands on servers.

.. code-block:: bash

    reemote -i ~/inventory_alpine.py -s reemote/operations/server/shell.py -c Shell -k "cmd=echo 'Hello World'"

This will run the command ``echo "hello"`` on all of the servers in the inventory.
The stdout will be written to the console, our inventory contains two servers so
hello will appear twice.

.. code-block:: bash

    +--------------------+--------------------------+-------------------------+--------------------------+-------------------------+
    | Command            | 10.156.135.16 Executed   | 10.156.135.16 Changed   | 10.156.135.19 Executed   | 10.156.135.19 Changed   |
    +====================+==========================+=========================+==========================+=========================+
    | echo 'Hello World' | True                     | True                    | True                     | True                    |
    +--------------------+--------------------------+-------------------------+--------------------------+-------------------------+
    +--------------------+----------------------------+------------------------+----------------------------+------------------------+
    | Command            |   10.156.135.16 Returncode | 10.156.135.16 Stdout   |   10.156.135.19 Returncode | 10.156.135.19 Stdout   |
    +====================+============================+========================+============================+========================+
    | echo 'Hello World' |                          0 | Hello World            |                          0 | Hello World            |
    +--------------------+----------------------------+------------------------+----------------------------+------------------------+

Running operations
------------------

This is the Add_user operation defined in reemote/operations/users/add_users.py

.. code-block:: python

    class Add_user:
        def __init__(self, user: str=None, password: str=None):
            self.user = user
            self.password = password

        def __repr__(self):
            return f"Add_sudo_user(user={self.user!r}, hosts={self.password!r})"

        def execute(self):
            from reemote.operations.server.shell import Shell
            yield Shell(f'adduser -D {self.user} && echo "{self.user}:{self.password}" | chpasswd', su=True)

The operation can be executed like this:

.. code-block:: bash

    reemote -i ~/inventory_alpine.py -s reemote/operations/users/add_user.py -c Add_user -k user='username',password='password'

This will run the operationt on all of the servers in the inventory.
Standard output is written to the console by the cli wrapper.
Our inventory contains two servers so the standard output will appear twice.

.. code-block:: bash

    +------------------------------------------------------------+--------------------------+-------------------------+--------------------------+-------------------------+
    | Command                                                    | 10.156.135.16 Executed   | 10.156.135.16 Changed   | 10.156.135.19 Executed   | 10.156.135.19 Changed   |
    +============================================================+==========================+=========================+==========================+=========================+
    | adduser -D username && echo "username:password" | chpasswd | True                     | True                    | True                     | True                    |
    +------------------------------------------------------------+--------------------------+-------------------------+--------------------------+-------------------------+
    +------------------------------------------------------------+----------------------------+---------------------------------+----------------------------+-------------------------------------------+
    | Command                                                    |   10.156.135.16 Returncode | 10.156.135.16 Stdout            |   10.156.135.19 Returncode | 10.156.135.19 Stdout                      |
    +============================================================+============================+=================================+============================+===========================================+
    | adduser -D username && echo "username:password" | chpasswd |                          1 | adduser: user 'username' in use |                          0 | chpasswd: password for 'username' changed |
    +------------------------------------------------------------+----------------------------+---------------------------------+----------------------------+-------------------------------------------+
