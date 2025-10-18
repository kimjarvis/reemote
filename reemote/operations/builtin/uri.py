# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command
import json
import urllib.parse

class Uri:
    """
    A class to implement the Ansible `uri` module functionality using `curl` via shell commands.
    This class translates Ansible `uri` parameters into equivalent `curl` options.

    Attributes:
        url (str): The HTTP or HTTPS URL to request.
        method (str): The HTTP method (e.g., GET, POST, PUT, DELETE).
        body (any): The body of the HTTP request/response.
        body_format (str): The serialization format of the body (e.g., json, form-urlencoded).
        headers (dict): Custom HTTP headers.
        url_username (str): Username for authentication.
        url_password (str): Password for authentication.
        validate_certs (bool): Whether to validate SSL certificates.
        ca_path (str): Path to a CA certificate bundle.
        client_cert (str): Path to a client certificate file.
        client_key (str): Path to a client private key file.
        timeout (int): Socket-level timeout in seconds.
        follow_redirects (str): Redirect behavior (e.g., all, none, safe).
        force_basic_auth (bool): Force Basic authentication on the initial request.
        creates (str): A filename; if it exists, the step will not run.
        removes (str): A filename; if it does not exist, the step will not run.
        dest (str): Path to download the response body.
        return_content (bool): Whether to return the response body as content.
        status_code (list): List of valid HTTP status codes for success.

    **Examples:**

    .. code-block:: python

        # Example 1: Check that you can connect (GET) to a page and it returns a status 200
        yield Uri(
            url="http://www.example.com",
            method="GET"
        )

        # Example 2: Check that a page returns successfully but fail if the word "AWESOME" is not in the page contents
        yield Uri(
            url="http://www.example.com",
            method="GET",
            return_content=True
        )

        # Example 3: Create a JIRA issue
        yield Uri(
            url="https://your.jira.example.com/rest/api/2/issue/",
            method="POST",
            body={"key": "value"},  # Replace with actual JSON payload
            body_format="json",
            url_username="your_username",
            url_password="your_pass",
            force_basic_auth=True,
            status_code=[201]
        )

        # Example 4: Login to a form-based webpage and use the returned cookie to access the app in later tasks
        yield Uri(
            url="https://your.form.based.auth.example.com/index.php",
            method="POST",
            body={"name": "your_username", "password": "your_password", "enter": "Sign in"},
            body_format="form-urlencoded",
            status_code=[302]
        )

        # Example 5: Upload a file via multipart/form-multipart
        yield Uri(
            url="https://httpbin.org/post",
            method="POST",
            body={
                "file1": {"filename": "/bin/true", "mime_type": "application/octet-stream", "multipart_encoding": "base64"},
                "file2": {"content": "text based file content", "filename": "fake.txt", "mime_type": "text/plain", "multipart_encoding": "7or8bit"},
                "text_form_field": "value"
            },
            body_format="form-multipart"
        )

        # Example 6: Connect to a website using a previously stored cookie
        yield Uri(
            url="https://your.form.based.auth.example.com/dashboard.php",
            method="GET",
            headers={"Cookie": "session_id=abc123"}  # Replace with actual cookie string
        )

        # Example 7: Queue a build of a project in Jenkins
        yield Uri(
            url=f"http://{jenkins_host}/job/{jenkins_job}/build?token={jenkins_token}",
            method="GET",
            url_username="your_jenkins_user",
            url_password="your_jenkins_password",
            force_basic_auth=True,
            status_code=[201]
        )

        # Example 8: POST from contents of a local file
        yield Uri(
            url="https://httpbin.org/post",
            method="POST",
            src="file.json"
        )

        # Example 9: Provide SSL/TLS ciphers as a list
        yield Uri(
            url="https://example.org",
            method="GET",
            ciphers=[
                "@SECLEVEL=2",
                "ECDH+AESGCM",
                "ECDH+CHACHA20",
                "ECDH+AES",
                "DHE+AES",
                "!aNULL",
                "!eNULL",
                "!aDSS",
                "!SHA1",
                "!AESCCM"
            ]
        )

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution.
    """
    def __init__(self,
                 url: str,
                 method: str = "GET",
                 body=None,
                 body_format: str = "raw",
                 headers: dict = None,
                 url_username: str = None,
                 url_password: str = None,
                 validate_certs: bool = True,
                 ca_path: str = None,
                 client_cert: str = None,
                 client_key: str = None,
                 timeout: int = 30,
                 follow_redirects: str = "safe",
                 force_basic_auth: bool = False,
                 creates: str = None,
                 removes: str = None,
                 dest: str = None,
                 return_content: bool = False,
                 status_code: list = None):
        self.url = url
        self.method = method.upper()
        self.body = body
        self.body_format = body_format
        self.headers = headers or {}
        self.url_username = url_username
        self.url_password = url_password
        self.validate_certs = validate_certs
        self.ca_path = ca_path
        self.client_cert = client_cert
        self.client_key = client_key
        self.timeout = timeout
        self.follow_redirects = follow_redirects
        self.force_basic_auth = force_basic_auth
        self.creates = creates
        self.removes = removes
        self.dest = dest
        self.return_content = return_content
        self.status_code = status_code or [200]

    def build_curl_command(self):
        """
        Constructs the `curl` command based on the provided parameters.
        """
        curl_cmd = ["curl", "-X", self.method]

        # URL
        curl_cmd.append(self.url)

        # Authentication
        if self.url_username and self.url_password:
            curl_cmd.extend(["--user", f"{self.url_username}:{self.url_password}"])
        if self.force_basic_auth:
            curl_cmd.append("--basic")

        # SSL/TLS Options
        if not self.validate_certs:
            curl_cmd.append("--insecure")
        if self.ca_path:
            curl_cmd.extend(["--cacert", self.ca_path])
        if self.client_cert:
            curl_cmd.extend(["--cert", self.client_cert])
        if self.client_key:
            curl_cmd.extend(["--key", self.client_key])

        # Timeout
        curl_cmd.extend(["--max-time", str(self.timeout)])

        # Redirects
        if self.follow_redirects == "all":
            curl_cmd.append("-L")
        elif self.follow_redirects == "none":
            pass  # No action needed
        elif self.follow_redirects == "safe":
            curl_cmd.append("--location-trusted")

        # Headers
        for header, value in self.headers.items():
            curl_cmd.extend(["-H", f"{header}: {value}"])

        # Body
        if self.body is not None:
            if self.body_format == "json":
                curl_cmd.extend(["-H", "Content-Type: application/json"])
                curl_cmd.extend(["--data", json.dumps(self.body)])
            elif self.body_format == "form-urlencoded":
                curl_cmd.extend(["-H", "Content-Type: application/x-www-form-urlencoded"])
                curl_cmd.extend(["--data", urllib.parse.urlencode(self.body)])
            elif self.body_format == "raw":
                curl_cmd.extend(["--data", str(self.body)])
            elif self.body_format == "form-multipart":
                raise NotImplementedError("form-multipart is not supported in this implementation.")

        # Output Destination
        if self.dest:
            curl_cmd.extend(["-o", self.dest])

        # Return Content
        if self.return_content:
            curl_cmd.append("--include")  # Include headers in output

        return " ".join(curl_cmd)

    def execute(self):
        """
        Executes the constructed `curl` command using the Shell class.
        """
        from reemote.operations.server.shell import Shell
        curl_command = self.build_curl_command()
        yield Shell(cmd=curl_command, guard=True)

