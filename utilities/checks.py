from response import Response

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
def get_output(r):
    return flatten(r)[0].output