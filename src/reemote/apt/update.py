from typing import AsyncGenerator, List

from fastapi import APIRouter, Depends

from reemote.context import Context, Method, ContextType
from reemote.operation import (
    Operation,
    CommonOperationRequest,
    common_operation_request,
)
from reemote.response import PostResponseElement
from reemote.router_handler import router_handler

router = APIRouter()


class Update(Operation):
    class Request(CommonOperationRequest):
        pass

    class Response(PostResponseElement):
        pass

    async def execute(self) -> AsyncGenerator[Context, List[Response]]:
        model_instance = self.Request.model_validate(self.kwargs)

        result = yield Context(
            command="apt-get update",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            type=ContextType.OPERATION,
            method=Method.POST,
            **self.common_kwargs,
        )
        self.Response(root=result)

    @router.post(
        "/update",
        tags=["APT Package Manager"],
        response_model=List[Response],
    )
    async def update(
        common: CommonOperationRequest = Depends(common_operation_request),
    ) -> CommonOperationRequest:
        """# Update APT packages"""
        return await router_handler(Update.Request, Update)(common=common)
