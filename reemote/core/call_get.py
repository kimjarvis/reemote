from typing import Any, AsyncGenerator, Callable, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from reemote.callback import Callback, CommonCallbackRequest, common_callback_request
from reemote.context import Context, ContextType, Method
from reemote.response import GetResponseElement
from reemote.router_handler import router_handler



router = APIRouter()


class call_get(Callback):
    class Response(GetResponseElement):
        pass


    class Request(CommonCallbackRequest):
        callback: Callable = Field(..., description="Callable callback function.")
        value: Optional[Any] = Field(
            default=None, description="The value to pass to the callback."
        )

    request_model = Request

    @staticmethod
    async def callback(context: Context) -> None:
        pass

    async def execute(self) -> AsyncGenerator[Context, List[Response]]:
        model_instance = self.request_model.model_validate(self.kwargs)

        yield Context(
            type=ContextType.PASSTHROUGH,
            value=model_instance.value,
            callback=model_instance.callback,
            method=Method.GET,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )

    @staticmethod
    @router.get(
        "/call_get",
        tags=["Core Operations"],
        response_model=List[Response],
    )
    async def call_get(
        callback: Any = Query(..., description="Callable callback function."),
        value: Optional[Any] = Query(
            default=None, description="The value to pass to the callback function."
        ),
        common: CommonCallbackRequest = Depends(common_callback_request),
    ) -> Request:
        """# Call a callback function that returns a value

        This is a python coroutine. The REST API endpoint is provided for documentation purposes, it cannot be called directly.
        """
        return await router_handler(call_get.Request, call_get)(
            value=value,
            callback=callback,
            common=common,
        )
