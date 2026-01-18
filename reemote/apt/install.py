from typing import AsyncGenerator, List

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from reemote.context import Context, ContextType, Method
from reemote.operation import (
    CommonOperationRequest,
    Operation,
    common_operation_request,
)
from reemote.response import PostResponse, PostResponseElement
from reemote.router_handler import router_handler

router = APIRouter()


class Install(Operation):
    class Request(CommonOperationRequest):
        packages: list[str] = Field(..., description="List of package names")

    class Response(PostResponseElement):
        pass

    async def execute(self) -> AsyncGenerator[Context, List[Response]]:
        model_instance = self.Request.model_validate(self.kwargs)

        result = yield Context(
            command=f"apt-get install -y {' '.join(model_instance.packages)}",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            type=ContextType.OPERATION,
            method=Method.POST,
            **self.common_kwargs,
        )
        self.Response(root=result)

    @staticmethod
    @router.post(
        "/install",
        tags=["APT Package Manager"],
        response_model=List[Response],
    )
    async def install(
        common: Request = Depends(common_operation_request),
        packages: list[str] = Query(..., description="List of package names"),
    ) -> Request:
        """# Install APT packages"""
        return await router_handler(Install.Request, Install)(
            common=common, packages=packages
        )
