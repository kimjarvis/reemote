from typing import AsyncGenerator, List, Optional, Tuple, Union

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, RootModel, ValidationError

from reemote import core
from reemote.context import Context, ContextType, Method
from reemote.operation import (
    CommonOperationRequest,
    Operation,
    common_operation_request,
)
from reemote.response import GetResponseElement
from reemote.router_handler import router_handler1

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
    lines = value.strip().split("\n")
    packages = []

    for line in lines:
        line = line.strip()
        if not line or line.startswith("Listing..."):
            continue

        # Split package name from the rest using first '/'
        if "/" not in line:
            continue

        name_part, rest = line.split("/", 1)
        name = name_part.strip()

        # Find the first space — version starts right after it
        space_index = rest.find(" ")
        if space_index == -1:
            continue

        # Extract everything after the first space
        after_space = rest[space_index + 1 :]

        # Version is everything until the next space or '['
        version = after_space.split(" ", 1)[0].split("[", 1)[0].rstrip(",")

        packages.append({"name": name, "version": version})

    return packages


class AptGetPackagesRequest(CommonOperationRequest):
    pass


class PackageListItem(BaseModel):
    name: str = Field(..., description="The name of the package")
    version: str = Field(..., description="The version of the package")


class PackageList(RootModel):
    root: List[PackageListItem]


class AptGetPackagesResponse(GetResponseElement):
    value: PackageList = Field(
        # default=[],
        default_factory=lambda: PackageList(root=[]),
        description="List of package versions.",
    )
    request: AptGetPackagesRequest = Field(
        default=None,
        description="The request object used to execute the operation.",
    )


class AptGetPackagesResponses(RootModel):
    root: List[AptGetPackagesResponse]


class Packages(Operation):
    request = AptGetPackagesRequest
    response = AptGetPackagesResponse
    responses = AptGetPackagesResponses
    method = Method.GET

    async def execute(self) -> AsyncGenerator[Context, AptGetPackagesResponse]:
        model_instance = self.request.model_validate(self.kwargs)
        response = yield core.get.Fact(cmd="apt list --installed | head -4")
        if not response.error:
            parsed_packages = parse_apt_list_installed(response.value.stdout)
            package_list = PackageList.model_validate(parsed_packages)
            yield core.get.Return(value=package_list)

    # todo: Add the request to these examples
    @staticmethod
    @router.get(
        "/packages",
        tags=["APT Package Manager"],
        response_model=AptGetPackagesResponses,
        responses={
            "200": {
                "description": "Successful Response",
                "content": {
                    "application/json": {
                        "example": [
                            {
                                "host": "server105",
                                "error": False,
                                "message": "",
                                "value": [
                                    {"name": "adduser", "version": "3.152"},
                                    {"name": "apparmor", "version": "4.1.0-1"},
                                    {"name": "apt-listchanges", "version": "4.8"},
                                ],
                            },
                            {
                                "host": "server104",
                                "error": False,
                                "message": "",
                                "value": [
                                    {"name": "adduser", "version": "3.152"},
                                    {"name": "apparmor", "version": "4.1.0-1"},
                                    {"name": "apt-listchanges", "version": "4.8"},
                                ],
                            },
                        ]
                    }
                },
            }
        },
    )
    async def packages(
        common: CommonOperationRequest = Depends(common_operation_request),
    ):
        """# Get a list of installed packages and versions

        <!-- block insert examples/apt/get/Packages_example.generated -->
        
        ## apt.get.Packages
        
        Example:
        
        ```python
        async def example(inventory):
            from reemote.execute import execute
            from reemote import apt1
        
            from reemote import apt1
            from reemote.execute import execute
        
            responses = await execute(lambda: apt1.get.Packages(), inventory)
        
            package_present = all(
                any(item.name == "adduser" for item in response.value.root)
                for response in responses
            )
            assert package_present == True, (
                "Expected the coroutine to return a list of packages and versions installed"
            )
        
            return responses
        ```
        <!-- block end -->
        """
        return await (router_handler1(Packages))(
            common=common,
        )
