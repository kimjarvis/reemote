from typing import Any, AsyncGenerator, Callable, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from reemote.passthrough import Passthrough, CommonPassthroughRequest, common_passthrough_request
from reemote.context import Context, ContextType, Method
from reemote.response import GetResponseElement, GetResponse
from reemote.router_handler import router_handler

router = APIRouter()


class call_get(Passthrough):
    class Request(CommonPassthroughRequest):
        callback: Callable = Field(..., description="Callable callback function.")
        value: Optional[Any] = Field(
            default=None, description="The value to pass to the callback function."
        )

    async def execute(self) -> AsyncGenerator[Context, List[GetResponseElement]]:
        model_instance = self.Request.model_validate(self.kwargs)

        yield Context(
            type=ContextType.PASSTHROUGH,
            value=model_instance.value,
            callback=model_instance.callback,
            method=Method.GET,
            response_type=GetResponseElement,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )

    @staticmethod
    @router.get(
        "/call_get",
        tags=["Core Operations"],
        response_model=List[GetResponseElement],
    )
    async def call_get(
        callback: Any = Query(..., description="Callable callback function."),
        value: Optional[Any] = Query(
            default=None, description="The value to pass to the callback function."
        ),
        common: CommonPassthroughRequest = Depends(common_passthrough_request),
    ) -> Request:
        """# Call a callback function that returns a value

        This is a python coroutine. The REST API endpoint is provided for documentation purposes, it cannot be called directly.
        """
        return await router_handler(call_get.Request, call_get)(
            value=value,
            callback=callback,
            common=common,
        )
