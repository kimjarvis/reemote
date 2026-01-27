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
    value: bool = False

class IsDir(Callback):
    class Request(PathRequest):
        pass

    request_schema = Request
    response_schema = IsDirResponse
    method = Method.GET

    @classmethod
    async def callback(cls, context: Context) -> None:
        context.response = cls.response_schema
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
            # block insert examples/sftp/get/IsDir_responses.generated -4
            "200": {
                "description": "Successful Response",
                "content": {
                    "application/json": {
                        "example": [
                            {
                                "host": "server105",
                                "error": False,
                                "message": "",
                                "value": True
                            },
                            {
                                "host": "server108",
                                "error": False,
                                "message": "",
                                "value": True
                            }
                        ]
                    }
                }
            }
            # block end
        },
    )
    async def is_dir(
        path: Union[PurePath, str, bytes] = Query(
            ..., description="Path to check if it's a directory"
        ),
        common: CommonCallbackRequest = Depends(common_callback_request),
    ) -> Request:
        """# Return if the remote path refers to a directory

        <!-- block insert examples/sftp/get/IsDir_example.generated -->
        
        ## sftp.get.IsDir
        
        Example:
        
        ```python
        async def example(inventory):
            from reemote.execute import execute
            from reemote import sftp1
        
            responses = await execute(lambda: sftp1.get.IsDir(path=".."), inventory)
            for item in responses:
                assert item.value, (
                    "Expected the coroutine to report that the current working directory exists on all hosts."
                )
        
            return responses
        ```
        <!-- block end -->
        """
        return await (router_handler1(IsDir))(
            path=path,
            common=common,
        )
