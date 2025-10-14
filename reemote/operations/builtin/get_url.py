# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class GetUrl:
    """
    A class to encapsulate the functionality of downloading files from HTTP, HTTPS, or FTP URLs.
    This mimics the behavior of Ansible's `ansible.builtin.get_url` module.

    Attributes:
        url (str): HTTP, HTTPS, or FTP URL in the form (http|https|ftp://[user[:pass]]@host.domain[:port]/path).
        dest (str): Absolute path of where to download the file to.
        backup (bool): Create a backup file including the timestamp information.
        checksum (str): If a checksum is passed, the digest of the destination file will be calculated.
        ciphers (list): SSL/TLS Ciphers to use for the request.
        client_cert (str): PEM formatted certificate chain file for SSL client authentication.
        client_key (str): PEM formatted file that contains your private key for SSL client authentication.
        decompress (bool): Whether to attempt to decompress gzip content-encoded responses.
        force (bool): If true, will download the file every time and replace if contents change.
        force_basic_auth (bool): Force the sending of the Basic authentication header.
        group (str): Name of the group that should own the filesystem object.
        headers (dict): Add custom HTTP headers to a request.
        http_agent (str): Header to identify as, generally appears in web server logs.
        mode (str): The permissions the resulting filesystem object should have.
        owner (str): Name of the user that should own the filesystem object.
        selevel (str): The level part of the SELinux filesystem object context.
        serole (str): The role part of the SELinux filesystem object context.
        setype (str): The type part of the SELinux filesystem object context.
        seuser (str): The user part of the SELinux filesystem object context.
        timeout (int): Timeout in seconds for URL request.
        tmp_dest (str): Absolute path of where temporary file is downloaded to.
        unredirected_headers (list): Headers that will not be sent on subsequent redirected requests.
        unsafe_writes (bool): Influence when to use atomic operation to prevent data corruption.
        url_password (str): The password for use in HTTP basic authentication.
        url_username (str): The username for use in HTTP basic authentication.
        use_gssapi (bool): Use GSSAPI to perform the authentication.
        use_netrc (bool): Determining whether to use credentials from ~/.netrc file.
        use_proxy (bool): If false, it will not use a proxy.
        validate_certs (bool): If false, SSL certificates will not be validated.
        attributes (str): The attributes the resulting filesystem object should have.
        guard (bool): If `False` the commands will not be executed.

    **Examples:**

    .. code:: python

        # Download a file
        r = yield GetUrl(url="http://example.com/path/file.conf", dest="/etc/foo.conf")
        print(r.cp.stdout)

        # Download file with custom headers
        r = yield GetUrl(
            url="http://example.com/path/file.conf",
            dest="/etc/foo.conf",
            headers={"key1": "one", "key2": "two"}
        )

        # Download file with checksum verification
        r = yield GetUrl(
            url="http://example.com/path/file.conf",
            dest="/etc/foo.conf",
            checksum="sha256:b5bb9d8014a0f9b1d61e21e796d78dccdf1352f23cd32812f4850b878ae4944c"
        )

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - This implementation uses curl under the hood to perform the download.
        - Commands are constructed based on the provided parameters.
    """

    def __init__(self,
                 url: str,
                 dest: str,
                 backup: bool = False,
                 checksum: str = "",
                 ciphers: list = None,
                 client_cert: str = None,
                 client_key: str = None,
                 decompress: bool = True,
                 force: bool = False,
                 force_basic_auth: bool = False,
                 group: str = None,
                 headers: dict = None,
                 http_agent: str = "ansible-httpget",
                 mode: str = None,
                 owner: str = None,
                 selevel: str = None,
                 serole: str = None,
                 setype: str = None,
                 seuser: str = None,
                 timeout: int = 10,
                 tmp_dest: str = None,
                 unredirected_headers: list = None,
                 unsafe_writes: bool = False,
                 url_password: str = None,
                 url_username: str = None,
                 use_gssapi: bool = False,
                 use_netrc: bool = True,
                 use_proxy: bool = True,
                 validate_certs: bool = True,
                 attributes: str = None,
                 guard: bool = True):

        self.url = url
        self.dest = dest
        self.backup = backup
        self.checksum = checksum
        self.ciphers = ciphers or []
        self.client_cert = client_cert
        self.client_key = client_key
        self.decompress = decompress
        self.force = force
        self.force_basic_auth = force_basic_auth
        self.group = group
        self.headers = headers or {}
        self.http_agent = http_agent
        self.mode = mode
        self.owner = owner
        self.selevel = selevel
        self.serole = serole
        self.setype = setype
        self.seuser = seuser
        self.timeout = timeout
        self.tmp_dest = tmp_dest
        self.unredirected_headers = unredirected_headers or []
        self.unsafe_writes = unsafe_writes
        self.url_password = url_password
        self.url_username = url_username
        self.use_gssapi = use_gssapi
        self.use_netrc = use_netrc
        self.use_proxy = use_proxy
        self.validate_certs = validate_certs
        self.attributes = attributes
        self.guard = guard

    def __repr__(self):
        return (f"GetUrl(url={self.url!r}, dest={self.dest!r}, "
                f"backup={self.backup!r}, checksum={self.checksum!r}, "
                f"ciphers={self.ciphers!r}, client_cert={self.client_cert!r}, "
                f"client_key={self.client_key!r}, decompress={self.decompress!r}, "
                f"force={self.force!r}, force_basic_auth={self.force_basic_auth!r}, "
                f"group={self.group!r}, headers={self.headers!r}, "
                f"http_agent={self.http_agent!r}, mode={self.mode!r}, "
                f"owner={self.owner!r}, selevel={self.selevel!r}, "
                f"serole={self.serole!r}, setype={self.setype!r}, "
                f"seuser={self.seuser!r}, timeout={self.timeout!r}, "
                f"tmp_dest={self.tmp_dest!r}, "
                f"unredirected_headers={self.unredirected_headers!r}, "
                f"unsafe_writes={self.unsafe_writes!r}, "
                f"url_password={self.url_password!r}, "
                f"url_username={self.url_username!r}, "
                f"use_gssapi={self.use_gssapi!r}, use_netrc={self.use_netrc!r}, "
                f"use_proxy={self.use_proxy!r}, "
                f"validate_certs={self.validate_certs!r}, "
                f"attributes={self.attributes!r}, guard={self.guard!r})")

    def execute(self):
        # Build curl command with appropriate options
        cmd_parts = ["curl", "-s", "-L", f"--connect-timeout {self.timeout}"]

        # Handle authentication
        if self.url_username and self.url_password:
            cmd_parts.append(f"--user {self.url_username}:{self.url_password}")
        elif self.force_basic_auth and self.url_username:
            cmd_parts.append(f"--user {self.url_username}:")

        # Handle headers
        if self.headers:
            for key, value in self.headers.items():
                cmd_parts.append(f"--header \"{key}: {value}\"")

        # Handle HTTP agent
        cmd_parts.append(f"--user-agent \"{self.http_agent}\"")

        # Handle proxy settings
        if not self.use_proxy:
            cmd_parts.append("--noproxy '*'")

        # Handle SSL validation
        if not self.validate_certs:
            cmd_parts.append("--insecure")

        # Handle compression
        if not self.decompress:
            cmd_parts.append("--compressed")

        # Handle client certificate
        if self.client_cert:
            cmd_parts.append(f"--cert {self.client_cert}")
        if self.client_key:
            cmd_parts.append(f"--key {self.client_key}")

        # Handle ciphers
        if self.ciphers:
            cipher_string = ":".join(self.ciphers)
            cmd_parts.append(f"--ciphers {cipher_string}")

        # Add URL and output destination
        cmd_parts.append(f"--output {self.dest}")
        cmd_parts.append(f"\"{self.url}\"")

        # Construct final command
        cmd = " ".join(cmd_parts)

        # Execute the command
        r = yield Command(cmd, guard=self.guard)
        r.changed = True