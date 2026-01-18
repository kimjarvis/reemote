from typing import Any, AsyncGenerator, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from reemote.callback import Callback, CommonCallbackRequest, common_callback_request
from reemote.context import Context, ContextType, Method
from reemote.response import PutResponse
from reemote.router_handler import router_handler

router = APIRouter()


class GetReturn(Callback):
    class Response(PutResponse):
        pass

    class Request(CommonCallbackRequest):
        value: Optional[Any] = Field(default=None, description="The value to return.")

    request_model = Request

    @staticmethod
    async def callback(context: Context) -> None:
        pass

    async def execute(self) -> AsyncGenerator[Context, List[Response]]:
        model_instance = self.request_model.model_validate(self.kwargs)

        yield Context(
            type=ContextType.PASSTHROUGH,
            method=Method.GET,
            value=model_instance.value,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )

    @staticmethod
    @router.get(
        "/getreturn",
        tags=["Core Operations"],
        response_model=List[Response],
    )
    async def getreturn(
        value: Optional[Any] = Query(default=None, description="The value to return."),
        common: CommonCallbackRequest = Depends(common_callback_request),
    ) -> Request:
        """# Return a value"""
        return await router_handler(GetReturn.Request, GetReturn)(
            value=value,
            common=common,
        )
