import importlib.util
import inspect

def get_classes_in_source(source_file):
    """
    Returns a list of class names (strings) defined in the given Python source file.
    Only includes top-level classes defined in the file — excludes imported classes.
    """
    # Load module from file
    spec = importlib.util.spec_from_file_location("temp_module", source_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Get class names defined in this module
    class_names = [
        name for name, obj in inspect.getmembers(module, inspect.isclass)
        if obj.__module__ == module.__name__
    ]

    return class_names