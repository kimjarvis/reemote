from typing import Any, AsyncGenerator, Callable, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, RootModel

from reemote.context import Context, ContextType, Method
from reemote.passthrough import (
    CommonPassthroughRequest,
    Passthrough,
    common_passthrough_request,
)
from reemote.response import PutResponseElement
from reemote.router_handler import router_handler1

router = APIRouter()

class CorePutCallRequest(CommonPassthroughRequest):
    callback: Callable
    value: Optional[Any]

class CorePutCallResponse(PutResponseElement):
    request: CorePutCallRequest = Field(
        default=None,
        description="The request object used to execute the operation.",
    )

class CorePutCallResponses(RootModel):
    root: List[CorePutCallResponse]


class Call(Passthrough):

    request = CorePutCallRequest
    response = CorePutCallResponse
    responses = CorePutCallResponses

    method = Method.PUT

    async def execute(self) -> AsyncGenerator[Context, CorePutCallResponse]:
        model_instance = self.request.model_validate(self.kwargs)

        yield Context(
            type=ContextType.PASSTHROUGH,
            value=model_instance.value,
            callback=model_instance.callback,
            method=self.method,
            request_instance=model_instance,
            response=self.response,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )


@router.put(
    "/call",
    tags=["Core Operations"],
    response_model=CorePutCallResponses,
    responses={
        # block insert examples/core/put/Call_responses.generated -4
        "200": {
            "description": "Successful Response",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "host": "server104",
                            "error": False,
                            "message": "",
                            "changed": True
                        }
                    ]
                }
            }
        }
        # block end
    },
)
async def call(
    callback: Any = Query(
        ...,
        description="Callable callback function.",
        examples=["callback"],
    ),
    value: Optional[Any] = Query(
        default=None,
        description="The value to pass to the callback function.",
        examples=["True"],
    ),
    common: CommonPassthroughRequest = Depends(common_passthrough_request),
):
    """# Call a coroutine that returns a changed indication

    *This REST API cannot be called.*

    <!-- block insert examples/core/put/Call_example.generated -->
    
    ## core.put.Call
    
    Example:
    
    ```python
    async def example(inventory):
        from reemote.execute import execute
        from reemote.context import Context
        from reemote import core
    
        async def callback(context: Context):
            # Make a change to the host
            context.changed = True
    
        responses = await execute(lambda: core.put.Call(callback=callback, value="Hello", group="server104"), inventory)
        for item in responses:
            assert item.changed == True, "Expected the coroutine to set the changed indicator"
    
        return responses
    ```
    <!-- block end -->
    """
    return await (router_handler1(Call))(
        value=value,
        callback=callback,
        common=common,
    )
