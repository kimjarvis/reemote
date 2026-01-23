# generate_responses.py
import json
from pathlib import Path
from typing import Dict


def generate_responses(responses: Dict, file: str):
    """
    Generate a formatted responses file with Python-style booleans,
    strip the first and last lines, and write it to the specified file.
    """
    stem = Path(file).stem
    directory = Path(file).parent
    path = f"{directory}/{stem}_responses.generated"
    print(f"writing {path}")

    # Convert the responses dictionary to a formatted JSON string with indent=4
    json_str = json.dumps(responses, indent=4)

    # Replace "false" with "False" and "true" with "True"
    json_str = json_str.replace("false", "False").replace("true", "True")

    # Write the modified JSON string to the file
    with open(path, "w") as f:
        f.write(json_str)

    # Strip the first and last lines from the file
    with open(path, "r") as f:
        lines = f.readlines()

    # Remove the first and last lines
    stripped_lines = lines[1:-1]

    # Rewrite the modified content back to the file
    with open(path, "w") as f:
        f.writelines(stripped_lines)