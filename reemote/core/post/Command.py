from typing import AsyncGenerator, List, Optional, Tuple, Union

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, RootModel

from reemote.context import Context, ContextType, Method
from reemote.operation import (
    CommonOperationRequest,
    Operation,
    common_operation_request,
)
from reemote.response import PostResponseElement
from reemote.router_handler import router_handler1

router = APIRouter()


class CorePostCommandRequest(CommonOperationRequest):
    cmd: str = Field(...)


class CorePostCommandResponse(PostResponseElement):
    request: CorePostCommandRequest = Field(
        default=None,
        description="The request object used to execute the operation.",
    )


class CorePostCommandResponses(RootModel):
    root: List[CorePostCommandResponse]


class Command(Operation):
    request = CorePostCommandRequest
    response = CorePostCommandResponse
    responses = CorePostCommandResponses

    method = Method.POST

    async def execute(self) -> AsyncGenerator[Context, CorePostCommandResponse]:
        model_instance = self.request.model_validate(self.kwargs)
        result = yield Context(
            command=model_instance.cmd,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            type=ContextType.OPERATION,
            method=self.method,
            request_instance=model_instance,
            response=self.response,
            **self.common_kwargs,
        )
        self.__class__.response(root=result)

    @staticmethod
    @router.post(
        "/command",
        tags=["Core Operations"],
        response_model=List[PostResponseElement],
        responses={
            # block insert examples/core/post/Command_responses.generated -4
            "200": {
                "description": "Successful Response",
                "content": {
                    "application/json": {
                        "example": [
                            {"host": "server104", "error": False, "message": ""},
                            {"host": "server105", "error": False, "message": ""},
                        ]
                    }
                },
            },
            # block end
            # block insert scripts/boilerplate/operation_error_examples.txt
        },
    )
    async def command(
        cmd: str = Query(
            ..., description="Shell command", examples=["systemctl start firewalld"]
        ),
        common: CommonOperationRequest = Depends(common_operation_request),
    ):
        """# Execute a shell command on the remote host

        <!-- block insert examples/core/post/Command_example.generated -->

        ## core.post.Command

        Example:

        ```python
        async def example(inventory):
            from reemote.execute import execute
            from reemote import core

            responses = await execute(lambda: core.post.Command(cmd='systemctl start firewalld'), inventory)

            return responses
        ```
        <!-- block end -->
        """
        return await (router_handler1(Command))(
            cmd=cmd,
            common=common,
        )
