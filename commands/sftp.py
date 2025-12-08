import asyncssh
from typing import Any, AsyncGenerator, Optional
from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel, Field
from common.base_classes import ShellBasedCommand
from command import Command
from response import Response
from common.router_utils import create_router_handler
from common_params import CommonParams, common_params
from construction_tracker import ConstructionTracker, track_construction, track_yields
import logging
import stat

router = APIRouter()


class MkdirModel(BaseModel):
    path: str
    permissions: Optional[int] = Field(
        None,
        ge=0,
        le=0o7777,
        description="Directory permissions as octal integer (e.g., 0o755)"
    )
    uid: Optional[int] = Field(None, description="User ID")
    gid: Optional[int] = Field(None, description="Group ID")
    atime: Optional[float] = Field(None, description="Access time")
    mtime: Optional[float] = Field(None, description="Modification time")

    def get_sftp_attrs(self) -> Optional[asyncssh.SFTPAttrs]:
        """Create SFTPAttrs object from provided attributes"""
        attrs_dict = {}

        if self.permissions is not None:
            attrs_dict['permissions'] = self.permissions
        if self.uid is not None:
            attrs_dict['uid'] = self.uid
        if self.gid is not None:
            attrs_dict['gid'] = self.gid
        if self.atime is not None:
            attrs_dict['atime'] = self.atime
        if self.mtime is not None:
            attrs_dict['mtime'] = self.mtime

        if attrs_dict:
            # Add directory bit if permissions are provided
            if 'permissions' in attrs_dict:
                attrs_dict['permissions'] = attrs_dict['permissions'] | stat.S_IFDIR
            return asyncssh.SFTPAttrs(**attrs_dict)
        return None


@track_construction
class Mkdir(ShellBasedCommand):
    Model = MkdirModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                sftp_attrs = caller.get_sftp_attrs()
                if sftp_attrs:
                    await sftp.mkdir(caller.path, sftp_attrs)
                else:
                    await sftp.mkdir(caller.path)

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        result = yield Command(local=True,
                               callback=self._callback,
                               caller=model_instance,
                               id=ConstructionTracker.get_current_id(),
                               parents=ConstructionTracker.get_parents(),
                               **self.extra_kwargs)
        # Directory creation is inherently a changing operation
        self.mark_changed(result)
        return



@router.get("/command/mkdir/", tags=["SFTP"])
async def shell_command(
        path: str = Query(..., description="Directory path"),
        permissions: Optional[int] = Query(
            None,
            ge=0,
            le=0o7777,
            description="Directory permissions as octal integer (e.g., 755)"
        ),
        uid: Optional[int] = Query(None, description="User ID"),
        gid: Optional[int] = Query(None, description="Group ID"),
        atime: Optional[float] = Query(None, description="Access time"),
        mtime: Optional[float] = Query(None, description="Modification time"),
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    # Build parameters dictionary
    params = {"path": path}
    if permissions is not None:
        params["permissions"] = permissions
    if uid is not None:
        params["uid"] = uid
    if gid is not None:
        params["gid"] = gid
    if atime is not None:
        params["atime"] = atime
    if mtime is not None:
        params["mtime"] = mtime

    return await mkdir_handler(**params, common=common)



class IsModel(BaseModel):
    path: str

@track_construction
class Isdir(ShellBasedCommand):
    Model = IsModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.isdir(caller.path)

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        result = yield Command(local=True,
                               callback=self._callback,
                               caller=model_instance,
                               id=ConstructionTracker.get_current_id(),
                               parents=ConstructionTracker.get_parents(),
                               **self.extra_kwargs)
        result.output = result.cp.stdout
        return

@track_construction
class Isfile(ShellBasedCommand):
    Model = IsModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.isfile(caller.path)

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        result = yield Command(local=True,
                               callback=self._callback,
                               caller=model_instance,
                               id=ConstructionTracker.get_current_id(),
                               parents=ConstructionTracker.get_parents(),
                               **self.extra_kwargs)
        result.output = result.cp.stdout
        return

@track_construction
class Islink(ShellBasedCommand):
    Model = IsModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.islink(caller.path)

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        result = yield Command(local=True,
                               callback=self._callback,
                               caller=model_instance,
                               id=ConstructionTracker.get_current_id(),
                               parents=ConstructionTracker.get_parents(),
                               **self.extra_kwargs)
        result.output = result.cp.stdout
        return

# Create endpoint handler
mkdir_handler = create_router_handler(MkdirModel, Mkdir)
isdir_handler = create_router_handler(IsModel, Isdir)
isfile_handler = create_router_handler(IsModel, Isfile)
islink_handler = create_router_handler(IsModel, Islink)


@router.get("/fact/isdir/", tags=["SFTP"])
async def shell_command(
        path: str = Query(..., description="Directory path"),
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    return await isdir_handler(path=path, common=common)

@router.get("/fact/isfile/", tags=["SFTP"])
async def shell_command(
        path: str = Query(..., description="File path"),
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    return await isfile_handler(path=path, common=common)

@router.get("/fact/islink/", tags=["SFTP"])
async def shell_command(
        path: str = Query(..., description="Link path"),
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    return await islink_handler(path=path, common=common)
