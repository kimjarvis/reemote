from pydantic import Field
from typing import AsyncGenerator, Callable, Any
from reemote.context import Context, ConnectionType
from reemote.core.response import Response
from reemote.core.local import LocalModel
from reemote.core.local import Local


class CallbackRequestModel(LocalModel):
    callback: Callable = Field(
        ...,  # Required field
    )
    value: Any = None  # Optional field with a default value


class Callback(Local):
    Model = CallbackRequestModel

    async def execute(self) -> AsyncGenerator[Context, Response]:
        model_instance = self.Model.model_validate(self.kwargs)

        yield Context(
            type=ConnectionType.LOCAL,
            value=model_instance.value,
            callback=model_instance.callback,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )


class ReturnRequestModel(LocalModel):
    value: Any = None
    changed: bool = True


class Return(Local):
    Model = ReturnRequestModel

    async def execute(self) -> AsyncGenerator[Context, Response]:
        model_instance = self.Model.model_validate(self.kwargs)

        yield Context(
            type=ConnectionType.PASSTHROUGH,
            value=model_instance.value,
            changed=model_instance.changed,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )
