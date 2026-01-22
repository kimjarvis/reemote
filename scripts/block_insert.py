import argparse
import os
import re
from pathlib import Path


def indent_lines(lines, spaces):
    """Indents each line by the specified number of spaces (can be negative)."""
    return [f"{' ' * max(0, spaces)}{line}" for line in lines]


def extract_block_info(marker_line, insert_path):
    """
    Extracts the file path, indentation adjustment, and original indent from a marker line.
    Returns (file_path, spaces, original_indent) or (None, None, None) if the line is invalid.
    """
    # Match the marker pattern with optional leading whitespace and optional spaces
    match = re.match(
        r"(\s*)# (block insert|Example usage)\s+(\S+)(?:\s+(-?\d+))?", marker_line
    )
    if match:
        original_indent = len(match.group(1))  # Capture the leading whitespace length
        spaces = (
            int(match.group(4)) if match.group(4) else 0
        )  # Default to 0 if not provided
        file_path = Path(insert_path) / match.group(
            3
        )  # Prefix the block path with insert_path
        return file_path, spaces, original_indent
    return None, None, None


def is_block_already_inserted(lines, marker_index, block_lines):
    """
    Checks if the block is already inserted after the marker.
    Returns True if the block is present, False otherwise.
    """
    block_length = len(block_lines)
    return (
        marker_index + block_length + 1 <= len(lines)
        and lines[marker_index + 1 : marker_index + block_length + 1] == block_lines
    )


def block_insert(source_file, insert_path):
    """Processes a single source file and ensures idempotent block insertion."""
    # Read the source file
    try:
        with open(source_file, "r") as f:
            source_lines = f.readlines()
    except FileNotFoundError:
        print(f"Error: Source file '{source_file}' not found.")
        return

    output_lines = []
    changes_made = False  # Track if any changes are made to the file

    i = 0
    while i < len(source_lines):
        line = source_lines[i]
        output_lines.append(line)

        # Check if the line contains a marker (`# block insert` or `# Example usage`)
        file_path, spaces, original_indent = extract_block_info(line, insert_path)
        if file_path and spaces is not None:
            try:
                # Read the block file
                with open(file_path, "r") as f:
                    block_lines = f.readlines()

                # Calculate total indentation (original_indent + spaces, can be negative)
                total_indent = original_indent + spaces
                indented_block = indent_lines(block_lines, total_indent)

                # Check if the block is already inserted
                if not is_block_already_inserted(source_lines, i, indented_block):
                    output_lines.extend(indented_block)
                    print(f"Block inserted into file {source_file} from {file_path}")
                    changes_made = True  # Mark that a change was made

            except FileNotFoundError:
                print(f"Warning: Block file '{file_path}' not found.")

        i += 1

    # Write back to the source file only if changes were made
    if changes_made:
        with open(source_file, "w") as f:
            f.writelines(output_lines)


def process_directory(directory, insert_path):
    """Recursively processes all Python files in the directory."""
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".py"):
                source_file = Path(root) / file
                block_insert(source_file, insert_path)


def main():
    parser = argparse.ArgumentParser(
        description="Insert code blocks into Python files based on markers."
    )
    parser.add_argument(
        "--source_path", required=True, help="Path to the source file or directory."
    )
    parser.add_argument(
        "--insert_path", required=True, help="Base path for block file references."
    )

    args = parser.parse_args()

    source_path = Path(args.source_path).expanduser().resolve()
    insert_path = Path(args.insert_path).expanduser().resolve()

    # Verify source_path exists
    if not source_path.exists():
        print(f"Error: Source path '{source_path}' does not exist.")
        return

    # Verify insert_path exists and is a directory
    if not insert_path.exists() or not insert_path.is_dir():
        print(
            f"Error: Insert path '{insert_path}' does not exist or is not a directory."
        )
        return

    # Process the source path
    if source_path.is_file():
        block_insert(source_path, insert_path)
    elif source_path.is_dir():
        process_directory(source_path, insert_path)


if __name__ == "__main__":
    main()
