import argparse
import os
import re
from pathlib import Path


def indent_lines(lines, spaces):
    """Indents each line by the specified number of spaces (can be negative)."""
    indented = []
    for line in lines:
        # Remove trailing newline, indent, then add newline back
        stripped_line = line.rstrip("\n")
        indented_line = f"{' ' * max(0, spaces)}{stripped_line}\n"
        indented.append(indented_line)
    return indented


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

        # Get the file path from the match
        file_name = match.group(3)
        file_path = Path(insert_path) / file_name
        return file_path, spaces, original_indent
    return None, None, None


def is_end_marker(line):
    """Checks if a line is an end marker."""
    return re.match(r"\s*# block end\s*", line) is not None


def find_end_marker_index(lines, start_index):
    """Finds the index of the next end marker after start_index."""
    for i in range(start_index + 1, len(lines)):
        if is_end_marker(lines[i]):
            return i
    return None


def block_insert(source_file, insert_path, clear_mode=False):
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

        # Check if the line contains a start marker (`# block insert` or `# Example usage`)
        file_path, spaces, original_indent = extract_block_info(line, insert_path)

        if file_path and spaces is not None:
            # Find the corresponding end marker
            end_index = find_end_marker_index(source_lines, i)

            # Always add the start marker to output
            output_lines.append(line)

            # Track if there was content to clear
            had_content = False

            if end_index is not None:
                # Check if there's any content between the markers
                for j in range(i + 1, end_index):
                    if source_lines[j].strip():
                        had_content = True
                        break

                # Skip all content between markers
                i = end_index

                if had_content:
                    changes_made = True
                    print(f"Cleared content between markers in file {source_file}")
            else:
                # No end marker found
                i += 1

            # In clear mode, we just skip everything (including end marker if it exists)
            if clear_mode:
                continue

            # Normal mode: Insert content from file
            try:
                # Read the block file
                with open(file_path, "r") as f:
                    block_lines = f.readlines()

                # Calculate total indentation (original_indent + spaces, can be negative)
                total_indent = original_indent + spaces
                indented_block = indent_lines(block_lines, total_indent)

                # Add the block content
                output_lines.extend(indented_block)

                # Add the end marker
                end_marker = f"{' ' * original_indent}# block end\n"
                output_lines.append(end_marker)

                if not had_content:
                    changes_made = True
                print(f"Block inserted into file {source_file} from {file_path}")

            except FileNotFoundError:
                print(f"Warning: Block file '{file_path}' not found.")
                # If file not found, still add end marker to maintain structure
                if not clear_mode:
                    end_marker = f"{' ' * original_indent}# block end\n"
                    output_lines.append(end_marker)
                    changes_made = True

        elif is_end_marker(line):
            # In clear mode, skip the end marker (don't add it to output)
            if clear_mode:
                i += 1
                continue

            # In normal mode, we should only see end markers that are orphaned
            # (not associated with a start marker we processed)
            output_lines.append(line)
            i += 1

        else:
            # Regular line
            output_lines.append(line)
            i += 1

    # Write back to the source file only if changes were made
    if changes_made:
        with open(source_file, "w") as f:
            f.writelines(output_lines)
        print(f"Updated file: {source_file}")


def process_directory(directory, insert_path, clear_mode):
    """Recursively processes all Python files in the directory."""
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".py"):
                source_file = Path(root) / file
                block_insert(source_file, insert_path, clear_mode)


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
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear content between block markers (including end marker) without insertion.",
    )

    args = parser.parse_args()

    source_path = Path(args.source_path).expanduser().resolve()
    insert_path = Path(args.insert_path).expanduser().resolve()
    clear_mode = args.clear

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
        block_insert(source_path, insert_path, clear_mode)
    elif source_path.is_dir():
        process_directory(source_path, insert_path, clear_mode)


if __name__ == "__main__":
    main()
