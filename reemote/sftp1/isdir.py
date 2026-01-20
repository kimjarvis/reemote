from pathlib import PurePath
from typing import List, Union

import asyncssh
from fastapi import APIRouter, Depends, Query
from pydantic import (
    Field,
)

from reemote.callback import Callback, CommonCallbackRequest, common_callback_request
from reemote.context import Context, Method
from reemote.response import GetResponseElement
from reemote.router_handler import router_handler1
from reemote.sftp1.common import PathRequest

router = APIRouter()


class Is_dirResponse(GetResponseElement):
    value: bool = Field(
        default=False,
        description="Whether or not the path is a directory.",
    )

class Is_dir(Callback):
    request_schema = PathRequest
    response_schema = Is_dirResponse
    method = Method.GET

    @classmethod
    async def callback(cls, context: Context) -> None:
        context.response_schema=cls.response_schema
        context.method = cls.method
        async with asyncssh.connect(
            **context.inventory_item.connection.to_json_serializable()
        ) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.isdir(context.caller.path)

@router.get(
    "/is_dir",
    tags=["SFTP Operations"],
    response_model=List[Is_dirResponse],
)
async def is_dir(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Path to check if it's a directory"
    ),
    common: CommonCallbackRequest = Depends(common_callback_request),
) -> PathRequest:
    """# Return if the remote path refers to a directory"""
    return await (router_handler1(Is_dir))(path=path, common=common)
