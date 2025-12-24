import logging
from fastapi import APIRouter, HTTPException
from reemote.config import Config
from typing import List, Dict, Any
from pydantic import BaseModel, RootModel, field_validator


# Define the Pydantic model
class InventoryEntry(RootModel):
    root: List[Dict]

    @field_validator("root")
    @classmethod
    def validate_entry_structure(cls, v: List[Dict]) -> List[Dict]:
        if len(v) != 2:
            raise ValueError("Entry must contain exactly two dictionaries")
        if not all(isinstance(item, dict) for item in v):
            raise ValueError("Both items must be dictionaries")
        if "host" not in v[0]:
            raise ValueError("First dictionary must contain 'host' parameter")
        if "groups" not in v[1]:
            raise ValueError("Second dictionary must contain 'groups' parameter")

        # Validate that 'groups' is a non-empty list
        groups = v[1].get("groups")
        if not isinstance(groups, list) or len(groups) == 0:
            raise ValueError("'groups' must be a list with at least one item")

        return v


# Function to validate a single inventory entry
def validate_inventory_entry(entry: List[Dict]) -> None:
    """
    Validates a single inventory entry.
    Raises ValueError if the entry is invalid.
    """
    try:
        # Validate the entry structure using the Pydantic model
        InventoryEntry(root=entry)
    except ValueError as e:
        raise ValueError(f"Validation failed for entry: {e}")


# Function to validate the entire structure
def validate_inventory_structure(inventory: List[List[Dict]]) -> None:
    """
    Validates the entire inventory structure.
    Raises ValueError if any entry is invalid or if there are duplicate host values.
    """
    seen_hosts = set()  # To track unique host values

    for entry in inventory:
        # Validate the structure of the individual entry
        validate_inventory_entry(entry)

        # Extract the 'host' value from the first dictionary
        host_value = entry[0].get("host")
        if host_value in seen_hosts:
            raise ValueError(f"Duplicate host value found: {host_value}")
        seen_hosts.add(host_value)


# Create an APIRouter instance
router = APIRouter()

# CRUD Operations


def read_inventory() -> List[List[Dict]]:
    config = Config()
    return config.get_inventory()


def create_inventory(inventory: List[List[Dict]]) -> Dict:
    # Validate the updated inventory structure
    validate_inventory_structure(inventory)

    config = Config()
    # Save the updated inventory back to the configuration
    config.set_inventory(inventory)

    return {
        "status": "success",
        "message": "Inventory created",
        "data": inventory,
    }


def add_entry(entry: List[Dict]) -> Dict:
    # Validate the structure of the individual entry
    validate_inventory_entry(entry)

    config = Config()
    inventory = config.get_inventory()

    # Append the new entry to the inventory list
    inventory.append(entry)

    # Validate the updated inventory structure
    validate_inventory_structure(inventory)

    # Save the updated inventory back to the configuration
    config.set_inventory(inventory)

    return {
        "status": "success",
        "message": "Inventory entry created",
        "data": entry,
    }

def get_entry(host: str) -> Dict:
    config = Config()
    inventory = config.get_inventory()

    for entry in inventory:
        if entry[0].get("host") == host:
            return {
                "status": "success",
                "message": "Inventory entry found",
                "data": entry,
            }
    raise ValueError(f"Entry for host not found: {host}")


def delete_entry(host: str) -> Dict:
    config = Config()
    inventory = config.get_inventory()

    for entry in inventory:
        if entry[0].get("host") == host:
            new_inv = [entry for entry in inventory if entry[0].get("host") != host]
            config.set_inventory(new_inv)
            return {
                "status": "success",
                "message": "Inventory entry deleted",
            }
    raise ValueError(f"Entry for host not found: {host}")


class ErrorResponse(BaseModel):
    """Error response model"""

    detail: str


@router.get(
    "/entries/",
    tags=["Inventory"],
    response_model=List[List[Dict]],
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Validation error - Invalid inventory structure",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
def read_the_inventory():
    try:
        return read_inventory()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.error(f"Error getting inventory: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


class InventoryCreateResponse(BaseModel):
    """Response model for inventory creation endpoint"""

    status: str
    message: str
    data: List[List[Dict[str, Any]]]  # Generic description without details


@router.post(
    "/create/",
    tags=["Inventory"],
    response_model=InventoryCreateResponse,
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Validation error - Invalid inventory structure",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
def create_the_inventory(inv: List[List[Dict]]):
    """
    # Create a new inventory
    Accepts a list of tuples one for each host.  Each tuple contains two dictionaries:
    - First dictionary: SSH parameters for AsyncSSH (must include 'host')
    - Second dictionary: Global parameters available in all classes

    ### Example Request Body
    ```json
    [
      [
        {
          "host": "192.168.1.76",
          "username": "user",
          "password": "password"
        },
        {
          "groups": [
            "all",
            "local"
          ],
          "sudo_user": "user",
          "sudo_password": "password"
        }
      ],
      [
        {
          "host": "192.168.1.24",
          "username": "user",
          "password": "password"
        },
        {
          "groups": [
            "all",
            "local"
          ],
          "sudo_user": "user",
          "sudo_password": "password"
        }
      ]
    ]
    ```
    """

    try:
        return create_inventory(inv)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.error(f"Error creating inventory: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


class EntryCreateResponse(BaseModel):
    """Response model for inventory creation endpoint"""

    status: str
    message: str
    data: List[Dict[str, Any]]  # Generic description without details


@router.post(
    "/entries/",
    tags=["Inventory"],
    response_model=InventoryCreateResponse,
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Validation error - Invalid inventory structure",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
def add_inventory_entry(entry: InventoryEntry):
    """
    # Create a new inventory entry
    Accepts a list containing two dictionaries:
    - First dictionary: SSH parameters for AsyncSSH (must include 'host')
    - Second dictionary: Global parameters available in all classes

    ### Example Request Body
    ```json
    [
        {
            "host": "192.168.1.76",
            "username": "user",
            "password": "password"
        },
        {
            "groups": ["all", "local"],
            "sudo_user": "user",
            "sudo_password": "password"
        }
    ]
    ```
    """
    try:
        # Get the data from the root model and call the shared function
        result = add_entry(entry.root)
        return result
    except ValueError as e:
        # Convert ValueError to HTTPException for API clients
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.error(f"Error adding inventory entry: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


class GetEntryResponse(BaseModel):
    """Response model for inventory creation endpoint"""

    status: str
    message: str
    data: List[Dict[str, Any]]  # Generic description without details


@router.get(
    "/entries/{host}",
    tags=["Inventory"],
    response_model=GetEntryResponse,
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
def get_inventory_entry(host: str):
    try:
        result = get_entry(host)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.error(f"Error getting inventory entry: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


class DeleteEntryResponse(BaseModel):
    """Response model for inventory creation endpoint"""

    status: str
    message: str


@router.delete(
    "/entries/{host}",
    tags=["Inventory"],
    response_model=DeleteEntryResponse,
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
def delete_inventory_entry(host: str):
    try:
        result = delete_entry(host)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.error(f"Error deleting inventory entry: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
