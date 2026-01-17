from pathlib import PurePath
from typing import List, Union

import asyncssh
from fastapi import APIRouter, Depends, Query
from pydantic import (
    Field,
    RootModel,
)
from reemote.context import Context, Method
from reemote.callback import Callback
from reemote.callback import CommonCallbackRequestModel, common_callback_request
from reemote.response import GetResponseElement
from reemote.router_handler import router_handler
from reemote.sftp1.common import PathRequestModel

router = APIRouter()


class IsdirResponseElement(GetResponseElement):
    value: bool = Field(
        default=False,
        description="Whether or not the path is a directory.",
    )

# todo: move into class
class IsdirResponse(RootModel):
    root: List[IsdirResponseElement]

class Isdir(Callback):
    request_model = PathRequestModel
    response_model = IsdirResponseElement

    @staticmethod
    async def callback(context: Context) -> None:
        async with asyncssh.connect(
            **context.inventory_item.connection.to_json_serializable()
        ) as conn:
            async with conn.start_sftp_client() as sftp:
                context.changed = False
                context.method = Method.GET
                return await sftp.isdir(context.caller.path)


@router.get("/isdir", tags=["SFTP Operations"], response_model=IsdirResponse)
async def isdir(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Path to check if it's a directory"
    ),
    common: CommonCallbackRequestModel = Depends(common_callback_request),
) -> PathRequestModel:
    """# Return if the remote path refers to a directory"""
    return await router_handler(PathRequestModel, Isdir)(path=path, common=common)
