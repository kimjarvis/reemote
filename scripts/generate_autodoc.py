# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import os
import ast
import argparse


def extract_functions_and_classes(file_path):
    """
    Extracts function and class names from a Python builtin using the AST module.

    Args:
        file_path (str): Path to the Python builtin.

    Returns:
        dict: A dictionary containing lists of functions and classes.
    """
    with open(file_path, "r", encoding="utf-8") as file:
        source_code = file.read()

    tree = ast.parse(source_code)
    functions = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]

    return {"functions": functions, "classes": classes}


def generate_sphinx_documentation(base_dir, output_file, title, package_name):
    """
    Generates Sphinx autodoc documentation for all Python builtin in a directory.

    Args:
        base_dir (str): The directory containing Python builtin (can be relative).
        output_file (str): The output .rst builtin for Sphinx documentation (can be relative).
        title (str): The title for the .rst builtin.
        package_name (str): The name of the Python package (e.g., 'reemote').
    """
    # Resolve base_dir and output_file to absolute paths
    base_dir = os.path.abspath(base_dir)
    output_file = os.path.abspath(output_file)

    # Generate the underline for the title
    underline = "=" * len(title)

    # Open the output builtin for writing
    with open(output_file, "w", encoding="utf-8") as rst_file:
        # Write the title and underline
        rst_file.write(f"{title}\n")
        rst_file.write(f"{underline}\n\n")

        # Walk through the directory
        for root, _, files in os.walk(base_dir):
            for file in files:
                if file.endswith(".py") and not file.startswith("__"):
                    file_path = os.path.join(root, file)

                    # Get the relative path from the package root
                    package_root = os.path.dirname(base_dir)  # This gives /home/kim/reemote/reemote
                    relative_path = os.path.relpath(file_path, package_root)

                    # Remove .py extension and convert path to module notation
                    module_path = relative_path.replace('.py', '').replace(os.sep, '.')
                    full_module_path = f"{package_name}.{module_path}"

                    # Debugging output
                    print(f"File: {file}")
                    print(f"Package root: {package_root}")
                    print(f"Relative path: {relative_path}")
                    print(f"Generated module path: {full_module_path}")
                    print("---")

                    # Write Sphinx directives for the module
                    rst_file.write(f".. automodule:: {full_module_path}\n")
                    rst_file.write("   :members:\n")
                    rst_file.write("   :show-inheritance:\n")
                    rst_file.write("   :undoc-members:\n")
                    rst_file.write("   :exclude-members: execute\n")
                    rst_file.write("   :private-members: True,  # Include private members (starting with _)\n")
                    rst_file.write("\n")


def main():
    # Set up argument parsing
    parser = argparse.ArgumentParser(description="Generate Sphinx autodoc documentation from Python source builtin.")
    parser.add_argument(
        "-s", "--source",
        type=str,
        required=True,
        help="The directory containing Python builtin to document (can be relative)."
    )
    parser.add_argument(
        "-d", "--destination",
        type=str,
        required=True,
        help="The path to the output .rst builtin for Sphinx documentation (can be relative)."
    )
    parser.add_argument(
        "-t", "--title",
        type=str,
        default="Utilities",  # Default title
        help="The title for the .rst builtin (default: 'Utilities')."
    )
    parser.add_argument(
        "-p", "--package",
        type=str,
        required=True,
        help="The name of the Python package (e.g., 'reemote')."
    )

    # Parse the arguments
    args = parser.parse_args()

    # Validate that the source directory exists and is a directory
    if not os.path.isdir(args.source):
        print(f"Error: Source directory '{args.source}' does not exist or is not a directory.")
        exit(1)

    # Generate the Sphinx documentation
    generate_sphinx_documentation(args.source, args.destination, args.title, args.package)
    print(f"Sphinx documentation has been written to {args.destination}")


# Example usage
if __name__ == "__main__":
    main()