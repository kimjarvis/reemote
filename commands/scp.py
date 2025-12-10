import stat
from functools import partial
from typing import AsyncGenerator

import asyncssh
from fastapi import APIRouter, Query, Depends
from pydantic import Field

from command import Command
from common.base_classes import ShellBasedCommand
from common.router_utils import create_router_handler
from common_params import CommonParams, common_params
from construction_tracker import  track_construction, track_yields
from response import Response

router = APIRouter()

from pathlib import PurePath
from typing import Union, Sequence, Callable, Optional
from pydantic import BaseModel

class ScpModel(BaseModel):
    srcpaths: Union[str, Sequence[str]]
    dstpath: Optional[str] = None
    preserve: bool = False
    recurse: bool = False
    block_size: int = 16384
    progress_handler: Optional[Callable] = None
    error_handler: Optional[Callable] = None

@track_construction
class Scp(ShellBasedCommand):
    Model = ScpModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:

                print(f"Connected successfully to destination host {host_info['host']}")

                print(f"{host_info}, {global_info}")
                print(f"{command.group}")
                # Fix execute on local to ensure that the group specified on scp is in the global info.
                # src_host should be the host name from host_info.
                # This does nothing to ensure that the destination is unique in the inventory.
                # But it allows the user to make it unique by using a unique group name.

                # Prepare source paths with proper host prefixes
                if isinstance(caller.srcpaths, list):
                    srcpaths = []
                    for srcpath in caller.srcpaths:
                        # Add source host prefix if not already present
                        if ':' not in srcpath and caller.src_hosts:
                            # Use the first source host if multiple specified
                            source_host = caller.src_hosts[0]
                            srcpaths.append(f"{source_host}:{srcpath}")
                        else:
                            srcpaths.append(srcpath)
                else:
                    if ':' not in caller.srcpaths and caller.src_hosts:
                        source_host = caller.src_hosts[0]
                        srcpaths = f"{source_host}:{caller.srcpaths}"
                    else:
                        srcpaths = caller.srcpaths

                # Prepare destination path (local to destination host)
                if ':' in caller.dstpath:
                    # Extract path if destination has host prefix
                    dstpath = caller.dstpath.split(':', 1)[1]
                else:
                    dstpath = caller.dstpath

                print(f"Copying from source: {srcpaths} -> destination: {dstpath} on {host_info['host']}")

                await asyncssh.scp(
                    srcpaths=caller.srcpaths,
                    dstpath=caller.dstpath,
                    preserve=caller.preserve,
                    recurse=caller.recurse,
                    block_size=caller.block_size,
                    progress_handler=caller.progress_handler,
                    error_handler=caller.error_handler,
                )
    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        result = yield Command(local=True,
                               callback=self._callback,
                               caller=model_instance,
                               **self.extra_kwargs)
        self.mark_changed(result)
        return

scp_handler = create_router_handler(ScpModel, Scp)

@router.get("/commands/scp/", tags=["SCP"])
async def scp(
        srcpaths: Union[str, Sequence[str]] = Query(
            ...,
            description="The paths of the source files or directories to copy"
        ),
        dstpath: Optional[str] = Query(
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
            description="Callback function name for download progress"
        ),
        error_handler: Optional[str] = Query(
            None,
            description="Callback function name for error handling"
        ),
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    """
    will continue starting with the next file.
    """
    return await scp_handler(
        srcpaths=srcpaths,
        dstpath=dstpath,
        preserve=preserve,
        recurse=recurse,
        block_size=block_size,
        progress_handler=progress_handler,
        error_handler=error_handler,
        common=common)
