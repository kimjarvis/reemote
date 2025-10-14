# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import ast

def parse_kwargs_string(param_str):
    """Parses a string of comma-separated key-value pairs into a dictionary.

    This function is designed to interpret a string like 'key=value,key2=value2'
    and convert it into a Python dictionary. It uses `ast.literal_eval` to
    safely parse the value part of each pair, allowing it to correctly handle
    various Python literal types.

    Key features include:

    - Parsing integers, floats, booleans, `None`, and quoted strings.
    - Handling list literals, even with commas inside (e.g., `items=[1,2,3]`).
    - Using a regular expression to robustly split key-value pairs.
    - Defaulting to a plain string for values that cannot be evaluated
      (e.g., an unquoted string like `color=red`).

    Args:
        param_str (str): The input string containing key-value pairs.
            For example: "count=10,is_active=True,names=['A','B']".

    Returns:
        dict: A dictionary where keys are the parameter names and values are
            the parsed Python objects. Returns an empty dictionary if the
            input string is empty.
    """
    if not param_str:
        return {}

    kwargs = {}
    # Use regex to split key-value pairs while respecting quoted values and lists
    import re
    pattern = r'(\w+)=((?:\[[^\]]*\]|[^,])+)'
    matches = re.findall(pattern, param_str)

    for key, value_str in matches:
        key = key.strip()
        value_str = value_str.strip()

        # Safely evaluate the value (handles True, False, None, numbers, strings, lists)
        try:
            value = ast.literal_eval(value_str)
        except (ValueError, SyntaxError):
            # Fallback: treat as string if literal_eval fails
            value = value_str

        kwargs[key] = value

    return kwargs
