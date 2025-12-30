from typing import AsyncGenerator, List, Any
from fastapi import APIRouter, Query, Depends
from reemote.command import Command
from reemote.router_handler import router_handler
from reemote.models import CommonModel, commonmodel, RemoteModel
from reemote.remote import Remote
from reemote.response import Response
from reemote.response import ShellResponse

router = APIRouter()


class InstallModel(RemoteModel):
    packages: list[str]


class Install(Remote):
    Model = InstallModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        model_instance = self.Model.model_validate(self.kwargs)

        yield Command(
            command=f"apt-get install -y {' '.join(model_instance.packages)}",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            **self.common_kwargs,
        )


@router.get(
    "/install",
    tags=["APT Package Manager Commands"],
    response_model=List[ShellResponse],
)
async def install(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonModel = Depends(commonmodel),
) -> list[dict]:
    """# Install APT packages"""
    return await router_handler(InstallModel, Install)(packages=packages, common=common)


class RemoveModel(RemoteModel):
    packages: list[str]


class Remove(Remote):
    Model = RemoveModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        model_instance = self.Model.model_validate(self.kwargs)

        yield Command(
            command=f"apt-get remove -y {' '.join(model_instance.packages)}",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            **self.common_kwargs,
        )


@router.get(
    "/remove",
    tags=["APT Package Manager Commands"],
    response_model=List[ShellResponse],
)
async def remove(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonModel = Depends(commonmodel),
) -> list[dict]:
    """# Remove APT packages"""
    return await router_handler(RemoveModel, Remove)(packages=packages, common=common)


class Update(Remote):
    async def execute(self) -> AsyncGenerator[Command, Response]:
        model_instance = self.Model.model_validate(self.kwargs)

        yield Command(
            command="apt-get update",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            **self.common_kwargs,
        )


@router.get(
    "/update",
    tags=["APT Package Manager Commands"],
    response_model=List[ShellResponse],
)
async def update(
    common: CommonModel = Depends(commonmodel),
) -> list[dict]:
    """# Update APT packages"""
    return await router_handler(RemoteModel, Update)(common=common)


class Upgrade(Remote):
    async def execute(self) -> AsyncGenerator[Command, Response]:
        model_instance = self.Model.model_validate(self.kwargs)

        yield Command(
            command="apt-get upgrade",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            **self.common_kwargs,
        )


@router.get(
    "/upgrade",
    tags=["APT Package Manager Commands"],
    response_model=List[ShellResponse],
)
async def upgrade(
    common: CommonModel = Depends(commonmodel),
) -> list[dict]:
    """# Upgrade APT packages"""
    return await router_handler(RemoteModel, Upgrade)(common=common)
