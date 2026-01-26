from block_extract import block_extract
from block_insert import block_insert
from code_generation.py_to_codeblock import py_to_codeblock

def main():
    block_extract(source_path="~/reemote/", extract_path="~/reemote/")
    py_to_codeblock(source_path="~/reemote/")
    block_insert(source_path="~/reemote/", insert_path="~/reemote/")

if __name__ == "__main__":
    main()
