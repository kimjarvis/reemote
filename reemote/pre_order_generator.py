from reemote.operation import Operation


def pre_order_generator(node):
    """
    Enhanced generator function with better error handling and string wrapping.
    """
    stack = [(node, iter(node.execute()))]
    result = None

    while stack:
        current_node, iterator = stack[-1]
        try:
            value = iterator.send(result) if result is not None else next(iterator)
            result = None

            # Handle different types of yielded values
            if isinstance(value, Operation):
                result = yield value
            elif isinstance(value, str):
                # Auto-wrap strings in Operation objects
                operation = Operation(value)
                result = yield operation
            elif hasattr(value, 'execute') and callable(value.execute):
                # If it's a node with execute method, push to stack
                stack.append((value, iter(value.execute())))
            else:
                raise TypeError(f"Unsupported yield type: {type(value)}")

        except StopIteration:
            stack.pop()
        except Exception as e:
            # Handle errors in node execution
            print(f"Error in node execution: {e}")
            stack.pop()
