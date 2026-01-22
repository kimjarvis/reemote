import argparse
import os
import re
from pathlib import Path


def indent_lines(lines, spaces):
    """Indents each line by the specified number of spaces (can be negative)."""
    indented = []
    for line in lines:
        stripped = line.rstrip("\n")
        indented_line = f"{' ' * max(0, spaces)}{stripped}\n"
        indented.append(indented_line)
    return indented


def extract_block_info(marker_line, insert_path):
    """
    Parses: '# block insert path/to/file.py [indent]'
    or      '# Example usage path/to/file.py [indent]'
    Returns (file_path, total_indent_spaces, original_indent_len) or None.
    """
    match = re.match(r"(\s*)# (block insert|Example usage)\s+(\S+)(?:\s+(-?\d+))?", marker_line)
    if not match:
        return None

    leading_ws = match.group(1)
    file_name = match.group(3)
    extra_indent = int(match.group(4)) if match.group(4) else 0

    original_indent = len(leading_ws)
    total_indent = original_indent + extra_indent
    file_path = Path(insert_path) / file_name
    return file_path, total_indent, original_indent


def is_end_marker(line):
    return re.fullmatch(r"\s*# block end\s*", line.strip()) is not None


def process_file(source_file, insert_path, clear_mode=False):
    try:
        with open(source_file, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"Error: Source file '{source_file}' not found.")
        return

    output = []
    i = 0
    changes_made = False

    while i < len(lines):
        line = lines[i]

        # Check for start marker
        info = extract_block_info(line, insert_path)
        if info:
            file_path, total_indent, orig_indent = info

            # Find end marker
            end_i = None
            for j in range(i + 1, len(lines)):
                if is_end_marker(lines[j]):
                    end_i = j
                    break

            # Always consume from start marker to end marker (if exists)
            if end_i is not None:
                # Remove everything from i to end_i (inclusive)
                i = end_i + 1
            else:
                # No end marker? Just consume start marker and move on
                i += 1

            # In clear mode: done (nothing inserted)
            if clear_mode:
                output.append(line)  # keep start marker only
                changes_made = True
                continue

            # Normal mode: insert block + new end marker
            output.append(line)  # keep start marker

            # Read and insert block content
            try:
                with open(file_path, "r") as f:
                    block_lines = f.readlines()
                indented_block = indent_lines(block_lines, total_indent)
                output.extend(indented_block)
                print(f"Inserted block from {file_path} into {source_file}")
            except FileNotFoundError:
                print(f"Warning: Block file '{file_path}' not found.")
                changes_made = True  # still modify (remove old content)

            # Add fresh end marker
            end_marker = f"{' ' * orig_indent}# block end\n"
            output.append(end_marker)
            changes_made = True

        else:
            # Regular line or orphaned end marker
            if not clear_mode or not is_end_marker(line):
                output.append(line)
            else:
                # In clear mode, drop orphaned end markers
                changes_made = True
            i += 1

    if changes_made:
        with open(source_file, "w") as f:
            f.writelines(output)
        print(f"Updated file: {source_file}")


def process_path(path, insert_path, clear_mode):
    path = Path(path).expanduser().resolve()
    insert_path = Path(insert_path).expanduser().resolve()

    if not path.exists():
        print(f"Error: Source path '{path}' does not exist.")
        return
    if not insert_path.is_dir():
        print(f"Error: Insert path '{insert_path}' is not a directory.")
        return

    if path.is_file():
        process_file(path, insert_path, clear_mode)
    else:
        for py_file in path.rglob("*.py"):
            process_file(py_file, insert_path, clear_mode)


def main():
    parser = argparse.ArgumentParser(description="Insert code blocks into Python files based on markers.")
    parser.add_argument("--source_path", required=True, help="Source file or directory.")
    parser.add_argument("--insert_path", required=True, help="Base path for block files.")
    parser.add_argument("--clear", action="store_true", help="Clear blocks without insertion.")
    args = parser.parse_args()

    process_path(args.source_path, args.insert_path, args.clear)


if __name__ == "__main__":
    main()