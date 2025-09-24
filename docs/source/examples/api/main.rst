.. _api-example:

Make Directory API Example
--------------------------

This command line application illustrates how to creates or deletes a directory on all the servers in the inventory.

To run the example:

.. code-block:: bash

    python3 examples/api/main.py -i ~/inventory_debian1.py -d /tmp/mydir -p True

The inventory is specified with the -i keyword and passed to the application.  The directory to be created or removed
is passed on the -d keyword.  The -p keyword indicates whether the directory shall be Present (True) or absent (False).

The most important part is where the Directory class is called.

.. code-block:: python

    responses = await execute(inventory(), Directory(path=args.directory, present=args.present))
    print(produce_table(produce_json(responses)))
    print(produce_output_table(produce_json(responses)))


