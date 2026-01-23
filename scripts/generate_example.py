# generate_example.py
import inspect
from pathlib import Path
from typing import Callable


def generate_example(func: Callable, file: str):
    """
    Extracts the source code of a function, removes the first and last lines,
    and writes the remaining lines to the specified file.
    """
    stem = Path(file).stem
    directory = Path(file).parent
    path = f"{directory}/{stem}_example.generated"
    print(f"writing {path}")

    # Get the source code of the function
    source_lines = inspect.getsource(func).splitlines()

    # Remove the first line (function definition) and the last line
    source_lines = source_lines[1:-1]


    # Write the remaining lines to the flat file, overwriting the file
    with open(path, "w") as f:  # Write mode to overwrite the file
        for line in source_lines:
            f.write(line + "\n")  # Write each line individually
        f.write("\n")  # Add a single blank line at the end


