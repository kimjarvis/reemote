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
from reemote.response import PutResponseElement
from reemote.router_handler import router_handler1

router = APIRouter()


class CorePutReturnRequest(CommonPassthroughRequest):
    changed: bool = None

class CorePutReturnResponse(PutResponseElement):
    request: CorePutReturnRequest = Field(
        default=None,
        description="The request object used to execute the operation.",
    )

class CorePutReturnResponses(RootModel):
    root: List[CorePutReturnResponse]


class Return(Passthrough):

    request = CorePutReturnRequest
    response = CorePutReturnResponse
    responses = CorePutReturnResponses
    method = Method.PUT

    @classmethod
    async def callback(cls, context: Context) -> None:
        context.response = cls.response
        context.method = cls.method
        return

    async def execute(self) -> AsyncGenerator[Context, CorePutReturnResponse]:
        model_instance = self.request.model_validate(self.kwargs)

        yield Context(
            type=ContextType.PASSTHROUGH,
            changed=model_instance.changed,
            callback=self.callback,
            method=self.method,
            request_instance=model_instance,
            response=self.response,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )

    @staticmethod
    @router.put(
        "/return",
        tags=["Core Operations"],
        response_model=CorePutReturnResponses,
        responses={
            # block insert examples/core/put/Return_responses.generated -4
            "200": {
                "description": "Successful Response",
                "content": {
                    "application/json": {
                        "example": [
                            {
                                "host": "server105",
                                "error": False,
                                "message": "",
                                "changed": True,
                                "request": {
                                    "group": None,
                                    "name": None,
                                    "changed": True
                                }
                            },
                            {
                                "host": "server104",
                                "error": False,
                                "message": "",
                                "changed": True,
                                "request": {
                                    "group": None,
                                    "name": None,
                                    "changed": True
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
        changed: bool = Query(..., description="Whether or not the operation changed the host."),
        common: CommonPassthroughRequest = Depends(common_passthrough_request),
    ):
        """# Return the operational context

        <!-- block insert examples/core/put/Return_example.generated -->
        
        ## core.put.Return
        
        Example:
        
        ```python
        async def example(inventory):
            from reemote import core
            from reemote.context import Context
            from reemote.execute import execute
        
            responses = await execute(lambda: core.put.Return(changed=True), inventory)
            assert all(response.changed for response in responses), "Expected the coroutine to return with changed equal to True"
        
            return responses
        ```
        <!-- block end -->
        """
        return await (router_handler1(Return))(
            changed=changed,
            common=common,
        )
