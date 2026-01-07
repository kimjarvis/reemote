import argparse
import os
import uvicorn
from reemote.config import Config


def validate_file_path(path, arg_name):
    """Validate that the given path is a string and represents a valid file path."""
    if not isinstance(path, str):
        raise ValueError(f"{arg_name} must be a string.")

    # Expand user directory (e.g., '~/logfile.txt' -> '/home/kim/logfile.txt')
    expanded_path = os.path.expanduser(path)

    # Check if the parent directory exists (if the file doesn't exist yet, the directory must exist)
    if not os.path.isdir(os.path.dirname(expanded_path)):
        raise ValueError(
            f"Directory for {arg_name} does not exist: {os.path.dirname(expanded_path)}"
        )

    return expanded_path


def convert_value(value):
    """
    Convert a value to its appropriate type:
    - int or float if numeric.
    - bool if "True" or "False".
    - Otherwise, keep as string.
    """
    try:
        # Try converting to integer
        return int(value)
    except ValueError:
        try:
            # Try converting to float
            return float(value)
        except ValueError:
            # Check for boolean values
            if value.lower() == "true":
                return True
            elif value.lower() == "false":
                return False
            # Return as string if no conversion is possible
            return value


def main():
    """Entry point for the reemote CLI command"""

    # Initialize argument parser
    parser = argparse.ArgumentParser(description="Server configuration")

    # Add specific arguments for reemote
    parser.add_argument("--logging", "-l", type=str, help="Set the logging file path")
    parser.add_argument(
        "--inventory", "-i", type=str, help="Set the inventory file path"
    )

    # Parse known arguments (reemote-specific) and collect unknown arguments (for uvicorn)
    args, extra_args = parser.parse_known_args()

    # Initialize Config object
    config = Config()

    # Validate and process reemote-specific arguments
    if args.logging:
        try:
            validated_logging_path = validate_file_path(args.logging, "--logging")
            config.set_logging(validated_logging_path)
        except ValueError as e:
            raise ValueError(f"Invalid logging path: {e}")

    if args.inventory:
        try:
            validated_inventory_path = validate_file_path(args.inventory, "--inventory")
            config.set_inventory_path(validated_inventory_path)
        except ValueError as e:
            raise ValueError(f"Invalid inventory path: {e}")

    # Dynamically parse unknown arguments into a dictionary
    extra_kwargs = {}
    i = 0
    while i < len(extra_args):
        arg = extra_args[i]
        if arg.startswith("--"):
            # Handle --key=value format
            if "=" in arg:
                key, value = arg[2:].split("=", 1)  # Remove '--' and split on '='
            else:
                key = arg[2:]  # Remove '--'
                if i + 1 < len(extra_args) and not extra_args[i + 1].startswith("--"):
                    value = extra_args[i + 1]
                    i += 2  # Skip the next item as it's the value
                    continue
                else:
                    # If no value is provided after the key, assume it's a flag (e.g., --reload)
                    value = True

            # Convert the value to its appropriate type
            extra_kwargs[key] = convert_value(value)
        i += 1

    # Start the FastAPI app with uvicorn
    uvicorn.run("reemote.app:app", **extra_kwargs)


if __name__ == "__main__":
    main()
