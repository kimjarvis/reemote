Using the reemote CLI
=====================

The reemote CLI is a handy way to get your remote classes running quickly.  Lets
look at an example.

.. code-block:: python

    reemote ~/inventory.py examples/cli/make_directory.py Make_directory

The command will create a directory on all of the hosts in the inventory.

The first parameter is the path to the inventory Python file.
This is a python source with a function Inventory() that returns an
inventory List object.  See the :doc:`inventory` for more details.

The second parameter is the path to the source Python file.  For example:

.. code-block:: python

    from reemote.operations.filesystem.directory import Directory

    class Make_directory:
        def execute(self):
            yield Directory(path="/tmp/mydir", present=True, sudo=True)

The third parameter is the name of the class in source file that has an execute(self) method  In this case `Make_directory`.


