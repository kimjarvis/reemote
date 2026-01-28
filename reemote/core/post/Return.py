from typing import AsyncGenerator, List, Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, RootModel

from reemote.context import Context
from reemote.context import ContextType, Method
from reemote.passthrough import (
    CommonPassthroughRequest,
    Passthrough,
    common_passthrough_request,
)
from reemote.response import PostResponseElement
from reemote.router_handler import router_handler1

router = APIRouter()


class CorePostReturnRequest(CommonPassthroughRequest):
    pass

class CorePostReturnResponse(PostResponseElement):
    request: CorePostReturnRequest = Field(
        default=None,
        description="The request object used to execute the operation.",
    )

class CorePostReturnResponses(RootModel):
    root: List[CorePostReturnResponse]


class Return(Passthrough):

    request = CorePostReturnRequest
    response = CorePostReturnResponse
    responses = CorePostReturnResponses
    method = Method.POST

    @classmethod
    async def callback(cls, context: Context) -> None:
        context.response = cls.response
        context.method = cls.method
        return context.value

    async def execute(self) -> AsyncGenerator[Context, CorePostReturnResponse]:
        model_instance = self.request.model_validate(self.kwargs)

        yield Context(
            type=ContextType.PASSTHROUGH,
            callback=self.callback,
            method=self.method,
            request_instance=model_instance,
            response=self.response,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )

    @staticmethod
    @router.post(
        "/return",
        tags=["Core Operations"],
        response_model=CorePostReturnResponses,
        responses={
            # block insert examples/core/post/Return_responses.generated -4
            "200": {
                "description": "Successful Response",
                "content": {
                    "application/json": {
                        "example": [
                            {
                                "host": "server105",
                                "error": False,
                                "message": "",
                                "request": {
                                    "group": None,
                                    "name": None
                                }
                            },
                            {
                                "host": "server104",
                                "error": False,
                                "message": "",
                                "request": {
                                    "group": None,
                                    "name": None
                                }
                            }
                        ]
                    }
                }
            }
            # block end
        },
    )
    async def _return(
        common: CommonPassthroughRequest = Depends(common_passthrough_request),
    ):
        """# Return the operational context

        <!-- block insert examples/core/post/Return_example.generated -->
        
        ## core.post.Return
        
        Example:
        
        ```python
        async def example(inventory):
            from reemote import core
            from reemote.context import Context
            from reemote.execute import execute
        
            responses = await execute(lambda: core.post.Return(), inventory)
        
            return responses
        ```
        <!-- block end -->
        """
        return await (router_handler1(Return))(
            common=common,
        )
