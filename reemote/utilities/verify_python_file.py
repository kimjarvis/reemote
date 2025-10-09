from pathlib import Path


def verify_python_file(file_path):
    """Verifies that a given path corresponds to an existing Python file.

    This function checks if the provided file path is valid by performing
    two key validations. First, it confirms that a file actually exists
    at the given path. Second, it checks that the file has a `.py`
    extension.

    If the file does not exist or does not have the correct extension, an
    error message is printed to the console.

    Args:
        file_path (str): The path to the file to be verified.

    Returns:
        bool: ``True`` if the file exists and has a ``.py`` extension,
              ``False`` otherwise.
    """
    """Verify that the file has .py extension and exists"""
    path = Path(file_path)
    if not path.exists():
        print(f"Error: File '{file_path}' does not exist")
        return False
    if path.suffix != '.py':
        print(f"Error: File '{file_path}' must have .py extension")
        return False
    return True
