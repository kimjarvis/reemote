from typing import Union
from reemote.local import Local
from reemote.models import LocalModel, localmodel, LocalPathModel
from reemote.commands.sftp import Mkdir
from reemote.api.sftp import Isdir, Stat
from reemote.commands.sftp import Rmdir, Chmod, Chown, Utime

from pathlib import PurePath
from typing import AsyncGenerator, Optional
from fastapi import APIRouter, Query, Depends
from reemote.response import Response
from reemote.api.system import Return
from pydantic import Field
from reemote.response import ResponseModel
from reemote.router_handler import router_handler_put

router = APIRouter()



class DirectoryRequestModel(LocalPathModel):
    present: bool = True
    permissions: Optional[int] = Field(None, ge=0, le=0o7777)
    uid: Optional[int] = Field(None, ge=0)
    gid: Optional[int] = Field(None, ge=0)
    atime: Optional[int] = Field(None, ge=0)
    mtime: Optional[int] = Field(None, ge=0)


class Directory(Local):
    Model = DirectoryRequestModel

    async def execute(
        self,
    ) -> AsyncGenerator[
        Isdir | Rmdir | Mkdir | Stat | Chmod | Chown | Utime | Return, Response
    ]:
        model_instance = self.Model.model_validate(self.kwargs)

        changed = False
        isdir = yield Isdir(path=model_instance.path, group=model_instance.group)
        if isdir:
            if not model_instance.present and not isdir["value"]:
                changed = False
            elif not model_instance.present and isdir["value"]:
                yield Rmdir(path=model_instance.path, group=model_instance.group)
                changed = True
            elif model_instance.present and not isdir["value"]:
                yield Mkdir(path=model_instance.path,
                            permissions=model_instance.permissions,
                            atime=model_instance.atime,
                            mtime=model_instance.mtime,
                            group=model_instance.group)
                changed = True
            elif model_instance.present and isdir["value"]:
                r = yield Stat(path="/home/user/freddy", group=model_instance.group)
                if (
                    model_instance.permissions
                    and r["value"]["permissions"] != model_instance.permissions
                ):
                    yield Chmod(
                        path=model_instance.path,
                        permissions=model_instance.permissions,
                        group=model_instance.group,
                    )
                    changed = True
                if model_instance.uid and r["value"]["uid"] != model_instance.uid:
                    yield Chown(
                        path=model_instance.path,
                        uid=model_instance.uid,
                        group=model_instance.group,
                    )
                    changed = True
                if model_instance.uid and r["value"]["gid"] != model_instance.gid:
                    yield Chown(
                        path=model_instance.path,
                        gid=model_instance.gid,
                        group=model_instance.group,
                    )
                    changed = True
                if model_instance.atime and r["value"]["atime"] != model_instance.atime:
                    yield Utime(
                        path=model_instance.path,
                        atime=model_instance.atime,
                        mtime=model_instance.mtime,
                        group=model_instance.group,
                    )
                    changed = True
            yield Return(value=None, changed=changed)


@router.put("/directory", tags=["SFTP"], response_model=ResponseModel)
async def directory(
    path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
    present: Optional[bool] = Query(...,
        description="Whether the directory should be present or not"
    ),
    permissions: Optional[int] = Query(
        None, ge=0, le=0o7777, description="Directory permissions as integer"
    ),
    uid: Optional[int] = Query(None, description="User ID"),
    gid: Optional[int] = Query(None, description="Group ID"),
    atime: Optional[int] = Query(None, description="Access time"),
    mtime: Optional[int] = Query(None, description="Modification time"),
    common: LocalModel = Depends(localmodel),
) -> ResponseModel:
    """# Manage APT packages"""
    params = {"path": path, "present": present}
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
    return await router_handler_put(DirectoryRequestModel, Directory)(
        **params,
        common=common,
    )
