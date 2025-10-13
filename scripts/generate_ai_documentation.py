import os
import re
from pathlib import Path
import argparse
import pypandoc  # Ensure pandoc is installed: https://pandoc.org/installing.html


def clean_pandoc_output(text):
    """
    Clean up common conversion artifacts from pypandoc RST to Markdown conversion.

    Args:
        text (str): The converted markdown text.

    Returns:
        str: Cleaned markdown text.
    """
    # Remove unwanted > blockquote symbols at start of lines
    text = re.sub(r'^> ', '', text, flags=re.MULTILINE)

    # Clean up attribute and method formatting
    text = re.sub(r'\n:\s*\n', '\n\n', text)  # Remove empty : lines
    text = re.sub(r'\n\s*:\s*', ' ', text)  # Join broken attribute lines

    # Fix reference formatting
    text = re.sub(r'\[([^\]]+)\]{\.title-ref}', r'`\1`', text)

    # Clean up extra whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)  # Replace 3+ newlines with 2
    text = text.strip()

    return text


def convert_docstring_to_markdown(docstring):
    """
    Convert docstring to markdown format with careful handling of RST syntax.

    Args:
        docstring (str): The original docstring content.

    Returns:
        str: Markdown formatted docstring.
    """
    # First, try manual conversion for common RST patterns
    markdown = docstring

    # Convert RST attributes/methods to markdown
    markdown = re.sub(r'^(\w+):\s*$', r'**\1:**', markdown, flags=re.MULTILINE)

    # Convert RST code references to markdown
    markdown = re.sub(r'``([^`]+)``', r'`\1`', markdown)

    # Ensure proper line breaks
    markdown = markdown.replace('\n', '  \n')  # Two spaces for markdown line breaks

    # Try pypandoc conversion as fallback for complex RST
    try:
        pandoc_output = pypandoc.convert_text(docstring, 'markdown', format='rst')
        # Only use pandoc output if it's better than our manual conversion
        if len(pandoc_output.strip()) > len(markdown.strip()):
            cleaned_pandoc = clean_pandoc_output(pandoc_output)
            if cleaned_pandoc:
                markdown = cleaned_pandoc
    except (ImportError, Exception):
        # If pypandoc fails, use our manual conversion
        pass

    return markdown.strip()


def extract_docstring(file_path):
    """
    Extracts the docstring from a Python file and converts it to Markdown format.

    Args:
        file_path (str): Path to the Python file.

    Returns:
        str: Markdown-formatted docstring, or an empty string if no docstring is found.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            content = file.read()

        # Match the first docstring in the file
        docstring_match = re.search(r'^\s*"""(.*?)"""', content, re.DOTALL | re.MULTILINE)
        if not docstring_match:
            return ""

        docstring = docstring_match.group(1).strip()

        # Use our improved conversion function
        markdown_docstring = convert_docstring_to_markdown(docstring)
        return markdown_docstring

    except Exception as e:
        print(f"Error extracting docstring from {file_path}: {e}")
        return ""


def get_file_content(file_path):
    """
    Reads and returns the complete content of a file.

    Args:
        file_path (str): Path to the file.

    Returns:
        str: Complete file content.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return file.read()
    except Exception as e:
        print(f"Error reading file {file_path}: {e}")
        return ""


def should_ignore_file(file_path):
    """
    Check if a file should be ignored based on common patterns.

    Args:
        file_path (str): Path to the file.

    Returns:
        bool: True if the file should be ignored.
    """
    ignore_patterns = [
        '__pycache__',
        '.git',
        '.pytest_cache',
        'venv',
        'env',
        '.env',
        'node_modules',
        '.vscode',
        '.idea',
        'build',
        'dist'
    ]

    return any(pattern in file_path for pattern in ignore_patterns)


def write_to_markdown(file_path, output_file, content, docstring=None):
    """
    Writes the file content to the markdown file with appropriate formatting for Qwen AI.

    Args:
        file_path (str): Path to the source file.
        output_file (file object): Opened markdown file for writing.
        content (str): Complete file content to write (including docstrings).
        docstring (str, optional): Extracted and formatted docstring to include before the code block. Defaults to None.
    """
    try:
        relative_path = os.path.relpath(file_path, start=Path.cwd())

        # Write file header
        output_file.write(f"## File: `{relative_path}`\n\n")

        # Write extracted docstring as overview (if exists)
        if docstring:
            output_file.write("### Overview\n")
            output_file.write(f"{docstring}\n\n")

        # Write the complete source code
        output_file.write("### Source Code\n")
        extension = os.path.splitext(file_path)[1][1:]

        # Map extensions to language identifiers for syntax highlighting
        lang_map = {
            'py': 'python',
            'yaml': 'yaml',
            'yml': 'yaml',
            'md': 'markdown',
            'txt': 'text',
            'json': 'json',
            'js': 'javascript',
            'html': 'html',
            'css': 'css',
            'sql': 'sql'
        }

        language = lang_map.get(extension, extension)
        output_file.write(f"```{language}\n")
        output_file.write(content)
        if not content.endswith('\n'):
            output_file.write('\n')
        output_file.write("```\n\n")

        # Add separator between files
        output_file.write("---\n\n")

    except Exception as e:
        print(f"Error writing to markdown for {file_path}: {e}")


def process_file(file_path, output_file):
    """
    Process a single file and write its content to the markdown output.

    Args:
        file_path (str): Path to the file to process.
        output_file (file object): Opened markdown file for writing.
    """
    if should_ignore_file(file_path):
        return

    extension = os.path.splitext(file_path)[1].lower()

    try:
        # Process Python files - extract docstring but keep full content
        if extension == ".py":
            docstring = extract_docstring(file_path)
            content = get_file_content(file_path)
            write_to_markdown(file_path, output_file, content, docstring)

        # Process other supported file types
        elif extension in [".yaml", ".yml", ".md", ".txt", ".json", ".js", ".html", ".css"]:
            content = get_file_content(file_path)
            write_to_markdown(file_path, output_file, content)

    except UnicodeDecodeError:
        print(f"Skipping binary file: {file_path}")
    except Exception as e:
        print(f"Error processing file {file_path}: {e}")


def process_directory(directory, output_file):
    """
    Recursively processes the directory, extracting and writing file contents.

    Args:
        directory (str): Path to the directory to process.
        output_file (file object): Opened markdown file for writing.
    """
    if not os.path.exists(directory):
        raise FileNotFoundError(f"Directory '{directory}' does not exist.")

    for root, dirs, files in os.walk(directory):
        # Skip ignored directories in-place
        dirs[:] = [d for d in dirs if not should_ignore_file(os.path.join(root, d))]

        # Sort files for consistent output
        files.sort()

        for file in files:
            file_path = os.path.join(root, file)
            process_file(file_path, output_file)


def main():
    # Set up argument parsing
    parser = argparse.ArgumentParser(
        description="Generate documentation in Markdown format optimized for Qwen AI."
    )
    parser.add_argument(
        "-d", "--directory",
        required=True,
        help="Directory to parse recursively."
    )
    parser.add_argument(
        "-o", "--output",
        default="code_documentation.md",
        help="Output markdown file name (default: code_documentation.md)."
    )
    parser.add_argument(
        "--no-pandoc",
        action="store_true",
        help="Disable pypandoc conversion and use manual conversion only."
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output."
    )

    args = parser.parse_args()

    # Process the specified directory
    try:
        with open(args.output, "w", encoding="utf-8") as md_file:
            # Write document header optimized for Qwen AI
            md_file.write("# Code Documentation\n\n")
            md_file.write("This document contains the complete source code from the project. ")
            md_file.write("Each file is presented with its full content including docstrings and comments. ")
            md_file.write(
                "The documentation is structured to help AI assistants understand the codebase and provide meaningful suggestions.\n\n")

            md_file.write(f"**Source Directory:** `{args.directory}`\n\n")
            md_file.write("## Files\n\n")

            # Process files
            process_directory(args.directory, md_file)

            # Write footer
            md_file.write("## End of Documentation\n")
            md_file.write("*This document was automatically generated.*\n")

        if args.verbose:
            print(f"✅ Successfully processed directory '{args.directory}'")
            print(f"📄 Documentation written to '{args.output}'")
            print("🤖 Format optimized for Qwen AI comprehension")

    except Exception as e:
        print(f"❌ Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())