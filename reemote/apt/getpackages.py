from typing import AsyncGenerator, List, Union

from fastapi import APIRouter, Depends

from reemote.context import Context
from reemote.core.request import Request, RequestModel, requestmodel
from reemote.core.router_handler import router_handler
from pydantic import BaseModel, Field, RootModel

router = APIRouter()

def parse_apt_list_installed(value):
    """Parses the output of 'apt list --installed' into a list of dictionaries.

    This helper function processes the raw string output from the
    `apt list --installed` command. It iterates through each line,
    skipping the "Listing..." header and any empty lines. For each valid
    package line, it accurately extracts the package name and its
    corresponding version number.

    Args:
        value (str): The raw string output from the `apt list --installed`
            command.

    Returns:
        list[dict]: A list of dictionaries, where each dictionary
            represents an installed package and contains 'name' and 'version'
            keys. Example: `[{'name': 'zlib1g', 'version': '1:1.2.11.dfsg-2'}]`.
    """
    lines = value.strip().split('\n')
    packages = []

    for line in lines:
        line = line.strip()
        if not line or line.startswith('Listing...'):
            continue

        # Split package name from the rest using first '/'
        if '/' not in line:
            continue

        name_part, rest = line.split('/', 1)
        name = name_part.strip()

        # Find the first space — version starts right after it
        space_index = rest.find(' ')
        if space_index == -1:
            continue

        # Extract everything after the first space
        after_space = rest[space_index + 1:]

        # Version is everything until the next space or '['
        version = after_space.split(' ', 1)[0].split('[', 1)[0].rstrip(',')

        packages.append({"name": name, "version": version})

    return packages


class PackageModel(BaseModel):
    name: str = Field(..., description="The name of the package")
    version: str = Field(..., description="The version of the package")


class GetPackagesResponseElement(BaseModel):
    value: Union[str, List[PackageModel]] = Field(
        default="",
        description="The response containing package versions, or an error message"
    )


class GetPackagesResponse(RootModel):
    root: List[GetPackagesResponseElement]

class GetPackages(Request):
    Model = RequestModel

    async def execute(self) -> AsyncGenerator[Context, GetPackagesResponse]:
        model_instance = self.Model.model_validate(self.kwargs)

        result = yield Context(
            command=f"apt list --installed",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            changed=False,
            **self.common_kwargs,
        )
        parsed_packages = parse_apt_list_installed(result["value"]["stdout"])

        # Convert the parsed list of dictionaries into a PackageList
        package_list = [PackageModel(**pkg) for pkg in parsed_packages]

        if not result["error"]:
            # Wrap the PackageList in a GetPackagesResponse
            result["value"] = package_list




@router.get(
    "/getpackages",
    tags=["APT Package Manager"],
    response_model=GetPackagesResponse,
)
async def getpackages(common: RequestModel = Depends(requestmodel)) -> GetPackagesResponse:
    """# Get installed APT packages"""
    return await router_handler(RequestModel, GetPackages)(common=common)
