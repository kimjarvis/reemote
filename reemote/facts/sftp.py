from pathlib import PurePath
from typing import Union

import asyncssh
from fastapi import APIRouter, Depends, Query
from pydantic import Field, field_validator

from reemote.router_handler import router_handler
from reemote.local_model import Local, LocalModel, local_params

router = APIRouter()


class IslinkModel(LocalModel):
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

    def __init__(self, **data):
        # Ensure path is converted to PurePath if it's a string/bytes
        if 'path' in data and isinstance(data['path'], (str, bytes)):
            data['path'] = PurePath(data['path'])
        super().__init__(**data)

class Islink(Local):
    Model = IslinkModel

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
    return await router_handler(IslinkModel, Islink)(path=path, common=common)


class IsfileModel(LocalModel):
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

    def __init__(self, **data):
        # Ensure path is converted to PurePath if it's a string/bytes
        if 'path' in data and isinstance(data['path'], (str, bytes)):
            data['path'] = PurePath(data['path'])
        super().__init__(**data)

class Isfile(Local):
    Model = IsfileModel

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
    return await router_handler(IsfileModel, Isfile)(path=path, common=common)


class IsdirModel(LocalModel):
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

class Isdir(Local):
    Model = IsdirModel

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
    return await router_handler(IsdirModel, Isdir)(path=path, common=common)
