Using the Reemote CLI
=====================

The Reemote CLI
---------------

The reemote CLI is a handy way to get your Reemote classes running quickly.

.. code-block:: bash

    reemote --help
    usage: usage: reemote [-h] [-i INVENTORY_FILE] [-s SOURCE_FILE] [-c CLASS_NAME] [--gui | --cli]

    Process inventory and source files with a specified class

    options:
      -h, --help            show this help message and exit
      --gui                 Use the GUI to upload inventory and view execution results (default)
      --cli                 Use the CLI to process inventory and source files
      -i, --inventory INVENTORY_FILE
                            Path to the inventory Python file (.py extension required)
      -s, --source SOURCE_FILE
                            Path to the source Python file (.py extension required)
      -c, --class CLASS_NAME
                            Name of the class in source file that has an execute(self) method
      -o, --output OUTPUT_FILE
                            Path to the output file where results will be saved
      -t, --type TYPE       Output format type: "grid", "json", or "rst"

    Example: reemote --cli -i ~/inventory.py -s examples/cli/make_directory.py -c Make_directory

The cli flag causes the class to be run in the CLI.  The execution results are presented in a table.
When the cli flag is used the inventory parameter is required.

The gui flag causes a browser session to start.  The inventory is choosen from the GUI.  The execution results are
presented in the GUI.
When the gui flag is used the inventory parameter is not used.

The source file path identifes a file containing the class to be executed.
The class name identifies the class in this file.




