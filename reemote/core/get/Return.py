from typing import AsyncGenerator, List, Any, Optional

from fastapi import APIRouter, Depends, Query

from reemote.context import Context as _Context
from reemote.context import ContextType, Method
from reemote.passthrough import (
    CommonPassthroughRequest,
    Passthrough,
    common_passthrough_request,
)
from reemote.response import GetResponseElement
from reemote.router_handler import router_handler1

router = APIRouter()


class Return(Passthrough):
    class Request(CommonPassthroughRequest):
        value: Any = None

    request_schema = Request
    response_schema = GetResponseElement
    method = Method.GET

    @classmethod
    async def callback(cls, context: _Context) -> None:
        context.response_schema = cls.response_schema
        context.method = cls.method
        return context.value

    async def execute(self) -> AsyncGenerator[_Context, List[GetResponseElement]]:
        model_instance = self.request_schema.model_validate(self.kwargs)

        yield _Context(
            type=ContextType.PASSTHROUGH,
            value=model_instance.value,
            callback=self.callback,
            method=self.method,
            response_schema=self.response_schema,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )

    @staticmethod
    @router.get(
        "/return",
        tags=["Core Operations"],
        response_model=List[GetResponseElement],
        responses={
            # block insert examples/core/get/Return_responses.generated -4
            "200": {
                "description": "Successful Response",
                "content": {
                    "application/json": {
                        "example": [
                            {
                                "host": "server105",
                                "error": False,
                                "message": "",
                                "value": 1
                            },
                            {
                                "host": "server104",
                                "error": False,
                                "message": "",
                                "value": 1
                            }
                        ]
                    }
                }
            }
            # block end
        },
    )
    async def _return(
        value: Any = Query(..., description="The value to return."),
        common: CommonPassthroughRequest = Depends(common_passthrough_request),
    ) -> Request:
        """# Return the operational context

        <!-- block insert examples/core/get/Return_example.generated -->
        
        ## core.get.Return()
        
        Example:
        
        ```python
        async def example(inventory):
            from reemote import core
            from reemote.context import Context
            from reemote.execute import execute
        
            responses = await execute(lambda: core.get.Return(value=1), inventory)
            assert all(response.value == 1 for response in responses), "Expected the coroutine to return the value"
        
            return responses
        ```
        <!-- block end -->
        """
        return await (router_handler1(Return))(
            value=value,
            common=common,
        )
