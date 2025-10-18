# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
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