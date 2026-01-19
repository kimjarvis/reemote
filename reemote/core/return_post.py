from typing import AsyncGenerator, List

from fastapi import APIRouter, Depends

from reemote.callback import Callback, CommonCallbackRequest, common_callback_request
from reemote.context import Context, ContextType, Method
from reemote.response import PostResponseElement
from reemote.router_handler import router_handler

router = APIRouter()


class return_post(Callback):
    class Response(PostResponseElement):
        pass

    class Request(CommonCallbackRequest):
        pass

    request_model = Request

    @staticmethod
    async def callback(context: Context) -> None:
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
    @router.post(
        "/return_post",
        tags=["Core Operations"],
        response_model=Response,
    )
    async def return_post(
        common: CommonCallbackRequest = Depends(common_callback_request),
    ) -> Request:
        """# Return without value or changed indication"""
        return await router_handler(return_post.Request, return_post)(
            common=common,
        )
