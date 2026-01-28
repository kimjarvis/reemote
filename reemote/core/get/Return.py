from typing import Any, AsyncGenerator, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, RootModel

from reemote.context import Context
from reemote.context import ContextType, Method
from reemote.passthrough import (
    CommonPassthroughRequest,
    Passthrough,
    common_passthrough_request,
)
from reemote.response import GetResponseElement
from reemote.router_handler import router_handler1

router = APIRouter()


class CoreGetReturnRequest(CommonPassthroughRequest):
    value: Any = None

class CoreGetReturnResponse(GetResponseElement):
    request: CoreGetReturnRequest = Field(
        default=None,
        description="The request object used to execute the operation.",
    )

class CoreGetReturnResponses(RootModel):
    root: List[CoreGetReturnResponse]

class Return(Passthrough):

    request = CoreGetReturnRequest
    response = CoreGetReturnResponse
    responses = CoreGetReturnResponses
    method = Method.GET

    @classmethod
    async def callback(cls, context: Context) -> None:
        context.response = cls.response
        context.method = cls.method
        return context.value

    async def execute(self) -> AsyncGenerator[Context, CoreGetReturnResponse]:
        model_instance = self.request.model_validate(self.kwargs)

        yield Context(
            type=ContextType.PASSTHROUGH,
            value=model_instance.value,
            callback=self.callback,
            method=self.method,
            request_instance=model_instance,
            response=self.response,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )

    @staticmethod
    @router.get(
        "/return",
        tags=["Core Operations"],
        response_model=CoreGetReturnResponses,
        responses={
            # block insert examples/core/get/Return_responses.generated -4
            "200": {
                "description": "Successful Response",
                "content": {
                    "application/json": {
                        "example": [
                            {
                                "host": "server104",
                                "error": False,
                                "message": "",
                                "value": 1
                            },
                            {
                                "host": "server105",
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
    ):
        """# Return the operational context

        <!-- block insert examples/core/get/Return_example.generated -->
        
        ## core.get.Return
        
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
