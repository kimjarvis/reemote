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


class IsDirResponse(GetResponseElement):
    value: bool = Field(
        default=False,
        description="Whether or not the path is a directory.",
    )

    class Config:
        title = "IsDirResponse"
        json_schema_extra = {
            "example": {
                **GetResponseElement.model_config["json_schema_extra"]["example"],
            },
            "description": "Response from the is_dir endpoint.",
        }


class IsDir(Callback):
    class Request(PathRequest):
        pass

    request_schema = Request
    response_schema = IsDirResponse
    method = Method.GET

    @classmethod
    async def callback(cls, context: Context) -> None:
        context.response_schema = cls.response_schema
        context.method = cls.method
        async with asyncssh.connect(
            **context.inventory_item.connection.to_json_serializable()
        ) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.isdir(context.caller.path)

    @staticmethod
    @router.get(
        "/is_dir",
        tags=["SFTP Operations"],
        response_model=List[IsDirResponse],
        responses={
            200: {
                "description": "Successful Response",
                "content": {
                    "application/json": {
                        "example": [
                            {
                                "host": "server105",
                                "error": False,
                                "message": "",
                                "value": True,
                            },
                            {
                                "host": "server104",
                                "error": False,
                                "message": "",
                                "value": True,
                            },
                        ]
                    }
                },
            }
        },
    )
    async def is_dir(
        path: Union[PurePath, str, bytes] = Query(
            ..., description="Path to check if it's a directory"
        ),
        common: CommonCallbackRequest = Depends(common_callback_request),
    ) -> Request:
        """# Return if the remote path refers to a directory

        ## Python API

        - Coroutine: `IsDir`
        - Response schema: `[IsDirResponse]`

        Python API example:

        ```python
        from reemote import sftp1

        responses = await execute(lambda: sftp1.IsDir(path="."), inventory)
        for item in responses:
            assert item.value, "The coroutine should report that the current working directory exists on the host."
        ```
        """
        return await (router_handler1(IsDir))(
            path=path,
            common=common,
        )
