import ast

def parse_kwargs_string(param_str):
    """Parse 'key=value,key2=value2' string into dict, handling lists and other literals."""
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
