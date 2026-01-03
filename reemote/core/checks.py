from reemote.core.response import Response
from typing import List

def flatten(obj):
    if isinstance(obj, Response):
        return [obj]
    elif isinstance(obj, list):
        return [item for sub in obj for item in flatten(sub)]
    else:
        raise TypeError("Unsupported type encountered")

def changed(r):
    for x in flatten(r):
        if x.changed:
            return True
    return False

# Do we need this, should it be -1 ?
def get_value(r):
    return flatten(r)[0].value

def mark_changed(result: List[Response]) -> None:
    """Helper to mark result as changed if it exists"""
    if result and hasattr(result, 'changed'):
        result.changed = True

def mark_unchanged(result: List[Response]) -> None:
    """Helper to mark result as changed if it exists"""
    if result and hasattr(result, 'changed'):
        result.changed = False
