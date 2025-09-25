import os
import sys
import pytest

def main():
    # Define the directory containing the tests
    tests_directory = os.path.join(os.getcwd(), "tests")

    # Check if the tests directory exists
    if not os.path.isdir(tests_directory):
        print(f"Error: The directory '{tests_directory}' does not exist.")
        sys.exit(1)

    # Run pytest on the tests directory
    print(f"Running tests in directory: {tests_directory}")
    exit_code = pytest.main([tests_directory])

    # Exit with the same exit code as pytest
    sys.exit(exit_code)

if __name__ == "__main__":
    main()