from pydantic import Field
from typing import AsyncGenerator
from reemote.local_model import Local
from reemote.command import Command
from reemote.response import Response

from reemote.commands.sftp import Mkdir, MkdirModel
from reemote.facts.sftp import Isdir, Stat
from reemote.commands.sftp import Rmdir


class DirectoryModel(MkdirModel):
    present: bool = Field(..., description="Whether the directory exists or not.")


class Directory(Local):
    Model = DirectoryModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        model_instance = self.Model(**self.kwargs)

        isdir = yield Isdir(path=model_instance.path, group=model_instance.group)
        if isdir:
            print("debug 03")
            if not model_instance.present and not isdir.output:
                print("debug 00")
            elif not model_instance.present and isdir.output:
                yield Rmdir(path=model_instance.path, group=model_instance.group)
            elif model_instance.present and not isdir.output:
                yield Mkdir(path=model_instance.path, group=model_instance.group)
            elif model_instance.present and isdir.output:
                print("debug 04")
                r = yield Stat(path="/home/user/freddy", group=model_instance.group)
                print(r)
