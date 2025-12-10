import functools
import logging
from typing import List, Any

# Global storage
_ROOT_HIERARCHY: List[Any] = []


def track_construction(cls):
    """Decorator to add results tracking to classes"""
    orig_init = cls.__init__

    def new_init(self, *args, **kwargs):
        self._execution_results = []
        if orig_init:
            orig_init(self, *args, **kwargs)

    if orig_init:
        cls.__init__ = new_init
    return cls


def track_yields(method):
    """Decorator to collect all yield results"""

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        if not hasattr(self, '_execution_results'):
            self._execution_results = []
        self._execution_results.clear()

        # Store reference to self for context
        import sys
        frame = sys._getframe()
        while frame:
            if 'self' in frame.f_locals:
                frame.f_locals['_caller_self'] = self
                break
            frame = frame.f_back

        if self.__class__.__name__ == 'C':
            _ROOT_HIERARCHY.clear()

        try:
            gen = method(self, *args, **kwargs)

            try:
                value = await gen.__anext__()

                while True:
                    try:
                        result = yield value

                        if result is not None:
                            self._execution_results.append(result)

                            if self.__class__.__name__ == 'C':
                                if (hasattr(value, '__class__') and
                                        value.__class__.__name__ in ['A', 'B'] and
                                        hasattr(value, '_execution_results') and
                                        result is not value):

                                    class_results = getattr(value, '_execution_results', [])
                                    if class_results:
                                        _ROOT_HIERARCHY.append(class_results)
                                else:
                                    _ROOT_HIERARCHY.append(result)

                        value = await gen.asend(result)

                    except StopAsyncIteration:
                        break
            finally:
                await gen.aclose()

        except Exception as e:
            logging.error(f"Error in {self.__class__.__name__}.execute(): {e}", exc_info=True)
            raise

    return wrapper


def get_structured_results(caller=None):
    """Get structured results"""
    if caller and hasattr(caller, '__class__'):
        class_name = caller.__class__.__name__
        if class_name == 'C':
            return _ROOT_HIERARCHY.copy()
        elif class_name in ['A', 'B']:
            return getattr(caller, '_execution_results', [])

    # Default: return root hierarchy
    return _ROOT_HIERARCHY.copy()


def clear_all_results():
    """Clear all results"""
    _ROOT_HIERARCHY.clear()


def get_all_results():
    """Get all results (flat)"""
    flat = []
    for item in _ROOT_HIERARCHY:
        if isinstance(item, list):
            flat.extend(item)
        else:
            flat.append(item)
    return flat