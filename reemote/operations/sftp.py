from pydantic import Field
from typing import AsyncGenerator
from reemote.local_model import Local
from reemote.response import Response

from reemote.commands.sftp import Mkdir, MkdirModel
from reemote.facts.sftp import Isdir, Stat
from reemote.commands.sftp import Rmdir, Chmod, Chown, Utime
from reemote.commands.system import Return


class DirectoryModel(MkdirModel):
    present: bool = Field(..., description="Whether the directory exists or not.")


class Directory(Local):
    Model = DirectoryModel

    async def execute(
        self,
    ) -> AsyncGenerator[
        Isdir | Rmdir | Mkdir | Stat | Chmod | Chown | Utime | Return, Response
    ]:
        model_instance = self.Model(**self.kwargs)
        changed = False
        isdir = yield Isdir(path=model_instance.path, group=model_instance.group)
        if isdir:
            if not model_instance.present and not isdir.output:
                changed = False
            elif not model_instance.present and isdir.output:
                yield Rmdir(path=model_instance.path, group=model_instance.group)
                changed = True
            elif model_instance.present and not isdir.output:
                yield Mkdir(path=model_instance.path, group=model_instance.group)
                changed = True
            elif model_instance.present and isdir.output:
                r = yield Stat(path="/home/user/freddy", group=model_instance.group)
                if (
                    model_instance.permissions
                    and r.output["permissions"] != model_instance.permissions
                ):
                    yield Chmod(
                        path=model_instance.path,
                        permissions=model_instance.permissions,
                        group=model_instance.group,
                    )
                    changed = True
                if model_instance.uid and r.output["uid"] != model_instance.uid:
                    yield Chown(
                        path=model_instance.path,
                        uid=model_instance.uid,
                        group=model_instance.group,
                    )
                    changed = True
                if model_instance.uid and r.output["uid"] != model_instance.gid:
                    yield Chown(
                        path=model_instance.path,
                        gid=model_instance.gid,
                        group=model_instance.group,
                    )
                    changed = True
                if model_instance.atime and r.output["atime"] != model_instance.atime:
                    yield Utime(
                        path=model_instance.path,
                        atime=model_instance.atime,
                        mtime=model_instance.mtime,
                        group=model_instance.group,
                    )
                    changed = True
            yield Return(value=None, changed=changed)
