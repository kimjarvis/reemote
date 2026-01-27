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



class Packages(Operation):
    class Request(CommonOperationRequest):
        packages: list[str]
        present: bool

    request_schema = Request
    response_schema = PutResponseElement
    method = Method.PUT

    async def execute(self) -> AsyncGenerator[Context, List[PutResponseElement]]:
        model_instance = self.request_schema.model_validate(self.kwargs)
        pre = yield apt1.get.Packages()
        if model_instance.present:
            yield core.post.Command(cmd=f"apt-get install -y {' '.join(model_instance.packages)}", **self.common_kwargs)
        else:
            yield core.post.Command(cmd=f"apt-get remove -y {' '.join(model_instance.packages)}",**self.common_kwargs)
        post = yield apt1.get.Packages()

        print(post.value)
        changed = pre.value != post.value
        yield core.put.Return(changed=changed)


    @staticmethod
    @router.put(
        "/packages",
        tags=["APT Package Manager"],
        response_model=List[PutResponseElement],
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
                                "changed": False
                            },
                            {
                                "host": "server105",
                                "error": False,
                                "message": "",
                                "changed": False
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
    ) -> Request:
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
