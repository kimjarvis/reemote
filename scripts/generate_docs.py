from block_extract import block_extract
from code_generation.py_to_codeblock import py_to_codeblock

def main():
    block_extract(source_path="~/reemote/", extract_path="~/reemote/")
    py_to_codeblock(source_path="~/reemote/")

if __name__ == "__main__":
    main()
