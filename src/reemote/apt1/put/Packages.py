from typing import AsyncGenerator, List, Optional, Tuple, Union

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, RootModel, ValidationError

from reemote import core
from reemote.context import Context, ContextType, Method
from reemote.operation import (
    CommonOperationRequest,
    Operation,
    common_operation_request,
)
from reemote.response import PutResponseElement
from reemote.router_handler import router_handler1

from reemote import apt1
from reemote import core


router = APIRouter()


class AptPutPackagesRequest(CommonOperationRequest):
    packages: list[str]
    present: bool

class AptPutPackagesResponse(PutResponseElement):
    request: AptPutPackagesRequest = Field(
        default=None,
        description="The request object used to execute the operation.",
    )

class AptPutPackagesResponses(RootModel):
    root: List[AptPutPackagesResponse]


class Packages(Operation):

    request = AptPutPackagesRequest
    response = AptPutPackagesResponse
    responses = AptPutPackagesResponses
    method = Method.PUT

    async def execute(self) -> AsyncGenerator[Context, AptPutPackagesResponse]:
        model_instance = self.request.model_validate(self.kwargs)
        pre = yield apt1.get.Packages()
        if model_instance.present:
            yield core.post.Command(cmd=f"apt-get install -y {' '.join(model_instance.packages)}", **self.common_kwargs)
        else:
            yield core.post.Command(cmd=f"apt-get remove -y {' '.join(model_instance.packages)}",**self.common_kwargs)
        post = yield apt1.get.Packages()

        changed = pre.value != post.value
        yield core.put.Return(changed=changed)


    @staticmethod
    @router.put(
        "/packages",
        tags=["APT Package Manager"],
        response_model=AptPutPackagesResponses,
        responses={
            # block insert examples/apt/put/Packages_responses.generated -4
            "200": {
                "description": "Successful Response",
                "content": {
                    "application/json": {
                        "example": [
                            {
                                "host": "server104",
                                "error": False,
                                "message": "",
                                "changed": False,
                                "request": {
                                    "group": None,
                                    "name": None,
                                    "changed": False
                                }
                            },
                            {
                                "host": "server105",
                                "error": False,
                                "message": "",
                                "changed": False,
                                "request": {
                                    "group": None,
                                    "name": None,
                                    "changed": False
                                }
                            }
                        ]
                    }
                }
            }
            # block end
        },
    )
    async def packages(
        common: CommonOperationRequest = Depends(common_operation_request),
    ):
        """# Manage packages

        <!-- block insert examples/apt/put/Packages_example.generated -->
        
        ## apt.put.Packages
        
        Example:
        
        ```python
        async def example(inventory):
            from reemote import apt1
            from reemote.execute import execute
        
            responses = await execute(
                lambda: apt1.put.Packages(
                    packages=["tree"],
                    present=False,
                    sudo=True,
                ),
                inventory,
            )
            return responses
        ```
        <!-- block end -->
        """
        return await (router_handler1(Packages))(
            common=common,
        )
