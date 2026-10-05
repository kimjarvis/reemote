from typing import Any, AsyncGenerator, Callable, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, RootModel

from reemote.context import Context, ContextType, Method
from reemote.passthrough import (
    CommonPassthroughRequest,
    Passthrough,
    common_passthrough_request,
)
from reemote.response import GetResponseElement
from reemote.router_handler import router_handler1

router = APIRouter()


class CoreGetCallRequest(CommonPassthroughRequest):
    callback: Callable
    value: Optional[Any]

class CoreGetCallResponse(GetResponseElement):
    request: CoreGetCallRequest = Field(
        default=None,
        description="The request object used to execute the operation.",
    )

class CoreGetCallResponses(RootModel):
    root: List[CoreGetCallResponse]

class Call(Passthrough):

    request = CoreGetCallRequest
    response = CoreGetCallResponse
    responses = CoreGetCallResponses
    method = Method.GET

    async def execute(self) -> AsyncGenerator[Context, CoreGetCallResponse]:
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
    @router.get(
        "/call",
        tags=["Core Operations"],
        response_model=CoreGetCallResponses,
        responses={
            # block insert examples/core/get/Call_responses.generated -4
            "200": {
                "description": "Successful Response",
                "content": {
                    "application/json": {
                        "example": [
                            {
                                "host": "server104",
                                "error": False,
                                "message": "",
                                "value": "Hello World!",
                                "request": {
                                    "group": "server104",
                                    "name": None,
                                    "callback": "callback",
                                    "value": "Hello "
                                }
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
            examples=["Hello"],
        ),
        common: CommonPassthroughRequest = Depends(common_passthrough_request),
    ):
        """# Call a coroutine that returns value

        *This REST API cannot be called.*

        <!-- block insert examples/core/get/Call_example.generated -->
        
        ## core.get.Call
        
        Example:
        
        ```python
        async def example(inventory):
            from reemote.execute import execute
            from reemote.context import Context
            from reemote import core
        
            async def callback(context: Context):
                return context.value + "World!"
        
            responses = await execute(lambda: core.get.Call(callback=callback, value="Hello ", group="server104"), inventory)
            for item in responses:
                assert item.value == "Hello World!", "Expected the coroutine to yield 'World!' appended to the input value"
        
            return responses
        ```
        <!-- block end -->
        """
        return await (router_handler1(Call))(
            value=value,
            callback=callback,
            common=common,
        )
