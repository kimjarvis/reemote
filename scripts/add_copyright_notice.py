# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import os
import sys


def add_copyright_to_python_files(directory, copyright_statement):
    """
    Recursively adds a copyright statement to all Python (.py) files in the given directory,
    except for __init__.py files.

    :param directory: The root directory to start searching for Python files.
    :param copyright_statement: The copyright statement to add to each Python file.
    """
    # Ensure the copyright statement ends with a newline for proper formatting
    if not copyright_statement.endswith("\n"):
        copyright_statement += "\n"

    # Walk through the directory recursively
    for root, _, filenames in os.walk(directory):
        for filename in filenames:
            # Check if the file is a Python file and not __init__.py
            if filename.endswith(".py") and filename != "__init__.py":
                file_path = os.path.join(root, filename)

                # Read the current content of the file
                try:
                    with open(file_path, "r", encoding="utf-8") as file:
                        content = file.read()
                except Exception as e:
                    print(f"Error reading {file_path}: {e}")
                    continue

                # Check if the copyright statement is already present
                if not content.startswith(copyright_statement):
                    # Prepend the copyright statement to the content
                    updated_content = copyright_statement + content

                    # Write the updated content back to the file
                    try:
                        with open(file_path, "w", encoding="utf-8") as file:
                            file.write(updated_content)
                        print(f"Updated: {file_path}")
                    except Exception as e:
                        print(f"Error writing to {file_path}: {e}")
                else:
                    print(f"Already contains copyright: {file_path}")


# Main execution block
if __name__ == "__main__":
    # Check if the correct number of arguments is provided
    if len(sys.argv) != 2:
        print("Usage: python add_copyright.py <directory>")
        sys.exit(1)

    # Get the target directory from the command-line argument
    target_directory = sys.argv[1]

    # Validate that the directory exists
    if not os.path.isdir(target_directory):
        print(f"Error: '{target_directory}' is not a valid directory.")
        sys.exit(1)

    # Define the copyright statement
    copyright_text = """# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
"""

    # Call the function to add the copyright statement
    add_copyright_to_python_files(target_directory, copyright_text)