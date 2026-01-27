from typing import AsyncGenerator, List

from fastapi import APIRouter, Depends

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


class ContextResponse(GetResponseElement):
    value: _Context = None


class Context(Passthrough):
    class Request(CommonPassthroughRequest):
        pass

    request_schema = Request
    response_schema = ContextResponse
    method = Method.GET

    @classmethod
    async def callback(cls, context: _Context) -> None:
        context.response = cls.response_schema
        context.method = cls.method
        return context

    async def execute(self) -> AsyncGenerator[_Context, List[GetResponseElement]]:
        model_instance = self.request_schema.model_validate(self.kwargs)

        yield _Context(
            type=ContextType.PASSTHROUGH,
            callback=self.callback,
            method=self.method,
            response=self.response_schema,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )

    @staticmethod
    @router.get(
        "/context",
        tags=["Core Operations"],
        response_model=List[ContextResponse],
        responses={
            # block insert examples/core/get/Context_responses.generated -4
            "200": {
                "description": "Successful Response",
                "content": {
                    "application/json": {
                        "example": [
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
                                            "sudo_password": "",
                                            "su_user": "",
                                            "su_password": ""
                                        },
                                        "session": {},
                                        "groups": [
                                            "server105"
                                        ]
                                    }
                                }
                            },
                            {
                                "host": "server108",
                                "error": False,
                                "message": "",
                                "value": {
                                    "group": None,
                                    "name": None,
                                    "sudo": False,
                                    "su": False,
                                    "inventory_item": {
                                        "connection": {
                                            "host": "server108",
                                            "username": "user",
                                            "password": "password"
                                        },
                                        "authentication": {
                                            "sudo_password": "",
                                            "su_user": "",
                                            "su_password": ""
                                        },
                                        "session": {},
                                        "groups": [
                                            "server108"
                                        ]
                                    }
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
    ) -> Request:
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
                assert response.host in ["server108", "server105"]
        
            return responses
        ```
        <!-- block end -->
        """
        return await (router_handler1(Context))(
            common=common,
        )
