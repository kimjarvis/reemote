# generate_example.py
import inspect


def generate_example(func, filename="code.txt"):
    """
    Extracts the source code of a function, removes the first and last lines,
    and writes the remaining lines to the specified file.
    """
    # Get the source code of the function
    source_lines = inspect.getsource(func).splitlines()

    # Remove the first line (function definition) and the last line
    source_lines = source_lines[1:-1]

    # Write the remaining lines to the flat file, overwriting the file
    with open(filename, "w") as f:  # Write mode to overwrite the file
        for line in source_lines:
            f.write(line + "\n")  # Write each line individually
        f.write("\n")  # Add a single blank line at the end