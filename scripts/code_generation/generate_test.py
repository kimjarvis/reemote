# generate_example.py
import inspect
from pathlib import Path
from typing import Callable


def generate_test(func: Callable, file: str):
    """
    Extracts the source code of a function, removes the first and last lines,
    and writes the remaining lines to the specified file.
    """
    stem = Path(file).stem
    directory = Path(file).parent
    parent = Path(file).parent.name  # Parent directory name
    grandparent = Path(file).parent.parent.name  # Grandparent directory name
    path = f"{directory}/{stem}_test.generated"
    print(f"writing {path}")

    # Get the source code of the function
    source_lines = inspect.getsource(func).splitlines()

    # Remove the first line (function definition) and the last line
    source_lines = source_lines[1:]


    # Write the remaining lines to the flat file, overwriting the file
    with open(path, "w") as f:  # Write mode to overwrite the file
        f.write("@pytest.mark.asyncio\n")
        f.write(f"async def test_{grandparent}_{parent}_{stem}_example(setup_inventory, setup_directory):\n")
        for line in source_lines:
            # Apply replacements if the line contains 'responses = await execute'
            if "responses = await execute" in line:
                line = (
                    line.replace("await execute", "await endpoint_execute")
                    .replace(", inventory)", ")")
                )
            f.write(line + "\n")  # Write each line individually

