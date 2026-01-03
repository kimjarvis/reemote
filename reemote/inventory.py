from fastapi import APIRouter, Body, Path
from pydantic import BaseModel, ValidationError, model_validator, Field
from typing import List, Dict, Any
from reemote.core.config import Config

# Define the router
router = APIRouter()


class Connection(BaseModel):
    host: str = Field(
        ..., description="The hostname or IP address of the remote server."
    )
    # model_config = {"extra": "allow"}

    # Allow arbitrary additional fields
    model_config = {
        "extra": "allow",
        "json_schema_extra": {
            "properties": {
                "host": {
                    "description": "The hostname or IP address of the remote server."
                },
                "username": {
                    "description": "The ssh username for authenticating with the remote server."
                },
                "password": {
                    "description": "The ssh password for authenticating with the remote server."
                },
                "port": {
                    "description": "The ssh port number for connecting to the remote server."
                },
            },
            "required": ["host"],
            "additionalProperties": {
                "type": "string",
                "description": "Additional asyncssh.SSHClientConnectionOptions for the connection.",
            },
        },
    }

    def to_json_serializable(self):
        """
        Convert the Connection object to a plain dictionary.
        """
        return self.model_dump()


class InventoryItem(BaseModel):
    connection: Connection = Field(
        ..., description="The ssh connection details for the remote server."
    )
    host_vars: Dict[str, Any] = Field(
        {}, description="Additional variables to be set for the remote server."
    )
    groups: List[str] = Field(
        [], description="The groups to which the remote server belongs."
    )

    def to_json_serializable(self):
        """
        Convert the InventoryItem object to a plain dictionary.
        """
        return {
            "connection": self.connection.to_json_serializable(),
            "host_vars": self.host_vars,
            "groups": self.groups,
        }


class Inventory(BaseModel):
    hosts: List[InventoryItem] = Field(
        default_factory=list,
        description="A list of inventory items representing remote servers.",
    )

    @model_validator(mode="after")
    def check_unique_hosts(self):
        """
        Validate that each 'host' in the inventory is unique.
        """
        seen_hosts = set()
        for item in self.hosts:
            host = item.connection.host
            if host in seen_hosts:
                raise ValueError(f"Duplicate host found: {host}")
            seen_hosts.add(host)
        return self

    def to_json_serializable(self):
        """
        Convert the Inventory object to a plain dictionary suitable for json.dump().
        """
        return {"hosts": [item.to_json_serializable() for item in self.hosts]}


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
    "/entries/{host}",
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
    """Response model for inventory retrieval endpoint"""

    error: bool
    value: Dict[str, List[Dict[str, Any]]]  # Inventory structure: {"hosts": [...]}


@router.get(
    "/entries/",
    tags=["Inventory Management"],
    response_model=InventoryGetResponse,
)
async def get_inventory():
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

        # Return the inventory in the response
        return InventoryGetResponse(error=False, value=inventory_data)
    except ValueError as e:
        # Handle custom validation errors (e.g., invalid inventory format)
        return InventoryGetResponse(
            error=True, value={"hosts": []}, description=f"Error: {e}"
        )
    except Exception as e:
        # Handle any other unexpected errors
        return InventoryGetResponse(
            error=True, value={"hosts": []}, description=f"Unexpected error: {e}"
        )


def get_inventory_item(inventory_item: dict) -> tuple:
    # Extract the connection dictionary
    connection = inventory_item.get("connection", {})

    # Extract the host_vars dictionary
    host_vars = inventory_item.get("host_vars", {})

    # Add the groups to the host_vars dictionary
    groups = inventory_item.get("groups", [])
    host_vars["groups"] = groups

    return connection, host_vars
