import asyncio
from block_insert import block_insert


import os

def remove_generated_files(base_dir):
    # Define file extensions to remove
    target_extensions = {'.log', '.generated'}
    files_to_remove = []

    # Walk through the directory recursively
    for root, _, files in os.walk(base_dir):
        for file in files:
            if os.path.splitext(file)[1] in target_extensions:
                files_to_remove.append(os.path.join(root, file))

    # List files to be removed
    if not files_to_remove:
        print("No matching files found.")
        return

    print("The following files will be removed:")
    for file_path in files_to_remove:
        print(file_path)

    # Prompt user for confirmation
    confirm = input("Do you want to proceed with deletion? (yes/no): ").strip().lower()
    if confirm == 'yes':
        for file_path in files_to_remove:
            try:
                os.remove(file_path)
                print(f"Removed: {file_path}")
            except Exception as e:
                print(f"Failed to remove {file_path}: {e}")
    else:
        print("Operation canceled.")

async def main():
    # Call the function with the base directory
    remove_generated_files(os.path.expanduser('~/reemote'))

    block_insert(source_path="~/reemote", insert_path="~/reemote/examples", clear_mode=True)


if __name__ == "__main__":
    asyncio.run(main())
