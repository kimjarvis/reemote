from typing import Any, AsyncGenerator, Callable, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, RootModel

from reemote.context import Context, ContextType, Method
from reemote.passthrough import (
    CommonPassthroughRequest,
    Passthrough,
    common_passthrough_request,
)
from reemote.response import PostResponseElement
from reemote.router_handler import router_handler1

router = APIRouter()


class CorePostCallRequest(CommonPassthroughRequest):
    callback: Callable
    value: Optional[Any]

class CorePostCallResponse(PostResponseElement):
    request: CorePostCallRequest = Field(
        default=None,
        description="The request object used to execute the operation.",
    )

class CorePostCallResponses(RootModel):
    root: List[CorePostCallResponse]


class Call(Passthrough):

    request = CorePostCallRequest
    response = CorePostCallResponse
    responses = CorePostCallResponses

    method = Method.POST

    async def execute(self) -> AsyncGenerator[Context, CorePostCallResponse]:
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

    @staticmethod
    @router.post(
        "/call",
        tags=["Core Operations"],
        response_model=CorePostCallResponses,
        responses={
            # block insert examples/core/post/Call_responses.generated -4
            "200": {
                "description": "Successful Response",
                "content": {
                    "application/json": {
                        "example": [
                            {
                                "host": "server104",
                                "error": False,
                                "message": ""
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
            examples=["'Hello'"],
        ),
        common: CommonPassthroughRequest = Depends(common_passthrough_request),
    ):
        """# Call a coroutine that returns a changed indication

        *This REST API cannot be called.*

        <!-- block insert examples/core/post/Call_example.generated -->
        
        ## core.post.Call
        
        Example:
        
        ```python
        async def example(inventory):
            from reemote.execute import execute
            from reemote.context import Context
            from reemote import core
        
            async def callback(context: Context):
                # Make a change to the host
                pass
        
            responses = await execute(lambda: core.post.Call(callback=callback, value="Hello", group="server104"), inventory)
        
            return responses
        ```
        <!-- block end -->
        """
        return await (router_handler1(Call))(
            value=value,
            callback=callback,
            common=common,
        )
