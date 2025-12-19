from fastapi import APIRouter, Depends, Query
from pydantic import Field
from typing import AsyncGenerator, Callable, Any
from reemote.command import Command, ConnectionType
from reemote.router_handler import router_handler
from reemote.remote_model import Remote, RemoteModel, remote_params
from reemote.response import Response
from reemote.local_model import Local, LocalModel

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

class Return(Local):
    Model = ReturnModel

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def execute(self) -> AsyncGenerator[Command, Response]:
        model_instance = self.Model(**self.kwargs)

        yield Command(
            type=ConnectionType.PASSTHROUGH,
            value=model_instance.value,
            call=str(model_instance),
            caller=model_instance,
            group=model_instance.group,
        )
