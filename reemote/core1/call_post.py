from typing import Any, AsyncGenerator, Callable, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from reemote.context import Context, ContextType, Method
from reemote.passthrough import (
    CommonPassthroughRequest,
    Passthrough,
    common_passthrough_request,
)
from reemote.response import PostResponseElement
from reemote.router_handler import router_handler1

router = APIRouter()


class CallPostRequest(CommonPassthroughRequest):
    model_config = {
        "title": "CallPostRequest",
        "json_schema_extra": {
            "example": {
                **CommonPassthroughRequest.model_config["json_schema_extra"]["example"],
                "callback": "f(x)",
                "value": True,
            },
            "description": "Response from the CallPost API.",
        },
    }

    callback: Callable = Field(..., description="Callable callback function.")
    value: Optional[Any] = Field(
        default=None, description="The value to pass to the callback function."
    )


class CallPost(Passthrough):
    request_schema = CallPostRequest
    response_schema = PostResponseElement
    method = Method.POST

    async def execute(self) -> AsyncGenerator[Context, List[PostResponseElement]]:
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


@router.post(
    "/call_post",
    tags=["Core Operations"],
    response_model=List[PostResponseElement],
    responses={
        200: {
            "description": "Successful Response",
            "content": {
                "application/json": {
                    "example": [{"host": "server104", "error": False, "message": ""}]
                }
            },
        }
    },
)
async def call_post(
    callback: Any = Query(..., description="Callable callback function."),
    value: Optional[Any] = Query(
        default=None, description="The value to pass to the callback function."
    ),
    common: CommonPassthroughRequest = Depends(common_passthrough_request),
) -> CallPostRequest:
    """# Call a coroutine that returns a changed indication

    *This REST API cannot be called.*

    ## Python API

    - Coroutine: `CallPost`
    - Response schema: `[PostResponseElement]`

    Python API example:

    ```python
    from reemote.context import Context
    from reemote import core1

    async def callback(context: Context):
        context.changed = context.value

    responses = await execute(lambda: core1.CallPost(callback=callback, value=False, group="server104"), inventory)
    for item in responses:
        assert item.changed == False, "The callback should return its argument"
    ```
    """
    return await (router_handler1(CallPost))(
        value=value,
        callback=callback,
        common=common,
    )
