# common/base_classes.py
from typing import Any, AsyncGenerator, Type, Optional
from pydantic import BaseModel
from command import Command
from response import Response
from utilities.validate_parameters import validate_parameters


class BaseCommand:
    """Base class for all command implementations"""
    Model: Type[BaseModel] = None

    def __init__(self, **kwargs: Any):
        if self.Model is None:
            raise NotImplementedError("Model must be defined in subclass")

        response = validate_parameters(self.Model, **kwargs)
        if response["valid"]:
            # Get extra kwargs (those not in the Model's fields)
            model_fields = self.Model.__fields__.keys()
            self.extra_kwargs = {k: v for k, v in kwargs.items() if k not in model_fields}
            self._data = response["data"]
        else:
            print(f"Validation errors: {response['errors']}")
            raise ValueError(f"Validation failed: {response['errors']}")

    async def execute(self) -> AsyncGenerator[Command, Response]:
        """To be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement execute method")

    def mark_changed(self, result: Optional[Response]) -> None:
        """Helper to mark result as changed if it exists"""
        if result and hasattr(result, 'changed'):
            result.changed = True


class ShellBasedCommand(BaseCommand):
    """Base class for commands that wrap Shell commands"""

    async def execute_shell_command(self, cmd: str) -> AsyncGenerator[Command, Response]:
        """Execute a shell command and mark as changed"""
        from commands.server import Shell
        result = yield Command(
            command=cmd,
            **self.extra_kwargs
        )
        self.mark_changed(result)
        return


