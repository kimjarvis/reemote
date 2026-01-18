from typing import Any, AsyncGenerator, Callable, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import Field, model_validator, BaseModel

from reemote.callback import Callback, CommonCallbackRequest, common_callback_request
from reemote.context import ContextType, Context, Method
from reemote.response import AbstractResponseModel

from reemote.router_handler import router_handler



router = APIRouter()

class CallRequest(CommonCallbackRequest):
    callback: Callable = Field(
        ...,  description="Callable callback function."
    )
    value: Optional[Any] = Field(default=None, description="The value to pass to the callback.")


class CallResponse(BaseModel):
    """The response type is method dependent."""
    pass

class GetCall(Callback):
    request_model = CallRequest

    @staticmethod
    async def callback(context: Context) -> None:
        # dummy  callback
        pass

    async def execute(self) -> AsyncGenerator[Context, AbstractResponseModel]:
        model_instance = self.request_model.model_validate(self.kwargs)

        yield Context(
            type=ContextType.CALLBACK, # todo: This could be passthrough
            value=model_instance.value,
            callback=model_instance.callback,
            method=Method.GET, # todo: This should be a method that makes sense for the callback
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )


@router.get("/call", tags=["Core Operations"], response_model=CallResponse)
async def call(
        callback: Any = Query(
            ..., description="Callable callback function."
        ),
        value: Optional[Any] = Query(default=None, description="The value to pass to the callback function."),
        common: CommonCallbackRequest = Depends(common_callback_request),
) -> CallRequest:
    """# Call a callback function

    **This interface is provided to document the Python API only.  It is not meaningful to call this HTTP endpoint.**
    """
    # todo: If this is always a return from put then the response model should be Putresponse
    return await router_handler(CallRequest, GetCall)(value=value,
                                                      callback=callback,
                                                      common=common, )
