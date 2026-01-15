from fastapi import APIRouter, Body, Path, Depends
from pydantic import BaseModel, ValidationError
from typing import List
from reemote.config import Config
from reemote.core.inventory_model import InventoryItem, Inventory
from pydantic import BaseModel, RootModel
from reemote.operation import Operation
from reemote.system import Call
from reemote.context import Context
from reemote.core.router_handler import router_handler
from reemote.callback import CommonCallbackRequestModel, common_callback_request

# Re-export
from reemote.core.inventory_model import Session
from reemote.core.inventory_model import Connection
from reemote.core.inventory_model import Authentication

# Define the router
router = APIRouter()


class InventoryResponse(BaseModel):
    """Response model for inventory creation endpoint"""

    error: bool
    message: str


@router.post(
    "/create/",
    tags=["Inventory Management"],
    response_model=InventoryResponse,
)
async def create_inventory(inventory_data: Inventory = Body(...)):
    """# Create an inventory"""
    try:
        config = Config()
        inventory = inventory_data.to_json_serializable()
        config.set_inventory(inventory)
        return InventoryResponse(error=False, message="Inventory created successfully.")
    except ValidationError as e:
        return InventoryResponse(error=True, message=f"Validation error: {e}")
    except ValueError as e:
        return InventoryResponse(error=True, message=f"Value error: {e}")
    except Exception as e:
        return InventoryResponse(error=True, message=f"Unexpected error: {e}")


@router.post(
    "/add/",
    tags=["Inventory Management"],
    response_model=InventoryResponse,
)
async def add_host(new_host: InventoryItem = Body(...)):
    """# Add a new host to the inventory"""
    try:
        config = Config()
        inventory_data = (
            config.get_inventory() or {}
        )  # Default to an empty dictionary if None

        # Ensure the inventory data has a "hosts" key with a list
        if not isinstance(inventory_data, dict):
            raise ValueError("Inventory data is not in the expected dictionary format.")
        if "hosts" not in inventory_data or not isinstance(
            inventory_data["hosts"], list
        ):
            inventory_data[
                "hosts"
            ] = []  # Initialize as an empty list if missing or invalid

        # Parse the current inventory into the Inventory model
        inventory = Inventory(hosts=inventory_data["hosts"])

        # Check if the host already exists in the inventory
        for item in inventory.hosts:
            if item.connection.host == new_host.connection.host:
                raise ValueError(
                    f"Host already exists in the inventory: {new_host.connection.host}"
                )

        # Add the new host to the inventory
        inventory.hosts.append(new_host)

        # Save the updated inventory back to the configuration
        config.set_inventory(inventory.to_json_serializable())

        # Return a success response
        return InventoryResponse(
            error=False, message=f"Host added successfully: {new_host.connection.host}"
        )
    except ValidationError as e:
        return InventoryResponse(error=True, message=f"Validation error: {e}")
    except ValueError as e:
        return InventoryResponse(error=True, message=f"Value error: {e}")
    except Exception as e:
        return InventoryResponse(error=True, message=f"Unexpected error: {e}")


class InventoryDeleteResponse(BaseModel):
    """Response model for inventory deletion endpoint"""

    error: bool
    message: str


@router.delete(
    "/delete/{host}",
    tags=["Inventory Management"],
    response_model=InventoryResponse,
)
async def delete_host(
    host: str = Path(
        ..., description="The hostname or IP address of the host to delete"
    ),
):
    """# Delete a host from the inventory"""
    try:
        # Load the current inventory from the configuration
        config = Config()
        inventory_data = config.get_inventory() or {"hosts": []}

        # Ensure the inventory data has a "hosts" key with a list
        if (
            not isinstance(inventory_data, dict)
            or "hosts" not in inventory_data
            or not isinstance(inventory_data["hosts"], list)
        ):
            raise ValueError("Inventory data is not in the expected format.")

        # Parse the current inventory into the Inventory model
        inventory = Inventory(hosts=inventory_data["hosts"])

        # Find and remove the host from the inventory
        updated_hosts = [
            item for item in inventory.hosts if item.connection.host != host
        ]
        if len(updated_hosts) == len(inventory.hosts):
            # Host was not found in the inventory
            raise ValueError(f"Host not found in the inventory: {host}")

        # Update the inventory with the modified hosts list
        inventory.hosts = updated_hosts

        # Save the updated inventory back to the configuration
        config.set_inventory(inventory.to_json_serializable())

        # Return a success response
        return InventoryResponse(
            error=False, message=f"Host deleted successfully: {host}"
        )
    except ValidationError as e:
        return InventoryResponse(error=True, message=f"Validation error: {e}")
    except ValueError as e:
        return InventoryResponse(error=True, message=f"Value error: {e}")
    except Exception as e:
        return InventoryResponse(error=True, message=f"Unexpected error: {e}")


class GetInventoryResponseElement(BaseModel):
    value: Inventory
    error: bool
    message: str


@router.get(
    "/get",
    tags=["Inventory Management"],
    response_model=GetInventoryResponseElement,  # Directly use the response element
)
async def getinventory():
    """# Retrieve the inventory"""
    try:
        # Load the current inventory from the configuration
        config = Config()
        inventory_data = config.get_inventory() or {"hosts": []}

        # Ensure the inventory data has a "hosts" key with a list
        if (
            not isinstance(inventory_data, dict)
            or "hosts" not in inventory_data
            or not isinstance(inventory_data["hosts"], list)
        ):
            raise ValueError("Inventory data is not in the expected format.")

        # Return a single GetInventoryResponseElement
        return GetInventoryResponseElement(
            error=False,
            value=Inventory(hosts=inventory_data["hosts"]),
            message="Success",
        )

    except ValueError as e:
        return GetInventoryResponseElement(
            error=True, value=Inventory(hosts=[]), message=f"Error: {e}"
        )

    except Exception as e:
        return GetInventoryResponseElement(
            error=True, value=Inventory(hosts=[]), message=f"Unexpected error: {e}"
        )
