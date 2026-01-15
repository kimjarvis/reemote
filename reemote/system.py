from typing import Any, AsyncGenerator, Callable, Optional

from pydantic import Field, model_validator

from reemote.callback import Callback, CommonCallbackRequestModel
from reemote.context import ConnectionType, Context, Method
from reemote.core.response import AbstractResponseModel


class CallRequestModel(CommonCallbackRequestModel):
    callback: Callable = Field(
        ...,  # Required field
    )
    value: Any = None  # Optional field with a default value


class Call(Callback):
    request_model = CallRequestModel

    @staticmethod
    async def callback(context: Context) -> None:
        # dummy  callback
        pass

    async def execute(self) -> AsyncGenerator[Context, AbstractResponseModel]:
        model_instance = self.request_model.model_validate(self.kwargs)

        yield Context(
            type=ConnectionType.CALLBACK,
            value=model_instance.value,
            callback=model_instance.callback,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )


class ReturnRequestModel(CommonCallbackRequestModel):
    value: Optional[Any] = None
    method: Method
    changed: Optional[bool] = None
    error: Optional[bool] = None
    message: Optional[str] = None

    @model_validator(mode="after")
    def validate_method_specific_rules(self) -> "ReturnRequestModel":
        # Get the raw input data
        raw_data = self.model_dump()

        # Check if 'value' and 'changed' were explicitly provided in the input data
        value_provided = "value" in raw_data
        changed_provided = "changed" in raw_data

        if self.method == Method.GET:
            if not value_provided or self.value is None:
                raise ValueError("value is required when method is GET")
            if changed_provided and self.changed is not None:
                raise ValueError("changed must not be specified when method is GET")

        elif self.method == Method.POST:
            if value_provided and self.value is not None:
                raise ValueError("value must not be specified when method is POST")
            if changed_provided and self.changed is not None:
                raise ValueError("changed must not be specified when method is POST")

        elif self.method == Method.PUT:
            if value_provided and self.value is not None:
                raise ValueError("value must not be specified when method is PUT")
            if not changed_provided and self.changed is None:
                raise ValueError("changed must be specified when method is PUT")

        return self


class Return(Callback):
    request_model = ReturnRequestModel


    @staticmethod
    async def callback(context: Context) -> None:
        # dummy  callback
        pass

    async def execute(self) -> AsyncGenerator[Context, AbstractResponseModel]:
        model_instance = self.request_model.model_validate(self.kwargs)

        yield Context(
            type=ConnectionType.PASSTHROUGH,
            method=model_instance.method,
            value=model_instance.value,
            changed=model_instance.changed,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )
