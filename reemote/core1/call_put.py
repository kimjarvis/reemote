from typing import Any, AsyncGenerator, Callable, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from reemote.context import Context, ContextType, Method
from reemote.passthrough import (
    CommonPassthroughRequest,
    Passthrough,
    common_passthrough_request,
)
from reemote.response import PutResponseElement
from reemote.router_handler import router_handler1

router = APIRouter()


class CallPutRequest(CommonPassthroughRequest):
    model_config = {
        "title": "CallPutRequest",
        "json_schema_extra": {
            "example": {
                **CommonPassthroughRequest.model_config["json_schema_extra"]["example"],
                "callback": "f(x)",
                "value": True,
            },
            "description": "Response from the CallPut API.",
        },
    }

    callback: Callable = Field(..., description="Callable callback function.")
    value: Optional[Any] = Field(
        default=None, description="The value to pass to the callback function."
    )


class CallPut(Passthrough):
    request_schema = CallPutRequest
    response_schema = PutResponseElement
    method = Method.PUT

    async def execute(self) -> AsyncGenerator[Context, List[PutResponseElement]]:
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


@router.put(
    "/call_put",
    tags=["Core Operations"],
    response_model=List[PutResponseElement],
    responses={
        200: {
            "description": "Successful Response",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "host": "server104",
                            "error": False,
                            "message": "",
                            "changed": False,
                        }
                    ]
                }
            },
        }
    },
)
async def call_put(
    callback: Any = Query(..., description="Callable callback function."),
    value: Optional[Any] = Query(
        default=None, description="The value to pass to the callback function."
    ),
    common: CommonPassthroughRequest = Depends(common_passthrough_request),
) -> CallPutRequest:
    """# Call a coroutine that returns a changed indication

    *This REST API cannot be called.*

    ## Python API

    - Coroutine: `CallPut`
    - Response schema: `[PutResponseElement]`

    Python API example:

    ```python
    from reemote.context import Context
    from reemote import core1

    async def callback(context: Context):
        context.changed = context.value

    responses = await execute(lambda: core1.CallPut(callback=callback, value=False, group="server104"), inventory)
    for item in responses:
        assert item.changed == False, "The callback should return its argument"
    ```
    """
    return await (router_handler1(CallPut))(
        value=value,
        callback=callback,
        common=common,
    )
