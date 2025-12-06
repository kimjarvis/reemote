from typing import Any, AsyncGenerator
from pydantic import BaseModel
from fastapi import APIRouter, Depends

from common.base_classes import ShellBasedCommand
from common.router_utils import create_router_handler
from facts.parse_apt_list_installed import parse_apt_list_installed
from common_params import CommonParams, common_params

router = APIRouter()


class FactModel(BaseModel):
    pass


class GetPackages(ShellBasedCommand):
    """Get installed packages fact"""
    Model = FactModel

    async def execute(self) -> AsyncGenerator:
        from commands.server import ShellCommand
        result = yield ShellCommand(cmd="apt list --installed | head -10", **self.extra_kwargs)

        if result and hasattr(result, 'output'):
            result.output = parse_apt_list_installed(result.cp.stdout)
            print(result.output)

        self.mark_changed(result)
        return


# Create endpoint handler
get_packages_handler = create_router_handler(FactModel, GetPackages)


@router.get("/get_packages/", tags=["APT Facts"])
async def facts_apt_packages(
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    """Get installed APT packages"""
    return await get_packages_handler(common=common)