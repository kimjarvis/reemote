from typing import List, AsyncGenerator, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import Field

from reemote.callback import Callback, CommonCallbackRequest
from reemote.context import ContextType, Context, Method
from reemote.response import PutResponse
from reemote.router_handler import router_handler
from reemote.callback import common_callback_request

router = APIRouter()


class ReturnPut(Callback):
    class Response(PutResponse):
        pass

    class Request(CommonCallbackRequest):
        changed: Optional[bool] = Field(
            default=None, description="Whether the operation changed the host."
        )

    request_model = Request

    @staticmethod
    async def callback(context: Context) -> None:
        pass

    async def execute(self) -> AsyncGenerator[Context, List[Response]]:
        model_instance = self.request_model.model_validate(self.kwargs)

        yield Context(
            type=ContextType.PASSTHROUGH,
            method=Method.PUT,
            changed=model_instance.changed,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )

    @staticmethod
    @router.put("/putreturn", tags=["Core Operations"], response_model=Response)
    async def putreturn(
        changed: Optional[bool] = Query(
            default=None, description="Whether the operation changed the host."
        ),
        common: CommonCallbackRequest = Depends(common_callback_request),
    ) -> Request:
        """# Return a changed indication"""
        # todo: This is always a return from put, it has "changed"
        return await router_handler(ReturnPut.Request, ReturnPut)(
            changed=changed,
            common=common,
        )
