Deployment
==========

Deployment files
----------------

Deployments are executable reemote classes.

This deployment prints ``Hello World`` on all your servers.

.. code-block::

    class Shell_example:
        def execute(self):
            from reemote.operations.server.shell import Shell
            # Execute a shell command on all hosts
            r = yield Shell("echo Hello World")
            # The result is available in stdout
            print(r.cp.stdout)

You can run a deployment using either the GUI or the CLI.

This deployment installs vim on Alpine Linux, using the apk package manager.

.. code-block::

    class Install_vim:
        def execute(self):
            from reemote.operations.apk.packages import Packages
            from reemote.operations.server.shell import Shell
            # Add the packages on all hosts
            r = yield Packages(packages=["vim""],present=True, su=True)
            # Verify installation
            r = yield Shell("which vim")
            print(r.cp.stdout)
