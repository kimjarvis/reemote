from pydantic import Field
from typing import AsyncGenerator, Callable, Any
from reemote.context import Context, ConnectionType
from reemote.core.response import ResponseModel
from reemote.callback import CommonCallbackRequestModel
from reemote.callback import Callback


class CallRequestModel(CommonCallbackRequestModel):
    callback: Callable = Field(
        ...,  # Required field
    )
    value: Any = None  # Optional field with a default value


class Call(Callback):
    Model = CallRequestModel

    @staticmethod
    async def callback(context: Context) -> None:
        # dummy  callback
        pass

    async def execute(self) -> AsyncGenerator[Context, ResponseModel]:
        model_instance = self.Model.model_validate(self.kwargs)

        yield Context(
            type=ConnectionType.LOCAL,
            value=model_instance.value,
            callback=model_instance.callback,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )


class ReturnRequestModel(CommonCallbackRequestModel):
    value: Any = None
    changed: bool = True


class Return(Callback):
    Model = ReturnRequestModel

    @staticmethod
    async def callback(context: Context) -> None:
        # dummy  callback
        pass

    async def execute(self) -> AsyncGenerator[Context, ResponseModel]:
        model_instance = self.Model.model_validate(self.kwargs)

        yield Context(
            type=ConnectionType.PASSTHROUGH,
            value=model_instance.value,
            changed=model_instance.changed,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )
