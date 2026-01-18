from typing import List, AsyncGenerator
from fastapi import APIRouter, Depends


from reemote.callback import Callback, CommonCallbackRequest
from reemote.context import ContextType, Context, Method
from reemote.response import PostResponse
from reemote.router_handler import router_handler
from reemote.callback import common_callback_request

router = APIRouter()


class ReturnPut(Callback):
    class Response(PostResponse):
        pass

    class Request(CommonCallbackRequest):
        pass

    request_model = Request

    @staticmethod
    async def callback(context: Context) -> None:
        # dummy  callback # todo: this should be passthrough
        pass

    async def execute(self) -> AsyncGenerator[Context, List[Response]]:
        model_instance = self.request_model.model_validate(self.kwargs)

        yield Context(
            type=ContextType.PASSTHROUGH,
            method=Method.POST,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )

    @staticmethod
    @router.post("/return", tags=["Core Operations"], response_model=Response)
    async def postreturn(
        common: CommonCallbackRequest = Depends(common_callback_request),
    ) -> Request:
        """# Return"""
        return await router_handler(ReturnPut.Request, ReturnPut)(
            common=common,
        )
