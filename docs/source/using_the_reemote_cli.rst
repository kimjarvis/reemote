Using the Reemote CLI
=====================

The Reemote CLI
---------------

The reemote CLI is a handy way to get your Reemote classes running quickly.

.. code-block:: bash

    usage: usage: reemote [-h] [-i INVENTORY_FILE] [-s SOURCE_FILE] [-c CLASS_NAME]

    Process inventory and source files with a specified class

    positional arguments:
      command               Command to execute (everything after --)

    options:
      -h, --help            show this help message and exit
      -i, --inventory INVENTORY
                            Path to the inventory Python file (.py extension required)
      -s, --source SOURCE   Path to the source Python file (.py extension required)
      -c, --class _CLASS    Name of the class in source file that has an execute(self) method
      --parameters PARAMETERS
                            Comma-separated key=value pairs
      -o, --output OUTPUT_FILE
                            Path to the output file where results will be saved
      -t, --type TYPE       Output format type: "grid", "json", or "rst"

            Example: reemote -i ~/inventory.py -s development/examples/main.py -c Info_example
                     reemote -i ~/inventory.py -- echo "hello"

Ad-hoc commands with reemote
----------------------------

You can start reemote immediately with some ad-hoc command execution.

.. code-block:: bash

    reemote -i ~/inventory.py -- echo "hello"

This will run the command ``echo "hello"`` on all of the servers in the inventory.
The stdout will be written to the console, our inventory contains two servers so
hello will appear twice.

.. code-block:: bash

    hello
    hello
    +------------+--------------------------+-------------------------+--------------------------+-------------------------+
    | Command    | 10.156.135.16 Executed   | 10.156.135.16 Changed   | 10.156.135.19 Executed   | 10.156.135.19 Changed   |
    +============+==========================+=========================+==========================+=========================+
    | echo hello | True                     | True                    | True                     | True                    |
    +------------+--------------------------+-------------------------+--------------------------+-------------------------+

Running a deployment
--------------------

A deploment is a collection of operations defined in Python files.
For example, the file examples/documentation/main.py contains the class Get_os_example.

.. code-block:: python

    class Get_os_example:
        def execute(self):
            from reemote.facts.server.get_os import Get_OS
            r = yield Get_OS("NAME")

The example can be executed like this:

.. code-block:: bash

    reemote -i ~/inventory.py -s examples/documentation/main.py -c Get_os_example

This will run the command deployment on all of the servers in the inventory.
The stdout will be written to the console, our inventory contains two servers so
the output will appear twice.

.. code-block:: bash

    Alpine Linux
    Alpine Linux
    +---------------------+--------------------------+-------------------------+--------------------------+-------------------------+
    | Command             | 10.156.135.16 Executed   | 10.156.135.16 Changed   | 10.156.135.19 Executed   | 10.156.135.19 Changed   |
    +=====================+==========================+=========================+==========================+=========================+
    | cat /etc/os-release | True                     | True                    | True                     | True                    |
    +---------------------+--------------------------+-------------------------+--------------------------+-------------------------+

