class Lineinfile():
    """
    A class to manage lines in a remote file using SFTP operations.

    This class ensures that a specific line exists in a file on a remote server. It supports adding,
    replacing, or modifying lines based on search criteria such as regular expressions, string matches,
    or exact line comparisons. Additionally, it can set file attributes (e.g., permissions) after making changes.

    :param str line: The line to ensure exists in the file. This line will be added, replaced,
                     or left unchanged depending on the search criteria.
    :param str path: The path to the file on the remote server.
    :param str regexp: A regular expression pattern to match lines in the file. Mutually exclusive
                       with ``search_string``. Defaults to ``None``.
    :param str search_string: A string to search for in the file. Mutually exclusive with ``regexp``.
                              Defaults to ``None``.
    :param str insertafter: A pattern or keyword ('EOF') specifying where to insert the line after a match.
                            Mutually exclusive with ``insertbefore``. Defaults to ``None``.
    :param str insertbefore: A pattern or keyword ('BOF') specifying where to insert the line before a match.
                             Mutually exclusive with ``insertafter``. Defaults to ``None``.
    :param dict attrs: File attributes (e.g., permissions) to set after modifying the file.
                       Defaults to ``None``.

    :raises ValueError: If mutually exclusive parameters (``regexp`` and ``search_string``, or
                        ``insertafter`` and ``insertbefore``) are both provided.

    **Examples:**

    Ensure SELinux is set to enforcing mode:

    .. code-block:: python

        yield Lineinfile(
            path="/etc/selinux/config",
            regexp="^SELINUX=",
            line="SELINUX=enforcing",
        )

    Make sure group wheel is not in the sudoers configuration:

    .. code-block:: python

        yield Lineinfile(
            path="/etc/sudoers",
            state="absent",
            regexp="^%wheel",
        )

    Replace a localhost entry with our own:

    .. code-block:: python

        yield Lineinfile(
            path="/etc/hosts",
            regexp="^127\\.0\\.0\\.1",
            line="127.0.0.1 localhost",
            owner="root",
            group="root",
            mode="0644",
        )

    Replace a localhost entry searching for a literal string to avoid escaping:

    .. code-block:: python

        yield Lineinfile(
            path="/etc/hosts",
            search_string="127.0.0.1",
            line="127.0.0.1 localhost",
            owner="root",
            group="root",
            mode="0644",
        )

    Ensure the default Apache port is 8080:

    .. code-block:: python

        yield Lineinfile(
            path="/etc/httpd/conf/httpd.conf",
            regexp="^Listen ",
            line="Listen 8080",
        )

    Add a line to a file if the file does not exist, without passing regexp:

    .. code-block:: python

        yield Lineinfile(
            path="/tmp/testfile",
            line="192.168.1.99 foo.lab.net foo",
            create=True,
        )

    .. note::
        This class is designed to be used in a generator-based workflow where commands are yielded
        for execution. File attributes (if provided) are applied after modifying the file.
    """