import ast


def parse_kwargs_string(param_str):
    """Parse 'key=value,key2=value2' string into dict."""
    if not param_str:
        return {}
    kwargs = {}
    for pair in param_str.split(','):
        key, value_str = pair.split('=', 1)
        key = key.strip()
        value_str = value_str.strip()

        # Safely evaluate the value (handles True, False, None, numbers, strings)
        try:
            value = ast.literal_eval(value_str)
        except (ValueError, SyntaxError):
            # Fallback: treat as string if literal_eval fails
            value = value_str

        kwargs[key] = value
    return kwargs
