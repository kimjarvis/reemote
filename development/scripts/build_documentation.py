import os
import subprocess
import sys

def run_command(command, error_message):
    """
    Helper function to run a shell command and handle errors.
    """
    result = subprocess.run(command, shell=True)
    if result.returncode != 0:
        print(error_message)
        sys.exit(1)

def main():
    # Step 1: Generate the API documentation using sphinx-apidoc
    print("Generating API documentation...")
    run_command(
        "sphinx-apidoc -o docs/ reemote/ --force",
        "Error: Failed to generate API documentation with sphinx-apidoc."
    )

    # Step 2: Navigate to the 'docs' directory
    print("Navigating to the 'docs' directory...")
    if not os.path.isdir("docs"):
        print("Error: 'docs' directory does not exist.")
        sys.exit(1)
    os.chdir("docs")

    # Step 3: Build the HTML documentation
    print("Building HTML documentation...")
    run_command(
        "make html",
        "Error: Failed to build HTML documentation."
    )

    print("Documentation successfully built in the 'docs/_build/html' directory.")

if __name__ == "__main__":
    main()