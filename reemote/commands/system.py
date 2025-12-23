from fastapi import APIRouter, Depends, Query
from pydantic import Field
from typing import AsyncGenerator, Callable, Any
from reemote.command import Command, ConnectionType
from reemote.router_handler import router_handler
from reemote.response import Response
from reemote.models import LocalModel, RemoteModel, remotemodel
from reemote.remote import Remote
from reemote.local import Local


class CallbackModel(LocalModel):
    callback: Callable = Field(
        ...,  # Required field
    )
    value: Any

class Callback(Local):
    Model = CallbackModel

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def execute(self) -> AsyncGenerator[Command, Response]:
        model_instance = self.Model(**self.kwargs)

        yield Command(
            type=ConnectionType.LOCAL,
            value=model_instance.value,
            callback=model_instance.callback,
            call=str(model_instance),
            caller=model_instance,
            group=model_instance.group,
        )

class ReturnModel(LocalModel):
    value: Any
    changed: bool

class Return(Local):
    Model = ReturnModel

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def execute(self) -> AsyncGenerator[Command, Response]:
        model_instance = self.Model(**self.kwargs)
        print("debug 00")
        yield Command(
            type=ConnectionType.PASSTHROUGH,
            value=model_instance.value,
            changed=model_instance.changed,
            call=str(model_instance),
            caller=model_instance,
            group=model_instance.group,
        )
