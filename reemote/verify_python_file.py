from pathlib import Path


def verify_python_file(file_path):
    """Verify that the file has .py extension and exists"""
    path = Path(file_path)
    if not path.exists():
        print(f"Error: File '{file_path}' does not exist")
        return False
    if path.suffix != '.py':
        print(f"Error: File '{file_path}' must have .py extension")
        return False
    return True
