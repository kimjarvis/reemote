# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import os
import tempfile
import hashlib
from pathlib import Path
import jinja2


from reemote.operations.sftp.read_file import Read_file
from reemote.operations.sftp.write_file import Write_file
from reemote.operations.sftp.setstat import Setstat


class Template():
    """
    A class to manage builtin templating on remote servers using SFTP operations.

    This class allows you to render Jinja2 templates and transfer them to remote servers.
    It supports template rendering with variables, backup creation, validation commands,
    and setting builtin attributes after transfer.

    Attributes:
        src (str): Path to the Jinja2 template builtin on the local system.
        dest (str): Path where the rendered template should be placed on the remote server.
        vars (dict, optional): Variables to use when rendering the template. Defaults to ``None``.
        backup (bool, optional): Whether to create a backup of the existing builtin. Defaults to ``False``.
        force (bool, optional): Whether to replace the builtin if content differs. Defaults to ``False``.
        lstrip_blocks (bool, optional): Strip leading spaces and tabs from blocks. Defaults to ``False``.
        trim_blocks (bool, optional): Remove first newline after a block. Defaults to ``False``.
        newline_sequence (str, optional): Newline sequence to use ('\\n', '\\r', '\\r\\n'). Defaults to ``None``.
        output_encoding (str, optional): Encoding for the output builtin. Defaults to ``None``.
        block_start_string (str, optional): String marking block start. Defaults to ``None``.
        block_end_string (str, optional): String marking block end. Defaults to ``None``.
        variable_start_string (str, optional): String marking variable start. Defaults to ``None``.
        variable_end_string (str, optional): String marking variable end. Defaults to ``None``.
        comment_start_string (str, optional): String marking comment start. Defaults to ``None``.
        comment_end_string (str, optional): String marking comment end. Defaults to ``None``.
        validate (str, optional): Validation command to run before final copy. Defaults to ``None``.
        attrs (dict, optional): File attributes to set after writing the builtin. Defaults to ``None``.

    **Examples:**

    .. code:: python

        # Create temporary build directory
        yield Tempfile(
            state="directory",
            suffix="build",
        )

        # Create temporary file
        yield Tempfile(
            state="file",
            suffix="temp",
        )
        # register: tempfile_1

        # Create a temporary file with a specific prefix
        yield Tempfile(
            state="file",
            suffix="txt",
            prefix="myfile_",
        )

        # Use the registered var and the file module to remove the temporary file
        yield File(
            path="{{ tempfile_1.path }}",
            state="absent",
        )
        # when: tempfile_1.path is defined

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution.
    """

    def __init__(self,
                 src="",
                 dest="",
                 vars=None,
                 backup=False,
                 force=True,
                 lstrip_blocks=False,
                 trim_blocks=True,
                 newline_sequence='\n',
                 output_encoding='utf-8',
                 block_start_string='{%',
                 block_end_string='%}',
                 variable_start_string='{{',
                 variable_end_string='}}',
                 comment_start_string='{#',
                 comment_end_string='#}',
                 validate=None,
                 attrs=None):

        self.src = src
        self.dest = dest
        self.vars = vars or {}
        self.backup = backup
        self.force = force
        self.lstrip_blocks = lstrip_blocks
        self.trim_blocks = trim_blocks
        self.newline_sequence = newline_sequence
        self.output_encoding = output_encoding
        self.block_start_string = block_start_string
        self.block_end_string = block_end_string
        self.variable_start_string = variable_start_string
        self.variable_end_string = variable_end_string
        self.comment_start_string = comment_start_string
        self.comment_end_string = comment_end_string
        self.validate = validate
        self.attrs = attrs

    def _calculate_checksum(self, content):
        """Calculate MD5 checksum of content."""
        if isinstance(content, dict):
            content = str(content)  # Convert dict to string for checksum
        return hashlib.md5(content.encode('utf-8')).hexdigest()

    def _read_template_file(self):
        """Read the template builtin with UTF-8 encoding."""
        try:
            with open(self.src, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            raise Exception(f"Failed to read template builtin '{self.src}': {str(e)}")

    def _render_template(self, template_content):
        """Render the Jinja2 template with the provided variables."""
        try:
            # Configure Jinja2 environment
            env = jinja2.Environment(
                loader=jinja2.BaseLoader(),
                trim_blocks=self.trim_blocks,
                lstrip_blocks=self.lstrip_blocks,
                block_start_string=self.block_start_string,
                block_end_string=self.block_end_string,
                variable_start_string=self.variable_start_string,
                variable_end_string=self.variable_end_string,
                comment_start_string=self.comment_start_string,
                comment_end_string=self.comment_end_string
            )

            template = env.from_string(template_content)
            rendered_content = template.render(**self.vars)

            # Handle newline sequence conversion
            if self.newline_sequence != '\n':
                rendered_content = rendered_content.replace('\n', self.newline_sequence)

            return rendered_content

        except jinja2.TemplateError as e:
            raise Exception(f"Template rendering failed: {str(e)}")
        except Exception as e:
            raise Exception(f"Unexpected error during template rendering: {str(e)}")

    def _ensure_string_content(self, content):
        """Ensure content is a string, converting if necessary."""
        if isinstance(content, dict):
            # Convert dictionary to JSON string
            import json
            return json.dumps(content, indent=2)
        elif isinstance(content, list):
            # Convert list to string representation
            return str(content)
        elif not isinstance(content, str):
            # Convert any other type to string
            return str(content)
        return content

    def execute(self):
        """
        :no-index:
        """
        try:
            # Step 1: Read and render the template
            template_content = self._read_template_file()
            rendered_content = self._render_template(template_content)

            # Ensure content is a string
            rendered_content = self._ensure_string_content(rendered_content)
            new_checksum = self._calculate_checksum(rendered_content)

            # Step 2: Check if remote builtin exists and compare content
            remote_exists = False
            remote_checksum = None
            remote_content = None

            try:
                r = yield Read_file(path=self.dest)
                remote_content = r.cp.stdout
                if remote_content:
                    remote_exists = True
                    if isinstance(remote_content, bytes):
                        remote_content = remote_content.decode('utf-8')
                    remote_content = self._ensure_string_content(remote_content)
                    remote_checksum = self._calculate_checksum(remote_content)
            except Exception:
                # File doesn't exist or can't be read
                remote_exists = False

            # Step 3: Check if changes are needed
            content_identical = remote_checksum == new_checksum

            if not self.force and remote_exists:
                print(f"File exists and force=False, skipping: {self.dest}")
                if self.attrs:
                    yield Setstat(path=self.dest, attrs=self.attrs)
                return

            if content_identical and remote_exists:
                print(f"Content unchanged, skipping: {self.dest}")
                if self.attrs:
                    yield Setstat(path=self.dest, attrs=self.attrs)
                return

            # Step 4: Create backup if requested and builtin exists
            if self.backup and remote_exists and remote_content:
                print(f"Creating backup at {self.dest}.backup")
                yield Write_file(path=f"{self.dest}.backup", text=remote_content)
                if self.attrs:
                    yield Setstat(path=f"{self.dest}.backup", attrs=self.attrs)

            # Step 5: Validate content if validation command provided
            if self.validate:
                print(f"Validating template with: {self.validate}")
                validation_passed = yield from self._validate_content(rendered_content)
                if not validation_passed:
                    raise Exception(f"Validation command failed: {self.validate}")

            # Step 6: Write the rendered content to remote builtin
            print(f"Writing template to: {self.dest}")
            yield Write_file(path=self.dest, text=rendered_content)

            # Step 7: Set builtin attributes if provided
            if self.attrs:
                print(f"Setting attributes for {self.dest}: {self.attrs}")
                yield Setstat(path=self.dest, attrs=self.attrs)

            print(f"Template successfully applied to: {self.dest}")

        except Exception as e:
            raise Exception(f"Template operation failed: {str(e)}")

    def _validate_content(self, content):
        """Validate the rendered content using the validation command."""
        if not self.validate:
            return True

        try:
            # Ensure content is string for validation
            content = self._ensure_string_content(content)

            # Create temporary builtin with rendered content
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False) as temp_file:
                temp_file.write(content)
                temp_path = temp_file.name

            # Replace %s in validate command with temp builtin path
            validate_cmd = self.validate.replace('%s', temp_path)

            # Execute validation command
            result = yield Command(command=validate_cmd)

            # Clean up temp builtin
            os.unlink(temp_path)

            return result.return_code == 0

        except Exception as e:
            raise Exception(f"Validation failed: {str(e)}")