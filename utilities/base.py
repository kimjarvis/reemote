from command import Command
from typing import Any
from utilities.validate_parameters import validate_parameters
import logging

class Base():
    def __init__(self, Model, **kwargs: Any):
        logging.info(f"base.py Base() __init__ kwargs: {kwargs}")
        response = validate_parameters(Model,**kwargs)
        if response["valid"]:
            extra_kwargs = {k: v for k, v in kwargs.items() if k not in Model.__fields__}
            self.command=Command(
                command=response.get('cmd'),
                **extra_kwargs
            )
        else:
            print(f"Validation errors: {response['errors']}")
            raise ValueError(f"Shell validation failed: {response['errors']}")



    async def execute(self):
        """Async generator that yields a Command."""
        # Yield the Command for execution and receive the result when resumed
        result = yield self.command

        # When we resume, mark the result as changed
        if result and hasattr(result, 'changed'):
            result.changed = True

        # End the async generator without returning a value
        return