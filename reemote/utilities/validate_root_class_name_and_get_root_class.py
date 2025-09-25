import importlib.util
import sys
from typing import Any


def validate_root_class_name_and_get_root_class(class_name, source_file) -> Any:
    module_name = "dynamic_module"  # You can name this anything
    spec = importlib.util.spec_from_file_location(module_name, source_file)
    # Create a new module based on the specification
    module = importlib.util.module_from_spec(spec)
    # Execute the module (this runs the code in the file)
    spec.loader.exec_module(module)

    # Optionally, add the module to sys.modules so it behaves like a regular import
    sys.modules[module_name] = module

    # Now you can access functions and classes defined in the file
    # Example:
    if not hasattr(module, class_name):
        print(f"Source file must contain class {class_name}")
        return False
    else:
        # Access the `inventory` function from the module
        root_class = getattr(module, class_name)
    return root_class
