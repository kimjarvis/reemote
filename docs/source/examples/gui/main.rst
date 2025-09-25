.. _gui-example:

Make Directory GUI Example
--------------------------

This example creates or deletes a directory on all the servers in the inventory.

.. image:: gui_demo.png
   :width: 100%
   :align: center
   :alt: GUI Demo Screenshot

The Reemote GUI is based on `NiceGUI <https://nicegui.io>`_ .  The Gui class provides methods to upload the
inventory and produce an execution report.

.. code-block:: python

    from nicegui import ui, native, app
    from reemote.gui import Gui
    from reemote.run import run
    from reemote.grid import grid
    from reemote.operations.filesystem.directory import Directory


    async def Control_directory(gui):
        operations, responses = await run(app.storage.user["inventory"],
                                          Directory(path="/tmp/mydir", present=app.storage.user["present"], su=True))
        app.storage.user["columnDefs"],app.storage.user["rowData"] = grid(operations, responses)
        gui.execution_report.refresh()

    @ui.page('/')
    def page():
        gui = Gui()
        gui.upload_inventory()
        ui.switch('Directory /tmp/mydir is present on hosts', value=False).bind_value(app.storage.user, 'present')
        ui.button('Run', on_click=lambda: Control_directory(gui))
        gui.execution_report()


    ui.run(title="Manage directory", reload=False, port=native.find_open_port(),
           storage_secret='private key to secure the browser session cookie')


The Gui class contains elements to upload the inventory and to display a report of the execution on the hosts. On the web page
the boolean value of the switch is written to application storage.
The function Control_directory runs the Directory operation.  The present parameter is read from application storage.