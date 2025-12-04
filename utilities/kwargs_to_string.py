def kwargs_to_string(**kwargs):
    """
    Handle nested data structures using repr().
    """

    def format_value(v):
        """Recursively format values using repr() for consistency"""
        if isinstance(v, list):
            return "[" + ", ".join(format_value(item) for item in v) + "]"
        elif isinstance(v, tuple):
            items = ", ".join(format_value(item) for item in v)
            return f"({items})" if len(v) != 1 else f"({items},)"
        elif isinstance(v, dict):
            return "{" + ", ".join(f"{format_value(k)}: {format_value(v)}" for k, v in v.items()) + "}"
        elif isinstance(v, set):
            return "{" + ", ".join(format_value(item) for item in v) + "}"
        else:
            return repr(v)

    return ", ".join(f"{key}={format_value(value)}" for key, value in kwargs.items())
