from fastapi import APIRouter, Depends, Query
from pydantic import Field
from typing import AsyncGenerator, Callable, Any
from reemote.command import Command, ConnectionType
from reemote.router_handler import router_handler
from reemote.response import Response
from reemote.models import LocalModel, RemoteModel, remotemodel
from reemote.remote import Remote
from reemote.local import Local


class CallbackRequestModel(LocalModel):
    callback: Callable = Field(
        ...,  # Required field
    )
    value: Any


class Callback(Local):
    Model = CallbackRequestModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        model_instance = self.Model.model_validate(self.kwargs)

        yield Command(
            type=ConnectionType.LOCAL,
            value=model_instance.value,
            callback=model_instance.callback,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )


class ReturnRequestModel(LocalModel):
    value: Any
    changed: bool


class Return(Local):
    Model = ReturnRequestModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        model_instance = self.Model.model_validate(self.kwargs)

        yield Command(
            type=ConnectionType.PASSTHROUGH,
            value=model_instance.value,
            changed=model_instance.changed,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )
