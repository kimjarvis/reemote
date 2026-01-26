from pathlib import Path

def py_to_codeblock(source_path: str = None):
    process_path(source_path)

def process_path(path):
    if path is None:
        path = "."
    path = Path(path).expanduser().resolve()

    if not path.exists():
        print(f"Error: Source path '{path}' does not exist.")
        return
    if not path.is_dir():
        print(f"Error: Insert path '{path}' is not a directory.")
        return

    for file in path.rglob("*.py.generated"):
        process_file(file)

def process_file(source_file):
    try:
        with open(source_file, "r") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: Source file '{source_file}' not found.")
        return

    markdown_content = f"```python\n{content}\n```"

    output_file = source_file.with_suffix(".md.generated")
    with open(output_file, "w") as f:
        f.write(markdown_content)