from typing import Any, AsyncGenerator, Callable, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import Field, model_validator, BaseModel

from reemote.callback import Callback, CommonCallbackRequest
from reemote.context import ContextType, Context, Method
from reemote.response import AbstractResponseModel
from reemote.router_handler import router_handler
from reemote.callback import CommonCallbackRequest, common_callback_request

router = APIRouter()

# Dummy response schema class
class ReturnResponse(BaseModel):
    """The response type is method dependent."""
    pass

class ReturnRequest(CommonCallbackRequest):
    value: Optional[Any] = Field(default=None, description="The value to return.")
    method: Method = Field(..., description="The HTTP method.")
    changed: Optional[bool] = Field(default=None, description="Whether the operation changed the host.")
    error: Optional[bool] = Field(default=None, description="Whether an error occurred during the operation.")
    message: Optional[str] = Field(default=None, description="Error message.")

    @model_validator(mode="after")
    def validate_method_specific_rules(self) -> "ReturnRequest":
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
    request_model = ReturnRequest

    @staticmethod
    async def callback(context: Context) -> None:
        # dummy  callback
        pass

    async def execute(self) -> AsyncGenerator[Context, AbstractResponseModel]:
        model_instance = self.request_model.model_validate(self.kwargs)

        yield Context(
            type=ContextType.PASSTHROUGH,
            method=model_instance.method,
            value=model_instance.value,
            changed=model_instance.changed,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )


@router.get("/return", tags=["Core Operations"], response_model=ReturnResponse)
async def _return(
        value: Optional[Any] = Query(default=None, description="The value to return."),
        method: Method = Query(..., description="The HTTP method."),
        changed: Optional[bool] = Query(default=None, description="Whether the operation changed the host."),
        error: Optional[bool] = Query(default=None, description="Whether an error occurred during the operation."),
        message: Optional[str] = Query(default=None, description="Error message."),
        common: CommonCallbackRequest = Depends(common_callback_request),
) -> ReturnRequest:
    """# Return from a put operation

    **This interface is provided to document the Python API only.  It is not meaningful to call this HTTP endpoint.**
    """
    # todo: If this is always a return from put then the response model should be Putresponse
    return await router_handler(ReturnRequest, Return)(value=value,
                                                       mmethod=method,
                                                       changed=changed,
                                                       error=error,
                                                       message=message,
                                                       common=common,)
