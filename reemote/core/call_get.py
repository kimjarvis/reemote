from typing import Any, AsyncGenerator, Callable, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from reemote.passthrough import Passthrough, CommonPassthroughRequest, common_passthrough_request
from reemote.context import Context, ContextType, Method
from reemote.response import GetResponseElement, GetResponse
from reemote.router_handler import router_handler

router = APIRouter()


class CallGetRequest(CommonPassthroughRequest):
    callback: Callable = Field(..., description="Callable callback function.")
    value: Optional[Any] = Field(
        default=None, description="The value to pass to the callback function."
    )

class CallGet(Passthrough):
    request_schema=CallGetRequest
    response_schema=GetResponseElement
    method=Method.GET

    async def execute(self) -> AsyncGenerator[Context, List[GetResponseElement]]:
        model_instance = self.request_schema.model_validate(self.kwargs)

        yield Context(
            type=ContextType.PASSTHROUGH,
            value=model_instance.value,
            callback=model_instance.callback,
            method=self.method,
            response_schema=self.response_schema,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )

@router.get(
    "/call_get",
    tags=["Core Operations"],
    response_model=List[GetResponseElement],
)
async def call_get(
    callback: Any = Query(..., description="Callable callback function."),
    value: Optional[Any] = Query(
        default=None, description="The value to pass to the callback function."
    ),
    common: CommonPassthroughRequest = Depends(common_passthrough_request),
) -> CallGetRequest:
    """# CallGet - Call a coroutine that returns a value

    *The REST API endpoint is provided for documentation purposes, it cannot be called directly.*

    Response schema: [reemote.response.GetResponseElement]

    Python API example:

    ```python
    from reemote.context import Context
    from reemote.core.call_get import CallGet

    async def callback(context: Context):
        return context.value + "World!"

    responses = await execute(lambda: CallGet(callback=callback, value="Hello ", group="server104"), inventory)
    for item in responses:
        assert item.value == "Hello World!"
    ```
    """
    return await (router_handler1(CallGet))(
        value=value,
        callback=callback,
        common=common,
    )
