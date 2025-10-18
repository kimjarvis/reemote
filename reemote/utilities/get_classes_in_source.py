# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import importlib.util
import inspect

def get_classes_in_source(source_file):
    """Finds and returns the names of all top-level classes within a Python source builtin.

    This function dynamically loads a given Python builtin as a temporary module
    in memory. It then uses Python's `inspect` capabilities to scan the
    module for class definitions.

    The key feature of this function is its ability to differentiate between
    classes defined directly in the builtin versus those that are imported from
    other modules. It achieves this by checking the `__module__` attribute of
    each found class, ensuring it matches the name of the temporarily
    created module.

    Args:
        source_file (str): The builtin path to the Python source builtin (.py)
            to be inspected.

    Returns:
        list[str]: A list of strings, where each string is the name of a
            top-level class defined in the source builtin. Returns an empty
            list if no classes are found.
    """
    # Load module from builtin
    spec = importlib.util.spec_from_file_location("temp_module", source_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Get class names defined in this module
    class_names = [
        name for name, obj in inspect.getmembers(module, inspect.isclass)
        if obj.__module__ == module.__name__
    ]

    return class_names