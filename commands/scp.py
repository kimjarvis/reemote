from typing import Callable, List, Optional

import asyncssh
from fastapi import APIRouter, Depends, Query
from pydantic import Field

from common.router_utils import create_router_handler
from construction_tracker import track_construction
from inventory import get_unique_host_user
from local_params import LocalModel, LocalParams, local_params

router = APIRouter()

class ScpModel(LocalParams):
    srcpaths: List[str] = Field(
        ...,  # Required field
    )
    dstpath: str = Field(
        ...,  # Required field
    )
    preserve: bool = False
    recurse: bool = False
    block_size: int = 16384
    progress_handler: Optional[Callable] = None
    error_handler: Optional[Callable] = None

@track_construction
class Upload(LocalModel):
    Model = ScpModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):

        return await asyncssh.scp(
            srcpaths=caller.srcpaths,
            dstpath=(host_info.get("host"), caller.dstpath),
            username=host_info.get("username"),
            preserve=caller.preserve,
            recurse=caller.recurse,
            block_size=caller.block_size,
            progress_handler=caller.progress_handler,
            error_handler=caller.error_handler,
        )

@router.get("/commands/upload/", tags=["SCP"])
async def upload(
        srcpaths: List[str] = Query(
            ...,
            description="The paths of the source files or directories to copy"
        ),
        dstpath: str = Query(
            None,
            description="The path of the destination file or directory to copy into"
        ),
        preserve: bool = Query(
            False,
            description="Whether or not to preserve the original file attributes"
        ),
        recurse: bool = Query(
            False,
            description="Whether or not to recursively copy directories"
        ),
        block_size: int = Query(
            16384,
            ge=1,
            description="The block size to use for file reads and writes"
        ),
        progress_handler: Optional[str] = Query(
            None,
            include_in_schema=False,  # This hides it from OpenAPI schema
            description="Callback function name for download progress"
        ),
        error_handler: Optional[str] = Query(
            None,
            include_in_schema=False,  # This hides it from OpenAPI schema
            description="Callback function name for error handling"
        ),
        common: LocalParams = Depends(local_params)
) -> list[dict]:
    """
    will continue starting with the next file.
    """
    return await create_router_handler(ScpModel, Upload)(
        srcpaths=srcpaths,
        dstpath=dstpath,
        preserve=preserve,
        recurse=recurse,
        block_size=block_size,
        progress_handler=progress_handler,
        error_handler=error_handler,
        common=common)

@track_construction
class Download(LocalModel):
    Model = ScpModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):

        unique, host, user = get_unique_host_user(command.group)

        if not unique:
            raise ValueError(f"group must identify a unique host")

        return await asyncssh.scp(
            srcpaths=[(host_info.get("host"), path) for path in caller.srcpaths],
            dstpath=caller.dstpath,
            username=host_info.get("username"),
            preserve=caller.preserve,
            recurse=caller.recurse,
            block_size=caller.block_size,
            progress_handler=caller.progress_handler,
            error_handler=caller.error_handler,
        )

@router.get("/commands/download/", tags=["SCP"])
async def download(
        srcpaths: List[str] = Query(
            ...,
            description="The paths of the source files or directories to copy"
        ),
        dstpath: str = Query(
            None,
            description="The path of the destination file or directory to copy into"
        ),
        preserve: bool = Query(
            False,
            description="Whether or not to preserve the original file attributes"
        ),
        recurse: bool = Query(
            False,
            description="Whether or not to recursively copy directories"
        ),
        block_size: int = Query(
            16384,
            ge=1,
            description="The block size to use for file reads and writes"
        ),
        progress_handler: Optional[str] = Query(
            None,
            include_in_schema=False,  # This hides it from OpenAPI schema
            description="Callback function name for download progress"
        ),
        error_handler: Optional[str] = Query(
            None,
            include_in_schema=False,  # This hides it from OpenAPI schema
            description="Callback function name for error handling"
        ),
        common: LocalParams = Depends(local_params)
) -> list[dict]:
    """
    will continue starting with the next file.
    """
    return await create_router_handler(ScpModel, Download)(
        srcpaths=srcpaths,
        dstpath=dstpath,
        preserve=preserve,
        recurse=recurse,
        block_size=block_size,
        progress_handler=progress_handler,
        error_handler=error_handler,
        common=common)

class CopyModel(ScpModel):
    dstgroup: str = None

@track_construction
class Copy(LocalModel):
    Model = CopyModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):

        unique, host, user = get_unique_host_user(command.group)
        if not unique:
            raise ValueError(f"group must identify a unique host")

        unique, dsthost, dstuser = get_unique_host_user(caller.dstgroup)
        if not unique:
            raise ValueError(f"dstgroup must identify a unique host")

        return await asyncssh.scp(
            srcpaths=[(host_info.get("host"), path) for path in caller.srcpaths],
            dstpath=(dsthost, caller.dstpath),
            username=dstuser,
            preserve=caller.preserve,
            recurse=caller.recurse,
            block_size=caller.block_size,
            progress_handler=caller.progress_handler,
            error_handler=caller.error_handler,
        )

@router.get("/commands/copy/", tags=["SCP"])
async def copy(
        srcpaths: List[str] = Query(
            ...,
            description="The paths of the source files or directories to copy"
        ),
        dstpath: str = Query(
            None,
            description="The path of the destination file or directory to copy into"
        ),
        dstgroup: str = Query(
            None,
            description="The group of the host to copy to"
        ),
        preserve: bool = Query(
            False,
            description="Whether or not to preserve the original file attributes"
        ),
        recurse: bool = Query(
            False,
            description="Whether or not to recursively copy directories"
        ),
        block_size: int = Query(
            16384,
            ge=1,
            description="The block size to use for file reads and writes"
        ),
        progress_handler: Optional[str] = Query(
            None,
            include_in_schema=False,  # This hides it from OpenAPI schema
            description="Callback function name for download progress"
        ),
        error_handler: Optional[str] = Query(
            None,
            include_in_schema=False,  # This hides it from OpenAPI schema
            description="Callback function name for error handling"
        ),
        common: LocalParams = Depends(local_params)
) -> list[dict]:
    """
    will continue starting with the next file.
    """
    return await create_router_handler(ScpModel, Copy)(
        srcpaths=srcpaths,
        dstpath=dstpath,
        dstgroup=dstgroup,
        preserve=preserve,
        recurse=recurse,
        block_size=block_size,
        progress_handler=progress_handler,
        error_handler=error_handler,
        common=common)
