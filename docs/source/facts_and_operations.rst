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

The deployments.apk.packages() deployment either installs or removes apk packages to a achieve the desired state.

.. code:: bash

    python3 -i=inventory.py deployments/apk/packages.py
