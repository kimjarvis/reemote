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
from reemote.router_handler import router_handler
from reemote.sftp1.common import PathRequest

router = APIRouter()


class is_dir(Callback):
    class Request(PathRequest):
        pass

    class Response(GetResponseElement):
        value: bool = Field(
            default=False,
            description="Whether or not the path is a directory.",
        )

    request_model = Request
    response_model = Response

    @staticmethod
    async def callback(context: Context) -> None:
        async with asyncssh.connect(
            **context.inventory_item.connection.to_json_serializable()
        ) as conn:
            async with conn.start_sftp_client() as sftp:
                context.changed = False
                context.method = Method.GET
                return await sftp.isdir(context.caller.path)

    @staticmethod
    @router.get(
        "/is_dir",
        tags=["SFTP Operations"],
        response_model=List[Response],
    )
    async def is_dir(
        path: Union[PurePath, str, bytes] = Query(
            ..., description="Path to check if it's a directory"
        ),
        common: CommonCallbackRequest = Depends(common_callback_request),
    ) -> Request:
        """# Return if the remote path refers to a directory"""
        return await router_handler(is_dir.Request, is_dir)(path=path, common=common)
