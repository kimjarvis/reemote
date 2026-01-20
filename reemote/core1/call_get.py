from typing import Any, AsyncGenerator, Callable, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from reemote.context import Context, ContextType, Method
from reemote.passthrough import (
    CommonPassthroughRequest,
    Passthrough,
    common_passthrough_request,
)
from reemote.response import GetResponseElement
from reemote.router_handler import router_handler1

router = APIRouter()


class CallGetRequest(CommonPassthroughRequest):
    model_config = {
        "title": "CallGetRequest",
        "json_schema_extra": {
            "example": {
                **CommonPassthroughRequest.model_config["json_schema_extra"]["example"],
                "callback": "f(x)",
                "value": True,
            },
            "description": "Response from the CallGet API.",
        }
    }

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
    """# Call a coroutine that returns a value

    *This REST API cannot be called.*

    ## Python API

    - Coroutine: `CallGet`
    - Response schema: `[GetResponseElement]`

    Python API example:

    ```python
    from reemote.context import Context
    from reemote import core1

    async def callback(context: Context):
        return context.value + "World!"

    responses = await execute(lambda: core1.CallGet(callback=callback, value="Hello ", group="server104"), inventory)
    for item in responses:
        assert item.value == "Hello World!"
    ```
    """
    return await (router_handler1(CallGet))(
        value=value,
        callback=callback,
        common=common,
    )
