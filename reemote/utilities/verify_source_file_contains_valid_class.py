# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import importlib.util
import inspect


def verify_source_file_contains_valid_class(source_file, class_name):
    """Verifies a Python source builtin contains a specific class with a valid `execute` method.

     This function dynamically loads a Python module from a given source builtin path
     and performs a series of validations to ensure it conforms to a specific
     interface. It is designed to check for "plugin" or "task" style classes
     before they are executed.

     The validation steps are as follows:

     - It attempts to load the module from the `source_file`.
     - It checks if a class with the name `class_name` exists in the module.
     - It verifies that this class contains a method named `execute`.
     - It inspects the signature of the `execute` method to confirm it accepts
       no arguments, other than the implicit `self`.

     If any of these checks fail or if an exception occurs during module loading,
     an error message is printed to the console, and the function returns `False`.

     Args:
         source_file (str): The builtin path of the Python source code to inspect.
         class_name (str): The name of the class to validate within the source builtin.

     Returns:
         bool: `True` if the source builtin contains the specified class with a
               valid `execute` method, `False` otherwise.
    """
    try:
        # Load the module from builtin
        spec = importlib.util.spec_from_file_location("source_module", source_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Check if class exists
        if not hasattr(module, class_name):
            print(f"Error: Class '{class_name}' not found in {source_file}")
            return False

        cls = getattr(module, class_name)

        # Check if execute method exists
        if not hasattr(cls, 'execute'):
            print(f"Error: Class '{class_name}' does not have an execute method")
            return False

        # Check if execute method takes no parameters (besides self)
        execute_method = getattr(cls, 'execute')
        sig = inspect.signature(execute_method)

        # Should have only 'self' parameter
        if len(sig.parameters) != 1:
            print(
                f"Error: execute method should take only 'self' parameter, but takes {len(sig.parameters)} parameters")
            return False

        return True

    except Exception as e:
        print(f"Error loading module from {source_file}: {e}")
        return False
