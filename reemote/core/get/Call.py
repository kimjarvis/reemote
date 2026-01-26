from typing import Any, AsyncGenerator, Callable, List, Optional

from fastapi import APIRouter, Depends, Query

from reemote.context import Context, ContextType, Method
from reemote.passthrough import (
    CommonPassthroughRequest,
    Passthrough,
    common_passthrough_request,
)
from reemote.response import GetResponseElement
from reemote.router_handler import router_handler1

router = APIRouter()


class Call(Passthrough):
    class Request(CommonPassthroughRequest):
        callback: Callable
        value: Optional[Any]

    request_schema = Request
    response_schema = GetResponseElement
    method = Method.GET

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

    @staticmethod
    @router.get(
        "/call",
        tags=["Core Operations"],
        response_model=List[GetResponseElement],
        responses={
            # block insert examples/core/get/Call_responses.generated -4
            "200": {
                "description": "Successful Response",
                "content": {
                    "application/json": {
                        "example": [
                            {
                                "host": "server108",
                                "error": False,
                                "message": "",
                                "value": "Hello World!"
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
    ) -> Request:
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
        
            responses = await execute(lambda: core.get.Call(callback=callback, value="Hello ", group="server108"), inventory)
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
