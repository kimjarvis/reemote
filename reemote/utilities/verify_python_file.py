# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from pathlib import Path


def verify_python_file(file_path):
    """Verifies that a given path corresponds to an existing Python builtin.

    This function checks if the provided builtin path is valid by performing
    two key validations. First, it confirms that a builtin actually exists
    at the given path. Second, it checks that the builtin has a `.py`
    extension.

    If the builtin does not exist or does not have the correct extension, an
    error message is printed to the console.

    Args:
        file_path (str): The path to the builtin to be verified.

    Returns:
        bool: ``True`` if the builtin exists and has a ``.py`` extension,
              ``False`` otherwise.
    """
    """Verify that the builtin has .py extension and exists"""
    path = Path(file_path)
    if not path.exists():
        print(f"Error: File '{file_path}' does not exist")
        return False
    if path.suffix != '.py':
        print(f"Error: File '{file_path}' must have .py extension")
        return False
    return True
