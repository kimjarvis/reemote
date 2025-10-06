Facts and Operations
====================

Facts
-----

Facts return information about the target system.

**Example:**

The facts.apk.get_packages() returns a formatted list of installed apk packages.

.. code:: bash

    reemote -i=inventory.py -s facts/apk/get_packages.py -c Get_packages

Commands
--------

Commands are executed on the target system.

* **guard parameter** indicates whether the command should be executed.

**Example:**

The commands.apk.packages() command installs apk packages.

.. code:: bash

    reemote -i=inventory.py -s operations/apk/install.py -c Install -k ['vim']

Operations
----------

Operations are declarative and idempotent.  They describe the desired state of the target system.

**Example:**

The operations.apk.packages() operation either installs or removes apk packages to a achieve the desired state.

.. code:: bash

    reemote -i=inventory.py -s operations/apk/packages.py -c Packages -k ['vim'],present=True

Deployments
-----------

A deployment is a stand alone Python program with an inventory parameter

**Example:**

The deployments.files.write_text_to_file deployment demonstrates writing a file using scp.

.. code:: bash

    python3 -i=inventory.py deployments/files/write_text_to_file.py

.. code:: python

    import asyncio
    from reemote.deployment import main

    class Write_text_to_file:
        def execute(self):
            from reemote.operations.sftp.write_file import Write_file
            yield Write_file(path='hello.txt',text='Hello World!')

    if __name__ == "__main__":
        asyncio.run(main(Write_text_to_file))

The deployment can also be called from the command line.

.. code:: bash

    reemote -i ~/reemote/inventory-proxmox-alpine.py \
    -s ~/reemote/reemote/deployments/files/write_text_to_file.py \
    -c Write_text_to_file
