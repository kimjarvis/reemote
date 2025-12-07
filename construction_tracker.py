# construction_tracker.py
from typing import List, Tuple, Optional, Any, Callable, AsyncGenerator
import threading
from contextlib import contextmanager
import inspect


class ConstructionTracker:
    """Tracks object construction hierarchy in a tree structure"""

    _next_id: int = 1
    _registry: dict = {}  # obj_id -> (class_name, parent_id)
    _parent_stack: List[int] = []
    # Thread-local storage for current parent
    _local = threading.local()

    @classmethod
    def clear(cls):
        """Clear all tracking data"""
        cls._next_id = 1
        cls._registry.clear()
        cls._parent_stack.clear()
        if hasattr(cls._local, 'current_parent'):
            cls._local.current_parent = None

    @classmethod
    def get_current_parent(cls) -> Optional[int]:
        """Get current parent ID"""
        if hasattr(cls._local, 'current_parent'):
            return cls._local.current_parent
        return cls._parent_stack[-1] if cls._parent_stack else None

    @classmethod
    def register(cls, class_name: str) -> int:
        """Register a new object and return its ID"""
        obj_id = cls._next_id

        # Get current parent
        parent_id = cls.get_current_parent()

        # Store in registry
        cls._registry[obj_id] = (class_name, parent_id)

        cls._next_id += 1
        return obj_id

    @classmethod
    def set_parent(cls, parent_id: Optional[int]):
        """Set current parent for upcoming object creations"""
        cls._local.current_parent = parent_id

    @classmethod
    @contextmanager
    def parent_context(cls, parent_id: Optional[int]):
        """Context manager for temporarily setting parent"""
        old_parent = cls.get_current_parent()
        cls.set_parent(parent_id)
        try:
            yield
        finally:
            cls.set_parent(old_parent)

    @classmethod
    def push_parent(cls, obj_id: int):
        """Push object ID to parent stack"""
        cls._parent_stack.append(obj_id)
        cls.set_parent(obj_id)

    @classmethod
    def pop_parent(cls):
        """Pop from parent stack"""
        if cls._parent_stack:
            cls._parent_stack.pop()
            # Set new parent to previous item on stack or None
            new_parent = cls._parent_stack[-1] if cls._parent_stack else None
            cls.set_parent(new_parent)

    @classmethod
    def get_hierarchy(cls) -> List[Tuple[int, str, Optional[int]]]:
        """Get the complete construction hierarchy as list of tuples"""
        return [(obj_id, class_name, parent_id)
                for obj_id, (class_name, parent_id) in sorted(cls._registry.items())]

    @classmethod
    def print(cls):
        """Print the hierarchy in the requested format"""
        hierarchy = cls.get_hierarchy()
        print("[")
        for obj_id, class_name, parent_id in hierarchy:
            parent_str = parent_id if parent_id is not None else None
            print(f"    ({obj_id}, '{class_name}', {parent_str}),")
        print("]")


def track_construction(cls):
    """Decorator to track object construction hierarchy"""
    # Store the original __new__ method
    original_new = getattr(cls, '__new__', object.__new__)

    def new_new(cls, *args, **kwargs):
        # Get current parent BEFORE creating instance
        current_parent = ConstructionTracker.get_current_parent()

        # Create the instance
        if original_new is object.__new__:
            instance = object.__new__(cls)
        else:
            instance = original_new(cls, *args, **kwargs)

        # Register with the parent that was current at creation time
        obj_id = ConstructionTracker.register(cls.__name__)

        # Store ID on instance
        instance._construction_id = obj_id

        return instance

    def new_init(self, *args, **kwargs):
        # Call original __init__ if it exists
        original_init = getattr(self.__class__, '__init__', None)
        if original_init and original_init != new_init:
            original_init(self, *args, **kwargs)

    # Replace methods
    cls.__new__ = staticmethod(new_new)
    cls.__init__ = new_init

    return cls


# Helper function to wrap generator methods
def with_parent_context(method):
    """
    Decorator for async generator methods that automatically sets
    parent context based on self._construction_id
    """
    if inspect.isasyncgenfunction(method):
        async def wrapper(self, *args, **kwargs):
            # Get the construction ID of self
            parent_id = getattr(self, '_construction_id', None)

            # Create the async generator
            gen = method(self, *args, **kwargs)

            try:
                # Get first value from generator
                value = await gen.__anext__()

                while True:
                    # Yield the value
                    try:
                        # Send value back and get next value
                        send_value = yield value

                        # Process next value with parent context
                        with ConstructionTracker.parent_context(parent_id):
                            value = await gen.asend(send_value)

                    except StopIteration:
                        break

            except StopAsyncIteration:
                pass
            finally:
                await gen.aclose()

        # Mark as async generator
        import types
        wrapper = types.coroutine(wrapper)
        return wrapper

    else:
        # For non-async generator methods
        def wrapper(self, *args, **kwargs):
            # Get the construction ID of self
            parent_id = getattr(self, '_construction_id', None)

            # Create the generator
            gen = method(self, *args, **kwargs)

            try:
                # Get first value from generator
                value = next(gen)

                while True:
                    # Yield the value
                    try:
                        send_value = yield value

                        # Process next value with parent context
                        with ConstructionTracker.parent_context(parent_id):
                            value = gen.send(send_value)

                    except StopIteration:
                        break

            except StopIteration:
                pass
            finally:
                gen.close()

        return wrapper


# Helper to create auto-parent-yielding generators
async def auto_parent_yield(generator_func):
    """
    Helper to automatically set parent context when yielding from a generator

    Usage:
        async def execute(self):
            async for item in auto_parent_yield(self._my_generator):
                yield item
    """
    # Get parent ID from self (assuming generator_func is a bound method)
    if hasattr(generator_func, '__self__'):
        parent_id = getattr(generator_func.__self__, '_construction_id', None)
    else:
        parent_id = None

    # Execute the generator with parent context
    gen = generator_func()
    try:
        while True:
            with ConstructionTracker.parent_context(parent_id):
                value = await gen.__anext__()
            yield value
    except StopAsyncIteration:
        pass
    finally:
        await gen.aclose()