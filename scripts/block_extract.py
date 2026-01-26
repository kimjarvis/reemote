#!/usr/bin/env python3

import argparse
import re
from pathlib import Path


def is_start_marker(line):
    s = line.strip()
    if re.fullmatch(r"#\s*block extract\s+\S+.*", s):
        return True
    return False


def is_end_marker(line):
    s = line.strip()
    if re.fullmatch(r"#\s*block end\s*", s):
        return True
    return False


def indent_lines(lines, spaces):
    indented = []
    for line in lines:
        stripped = line.rstrip("\n")
        indented_line = f"{' ' * max(0, spaces)}{stripped}\n"
        indented.append(indented_line)
    return indented


def extract_block_info(marker_line, extract_path):
    match = re.match(r"(\s*)#\s*block extract\s+(\S+)(?:\s+(-?\d+))?", marker_line)
    if match:
        leading_ws = match.group(1)
        file_name = match.group(2)
        extra_indent = int(match.group(3)) if match.group(3) else 0
        original_indent = len(leading_ws)
        total_indent = original_indent + extra_indent
        file_path = Path(extract_path) / file_name
        return file_path, total_indent
    return None


def process_file(source_file, extract_path):
    try:
        with open(source_file, "r") as f:
            original_lines = f.readlines()
    except FileNotFoundError:
        print(f"Error: Source file '{source_file}' not found.")
        return

    i = 0
    while i < len(original_lines):
        line = original_lines[i]
        if is_start_marker(line):
            info = extract_block_info(line, extract_path)
            if info:
                file_path, total_indent = info
                i += 1
                block_lines = []
                while i < len(original_lines):
                    current_line = original_lines[i]
                    if is_end_marker(current_line):
                        break
                    block_lines.append(current_line)
                    i += 1
                else:
                    print(f"Warning: Unclosed block starting at {source_file}:{i - len(block_lines)}")
                    break

                # Ensure parent directories exist
                file_path.parent.mkdir(parents=True, exist_ok=True)

                # Write extracted block with indentation
                indented_lines = indent_lines(block_lines, total_indent)
                with open(file_path, "w") as out_f:
                    out_f.writelines(indented_lines)
        i += 1


def process_path(path, extract_path):
    path = Path(path).expanduser().resolve()
    extract_path = Path(extract_path).expanduser().resolve()

    if not path.exists():
        print(f"Error: Source path '{path}' does not exist.")
        return
    if not extract_path.is_dir():
        print(f"Error: Extract path '{extract_path}' is not a directory.")
        return

    if path.is_file():
        process_file(path, extract_path)
    else:
        for file in path.rglob("*.py"):
            process_file(file, extract_path)


def block_extract(source_path, extract_path):
    process_path(source_path, extract_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source_path", required=True, help="Source file or directory.")
    parser.add_argument("--extract_path", required=True, help="Base path for block files.")
    args = parser.parse_args()

    block_extract(source_path=args.source_path, extract_path=args.extract_path)


if __name__ == "__main__":
    main()