from typing import AsyncGenerator, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, RootModel

from reemote.context import Context as _Context
from reemote.context import ContextType, Method
from reemote.passthrough import (
    CommonPassthroughRequest,
    Passthrough,
    common_passthrough_request,
)
from reemote.response import GetResponseElement
from reemote.router_handler import router_handler1

router = APIRouter()


class CoreGetContextRequest(CommonPassthroughRequest):
    pass

class CoreGetContextResponse(GetResponseElement):
    value: _Context = None
    request: CoreGetContextRequest = Field(
        default=None,
        description="The request object used to execute the operation.",
    )

class CoreGetContextResponses(RootModel):
    root: List[CoreGetContextResponse]


class Context(Passthrough):

    request = CoreGetContextRequest
    response = CoreGetContextResponse
    responses = CoreGetContextResponses
    method = Method.GET

    @classmethod
    async def callback(cls, context: _Context) -> None:
        context.response = cls.response
        context.method = cls.method
        return context

    async def execute(self) -> AsyncGenerator[_Context, CoreGetContextResponse]:
        model_instance = self.request.model_validate(self.kwargs)

        yield _Context(
            type=ContextType.PASSTHROUGH,
            callback=self.callback,
            method=self.method,
            response=self.response,
            request_instance=model_instance,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )

    @staticmethod
    @router.get(
        "/context",
        tags=["Core Operations"],
        response_model=CoreGetContextResponses,
        responses={
            # block insert examples/core/get/Context_responses.generated -4
            "200": {
                "description": "Successful Response",
                "content": {
                    "application/json": {
                        "example": [
                            {
                                "host": "server104",
                                "error": False,
                                "message": "",
                                "value": {
                                    "group": None,
                                    "name": None,
                                    "sudo": False,
                                    "su": False,
                                    "inventory_item": {
                                        "connection": {
                                            "host": "server104",
                                            "username": "user",
                                            "password": "password"
                                        },
                                        "authentication": {
                                            "sudo_password": "password",
                                            "su_user": "",
                                            "su_password": ""
                                        },
                                        "session": {},
                                        "groups": [
                                            "server104"
                                        ]
                                    }
                                },
                                "request": {
                                    "group": None,
                                    "name": None
                                }
                            },
                            {
                                "host": "server105",
                                "error": False,
                                "message": "",
                                "value": {
                                    "group": None,
                                    "name": None,
                                    "sudo": False,
                                    "su": False,
                                    "inventory_item": {
                                        "connection": {
                                            "host": "server105",
                                            "username": "user",
                                            "password": "password"
                                        },
                                        "authentication": {
                                            "sudo_password": "password",
                                            "su_user": "",
                                            "su_password": ""
                                        },
                                        "session": {},
                                        "groups": [
                                            "server105"
                                        ]
                                    }
                                },
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
    async def context(
        common: CommonPassthroughRequest = Depends(common_passthrough_request),
    ):
        """# Return the operational context

        <!-- block insert examples/core/get/Context_example.generated -->
        
        ## core.get.Context
        
        Example:
        
        ```python
        async def example(inventory):
            from reemote.execute import execute
            from reemote.context import Context
            from reemote import core
        
            responses = await execute(lambda: core.get.Context(), inventory)
        
            for response in responses:
                assert response.host in ["server104", "server105"]
        
            return responses
        ```
        <!-- block end -->
        """
        return await (router_handler1(Context))(
            common=common,
        )
