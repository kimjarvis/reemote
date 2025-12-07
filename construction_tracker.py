# construction_tracker.py
from typing import List, Tuple, Optional, Any
import threading
from contextlib import contextmanager
import inspect
import functools


class ConstructionTracker:
    """Tracks object construction hierarchy in a tree structure"""

    _next_id: int = 1
    _registry: dict = {}  # obj_id -> (class_name, parent_id)
    _parent_stack: List[int] = []
    # Thread-local storage for current parent
    _local = threading.local()

    @classmethod
    def get_current_id(cls) -> Optional[int]:
        return cls._next_id

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


    @classmethod
    def get_parents(cls) -> List[Tuple[int, str]]:
        hierarchy_data = cls.get_hierarchy()
        start_id = cls.get_current_id()-1
        if not hierarchy_data:
            return []

        # Create a mapping from id to node data for efficient lookup
        node_map = {node[0]: node for node in hierarchy_data}

        # Find the starting node
        if start_id not in node_map:
            raise ValueError(f"Node with id {start_id} not found in hierarchy")

        parents = []

        # Start with the current node itself
        current_node = node_map[start_id]
        parents.append((current_node[0], current_node[1]))

        # Now get all parents
        parent_id = current_node[2]

        # Traverse up the parent chain
        while parent_id is not None:
            parent_node = node_map.get(parent_id)
            if parent_node is None:
                raise ValueError(f"Parent with id {parent_id} not found in hierarchy")

            parents.append((parent_node[0], parent_node[1]))
            parent_id = parent_node[2]

        return parents

def track_construction(cls):
    """Decorator to track object construction hierarchy"""
    # Store the original __new__ and __init__ methods BEFORE modifying them
    original_new = cls.__new__
    original_init = cls.__init__

    # Create a closure to capture the original init
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

    # Create wrapper that calls the ORIGINAL init, not self.__class__.__init__
    @functools.wraps(original_init)
    def new_init(self, *args, **kwargs):
        # Call the ORIGINAL init function
        return original_init(self, *args, **kwargs)

    # Replace methods
    cls.__new__ = staticmethod(new_new)
    cls.__init__ = new_init

    return cls


def track_yields(method):
    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        parent_id = getattr(self, '_construction_id', None)

        # Store the original parent to restore later
        original_parent = ConstructionTracker.get_current_parent()

        # Always set our ID as parent when this generator runs
        ConstructionTracker.set_parent(parent_id)

        try:
            gen = method(self, *args, **kwargs)
            try:
                value = await gen.__anext__()
                while True:
                    try:
                        # Yield the current value
                        result = yield value
                        # Get the next value by sending the result
                        value = await gen.asend(result)
                    except StopAsyncIteration:
                        break
            finally:
                await gen.aclose()
        finally:
            # Restore original parent
            ConstructionTracker.set_parent(original_parent)

    return wrapper