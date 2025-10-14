# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import os
import re
from pathlib import Path
import argparse
import json


def extract_docstring(file_path):
    """
    Extracts the docstring from a Python file without any conversion.

    Args:
        file_path (str): Path to the Python file.

    Returns:
        str: Raw docstring content as it appears in the file.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            content = file.read()

        # Match the first docstring in the file
        docstring_match = re.search(r'^\s*"""(.*?)"""', content, re.DOTALL | re.MULTILINE)
        if not docstring_match:
            return ""

        docstring = docstring_match.group(1).strip()
        return docstring

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


def create_file_object(file_path, content, docstring=None):
    """
    Creates a JSON object for a file with the required fields.

    Args:
        file_path (str): Path to the source file.
        content (str): Complete file content.
        docstring (str, optional): Extracted docstring. Defaults to None.

    Returns:
        dict: JSON object with path, doc, and source fields.
    """
    try:
        # Use absolute path for the path field
        absolute_path = os.path.abspath(file_path)

        file_obj = {
            "path": absolute_path,
            "doc": docstring or "",
            "source": content
        }

        return file_obj

    except Exception as e:
        print(f"Error creating JSON object for {file_path}: {e}")
        return None


def process_file(file_path):
    """
    Process a single file and return its JSON object.

    Args:
        file_path (str): Path to the file to process.

    Returns:
        dict: JSON object for the file, or None if file should be ignored.
    """
    if should_ignore_file(file_path):
        return None

    extension = os.path.splitext(file_path)[1].lower()

    try:
        # Process Python files - extract docstring but keep full content
        if extension == ".py":
            docstring = extract_docstring(file_path)
            content = get_file_content(file_path)
            return create_file_object(file_path, content, docstring)

        # Process other supported file types
        elif extension in [".yaml", ".yml", ".md", ".txt", ".json", ".js", ".html", ".css"]:
            content = get_file_content(file_path)
            return create_file_object(file_path, content)

    except UnicodeDecodeError:
        print(f"Skipping binary file: {file_path}")
        return None
    except Exception as e:
        print(f"Error processing file {file_path}: {e}")
        return None


def process_directory(directory):
    """
    Recursively processes the directory, extracting file contents and creating JSON objects.

    Args:
        directory (str): Path to the directory to process.

    Returns:
        list: List of JSON objects for each file.
    """
    if not os.path.exists(directory):
        raise FileNotFoundError(f"Directory '{directory}' does not exist.")

    file_objects = []

    for root, dirs, files in os.walk(directory):
        # Skip ignored directories in-place
        dirs[:] = [d for d in dirs if not should_ignore_file(os.path.join(root, d))]

        # Sort files for consistent output
        files.sort()

        for file in files:
            file_path = os.path.join(root, file)
            file_obj = process_file(file_path)
            if file_obj:
                file_objects.append(file_obj)

    return file_objects


def main():
    # Set up argument parsing
    parser = argparse.ArgumentParser(
        description="Generate code documentation in JSON format."
    )
    parser.add_argument(
        "-d", "--directory",
        required=True,
        help="Directory to parse recursively."
    )
    parser.add_argument(
        "-o", "--output",
        default="code.json",
        help="Output JSON file name (default: code.json)."
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output."
    )

    args = parser.parse_args()

    # Process the specified directory
    try:
        # Process files and get JSON objects
        file_objects = process_directory(args.directory)

        # Write to JSON file
        with open(args.output, "w", encoding="utf-8") as json_file:
            json.dump(file_objects, json_file, indent=2, ensure_ascii=False)

        if args.verbose:
            print(f"✅ Successfully processed directory '{args.directory}'")
            print(f"📄 JSON documentation written to '{args.output}'")
            print(f"📊 Processed {len(file_objects)} files")

    except Exception as e:
        print(f"❌ Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())