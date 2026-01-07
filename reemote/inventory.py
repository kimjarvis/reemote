from fastapi import APIRouter, Body, Path, Depends
from pydantic import BaseModel, ValidationError
from typing import List
from reemote.config import Config
from reemote.core.inventory_model import InventoryItem, Inventory
from reemote.core.remote import Remote
from reemote.system import Callback
from reemote.context import Context
from reemote.core.router_handler import router_handler
from reemote.core.local import LocalModel, localmodel

# Re-export
from reemote.core.inventory_model import Session
from reemote.core.inventory_model import Connection
from reemote.core.inventory_model import Authentication

# Define the router
router = APIRouter()


class InventoryCreateResponse(BaseModel):
    """Response model for inventory creation endpoint"""

    error: bool
    value: str


@router.post(
    "/create/",
    tags=["Inventory Management"],
    response_model=InventoryCreateResponse,
)
async def create_inventory(inventory_data: Inventory = Body(...)):
    """# Create an inventory"""
    try:
        # No need to revalidate the Inventory object; it's already validated by FastAPI
        config = Config()
        inventory = (
            inventory_data.to_json_serializable()
        )  # Use the method on the Inventory object
        config.set_inventory(inventory)
        # If successful, return a success response
        return InventoryCreateResponse(
            error=False, value="Inventory created successfully."
        )
    except ValidationError as e:
        # Handle Pydantic validation errors
        return InventoryCreateResponse(error=True, value=f"Validation error: {e}")
    except ValueError as e:
        # Handle custom validation errors (e.g., duplicate hosts)
        return InventoryCreateResponse(error=True, value=f"Error: {e}")
    except Exception as e:
        # Handle any other unexpected errors
        return InventoryCreateResponse(error=True, value=f"Unexpected error: {e}")


@router.post(
    "/add/",
    tags=["Inventory Management"],
    response_model=InventoryCreateResponse,
)
async def add_host(new_host: InventoryItem = Body(...)):
    """# Add a new host to the inventory"""
    try:
        # Load the current inventory from the configuration
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
        return InventoryCreateResponse(
            error=False, value=f"Host added successfully: {new_host.connection.host}"
        )
    except ValidationError as e:
        # Handle Pydantic validation errors
        return InventoryCreateResponse(error=True, value=f"Validation error: {e}")
    except ValueError as e:
        # Handle custom validation errors (e.g., duplicate hosts or invalid inventory format)
        return InventoryCreateResponse(error=True, value=f"Error: {e}")
    except Exception as e:
        # Handle any other unexpected errors
        return InventoryCreateResponse(error=True, value=f"Unexpected error: {e}")


class InventoryDeleteResponse(BaseModel):
    """Response model for inventory deletion endpoint"""

    error: bool
    value: str


@router.delete(
    "/delete/{host}",
    tags=["Inventory Management"],
    response_model=InventoryDeleteResponse,
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
        return InventoryDeleteResponse(
            error=False, value=f"Host deleted successfully: {host}"
        )
    except ValidationError as e:
        # Handle Pydantic validation errors
        return InventoryDeleteResponse(error=True, value=f"Validation error: {e}")
    except ValueError as e:
        # Handle custom validation errors (e.g., host not found or invalid inventory format)
        return InventoryDeleteResponse(error=True, value=f"Error: {e}")
    except Exception as e:
        # Handle any other unexpected errors
        return InventoryDeleteResponse(error=True, value=f"Unexpected error: {e}")

class InventoryGetResponse(BaseModel):
    error: bool
    value: Inventory

async def inventory_get_callback(context: Context):
    return Config().get_inventory()

class Getinventory(Remote):
    Model = LocalModel

    async def execute(self):
        yield Callback(callback=inventory_get_callback)

@router.get(
    "/get",
    tags=["Inventory Management"],
    response_model=List[InventoryGetResponse],
)
async def get_inventory(
    common: LocalModel = Depends(localmodel)
) -> List[InventoryGetResponse]:
    """# Retrieve the inventory"""
    return await router_handler(LocalModel, Getinventory)(
        common=common
    )