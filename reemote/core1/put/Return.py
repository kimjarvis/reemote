from typing import AsyncGenerator, List, Any, Optional

from fastapi import APIRouter, Depends, Query

from reemote.context import Context as _Context
from reemote.context import ContextType, Method
from reemote.passthrough import (
    CommonPassthroughRequest,
    Passthrough,
    common_passthrough_request,
)
from reemote.response import PutResponseElement
from reemote.router_handler import router_handler1

router = APIRouter()


class Return(Passthrough):
    class Request(CommonPassthroughRequest):
        changed: bool = None

    request_schema = Request
    response_schema = PutResponseElement
    method = Method.PUT

    @classmethod
    async def callback(cls, context: _Context) -> None:
        context.response_schema = cls.response_schema
        context.method = cls.method
        return

    async def execute(self) -> AsyncGenerator[_Context, List[PutResponseElement]]:
        model_instance = self.request_schema.model_validate(self.kwargs)

        yield _Context(
            type=ContextType.PASSTHROUGH,
            changed=model_instance.changed,
            callback=self.callback,
            method=self.method,
            response_schema=self.response_schema,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )

    @staticmethod
    @router.put(
        "/return",
        tags=["Core Operations"],
        response_model=List[PutResponseElement],
        responses={
            # block insert examples/core/put/Return_responses.generated -4
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
                            },
                            {
                                "host": "server105",
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
    async def _return(
        changed: bool = Query(..., description="Whether or not the operation changed the host."),
        common: CommonPassthroughRequest = Depends(common_passthrough_request),
    ) -> Request:
        """# Return the operational context

        <!-- block insert examples/core/put/Return_example.generated -->
        
        ## core.put.Return()
        
        Example:
        
        ```python
        async def example(inventory):
            from reemote import core1
            from reemote.context import Context
            from reemote.execute import execute
        
            responses = await execute(lambda: core1.put.Return(changed=True), inventory)
            assert all(response.changed for response in responses), "Expected the coroutine to return with changed equal to True"
        
            return responses
        ```
        <!-- block end -->
        """
        return await (router_handler1(Return))(
            changed=changed,
            common=common,
        )
