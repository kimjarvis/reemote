from typing import AsyncGenerator, List, Optional, Tuple, Union

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from reemote.callback import (
    Callback,
    CommonCallbackRequestModel,
    common_callback_request,
)
from reemote.context import Context
from reemote.operation import (
    CommonOperationRequestModel,
)
from reemote.response import GetResponseModel
from reemote.router_handler import router_handler

router = APIRouter()


class Getcontext(Callback):
    request_model = CommonOperationRequestModel

    @staticmethod
    async def callback(context: Context) -> None:
        return context


@router.get(
    "/getcontext",
    tags=["Core Operations"],
    response_model=GetResponseModel,
)
async def get_context(
    common: CommonCallbackRequestModel = Depends(common_callback_request),
) -> BaseModel:
    return await router_handler(CommonOperationRequestModel, Getcontext)(common=common)
