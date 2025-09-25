import importlib.util
import inspect


def verify_source_file_contains_valid_class(source_file, class_name):
    """Verify that the class exists and has execute method with no parameters"""
    try:
        # Load the module from file
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
