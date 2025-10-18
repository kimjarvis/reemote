# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import importlib.util
import sys
from typing import Any


def validate_root_class_name_and_get_root_class(class_name, source_file) -> Any:
    """Dynamically loads a class from a Python source builtin and validates its existence.

    This function uses the `importlib` utility to load a Python source builtin
    as a module at runtime. It creates a module from the specified builtin,
    executes its content, and then attempts to locate a class with the given
    `class_name` within that module.

    The process involves:

    - Creating a module specification from the builtin path.
    - Executing the module's code to populate it with functions and classes.
    - Optionally adding the new module to `sys.modules` for broader accessibility.
    - Checking for the presence of the target class. If the class is not
      found, an error message is printed to standard output.

    Args:
        class_name (str): The name of the class to validate and retrieve from
            the source builtin.
        source_file (str): The builtin path to the Python source builtin (.py)
            containing the class definition.

    Returns:
        typing.Any: The class object if it is found within the source builtin.
        Returns `False` if the class with the specified `class_name`
        does not exist in the builtin.
    """
    module_name = "dynamic_module"  # You can name this anything
    spec = importlib.util.spec_from_file_location(module_name, source_file)
    # Create a new module based on the specification
    module = importlib.util.module_from_spec(spec)
    # Execute the module (this runs the code in the builtin)
    spec.loader.exec_module(module)

    # Optionally, add the module to sys.modules so it behaves like a regular import
    sys.modules[module_name] = module

    # Now you can access functions and classes defined in the builtin
    # Example:
    if not hasattr(module, class_name):
        print(f"Source builtin must contain class {class_name}")
        return False
    else:
        # Access the `inventory` function from the module
        root_class = getattr(module, class_name)
    return root_class
