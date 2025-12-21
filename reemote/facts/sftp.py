from pathlib import PurePath
from typing import Union

import asyncssh
from fastapi import APIRouter, Depends, Query
from pydantic import Field, field_validator

from reemote.router_handler import router_handler
from reemote.local_model import Local, LocalModel, local_params

router = APIRouter()


class LocalPathModel(LocalModel):
    path: Union[PurePath, str, bytes] = Field(
        ...,  # Required field
    )

    @field_validator('path', mode='before')
    @classmethod
    def ensure_path_is_purepath(cls, v):
        """
        Ensure the 'path' field is converted to a PurePath object.
        This runs before the field is validated by Pydantic.
        """
        if v is None:
            raise ValueError("path cannot be None.")
        if not isinstance(v, PurePath):
            try:
                return PurePath(v)
            except TypeError:
                raise ValueError(f"Cannot convert {v} to PurePath.")
        return v

class Islink(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.islink(str(caller.path))

@router.get("/fact/islink/", tags=["SFTP Facts"])
async def islink(
        path: Union[PurePath, str, bytes] = Query(..., description="Path to check if it's a link"),
        common: LocalModel = Depends(local_params)
) -> list[dict]:
    """# Return if the remote path refers to a symbolic link"""
    return await router_handler(LocalPathModel, Islink)(path=path, common=common)


class Isfile(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.isfile(str(caller.path))

@router.get("/fact/isfile/", tags=["SFTP Facts"])
async def isfile(
        path: Union[PurePath, str, bytes] = Query(..., description="Path to check if it's a file"),
        common: LocalModel = Depends(local_params)
) -> list[dict]:
    """# Return if the remote path refers to a file"""
    return await router_handler(LocalPathModel, Isfile)(path=path, common=common)

class Isdir(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.isdir(str(caller.path))

@router.get("/fact/isdir/", tags=["SFTP Facts"])
async def isdir(
        path: Union[PurePath, str, bytes] = Query(..., description="Path to check if it's a directory"),
        common: LocalModel = Depends(local_params)
) -> list[dict]:
    """# Return if the remote path refers to a directory"""
    return await router_handler(LocalPathModel, Isdir)(path=path, common=common)

class StatModel(LocalPathModel):
    follow_symlinks: bool = Field(
        True,  # Default value
    )

class Stat(Local):
    Model = StatModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                sftp_attrs = await sftp.stat(caller.path, follow_symlinks=caller.follow_symlinks)

                # Extract each field from the SFTPAttrs object and create the dictionary
                attrs_dict = {
                    'uid': getattr(sftp_attrs, 'uid'),
                    'gid': getattr(sftp_attrs, 'gid'),
                    'permissions': getattr(sftp_attrs, 'permissions') & 0o777,
                    'atime': getattr(sftp_attrs, 'atime'),
                    'mtime': getattr(sftp_attrs, 'mtime'),
                    'size': getattr(sftp_attrs, 'size')
                }

                # print(attrs_dict)
                return attrs_dict

@router.get("/fact/stat/", tags=["SFTP Facts"])
async def stat(
        path: Union[PurePath, str, bytes] = Query(..., description="The path of the remote file or directory to get attributes for"),
        follow_symlinks: bool = Query(True, description="Whether or not to follow symbolic links"),
        common: LocalModel = Depends(local_params)
) -> list[dict]:
    """# Get attributes of a remote file, directory, or symlink"""
    return await router_handler(StatModel, Stat)(path=path, follow_symlinks=follow_symlinks, common=common)
