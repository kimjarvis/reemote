from typing import AsyncGenerator, List, Any, Optional

from fastapi import APIRouter, Depends, Query

from reemote.context import Context as _Context
from reemote.context import ContextType, Method
from reemote.passthrough import (
    CommonPassthroughRequest,
    Passthrough,
    common_passthrough_request,
)
from reemote.response import PostResponseElement
from reemote.router_handler import router_handler1

router = APIRouter()


class Return(Passthrough):
    class Request(CommonPassthroughRequest):
        pass

    request_schema = Request
    response_schema = PostResponseElement
    method = Method.POST

    @classmethod
    async def callback(cls, context: _Context) -> None:
        context.response_schema = cls.response_schema
        context.method = cls.method
        return context.value

    async def execute(self) -> AsyncGenerator[_Context, List[PostResponseElement]]:
        model_instance = self.request_schema.model_validate(self.kwargs)

        yield _Context(
            type=ContextType.PASSTHROUGH,
            callback=self.callback,
            method=self.method,
            response_schema=self.response_schema,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )

    @staticmethod
    @router.post(
        "/return",
        tags=["Core Operations"],
        response_model=List[PostResponseElement],
        responses={
            # block insert examples/core/post/Return_responses.generated -4
            "200": {
                "description": "Successful Response",
                "content": {
                    "application/json": {
                        "example": [
                            {
                                "host": "server104",
                                "error": False,
                                "message": ""
                            },
                            {
                                "host": "server105",
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
    async def _return(
        common: CommonPassthroughRequest = Depends(common_passthrough_request),
    ) -> Request:
        """# Return the operational context

        <!-- block insert examples/core/post/Return_example.generated -->
        
        ## core.post.Return()
        
        Example:
        
        ```python
        async def example(inventory):
            from reemote import core1
            from reemote.context import Context
            from reemote.execute import execute
        
            responses = await execute(lambda: core1.post.Return(), inventory)
        
            return responses
        ```
        <!-- block end -->
        """
        return await (router_handler1(Return))(
            common=common,
        )
