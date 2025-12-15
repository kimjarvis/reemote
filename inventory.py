import json
import logging
import os
from typing import Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import RootModel, field_validator

from validate_inventory import (
    check_host_uniqueness_across_database,
    get_next_id,
    validate_host_parameter,
)
from config import Config

# Create an APIRouter instance
router = APIRouter()

# Define Pydantic root model for inventory entries using RootModel
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
        return v


# CRUD Operations

# Bulk create endpoint
@router.post("/create/", tags=["Inventory"], response_model=Dict)
def bulk_create_inventory(entries: List[List[Dict]]):
    """
    # Bulk create multiple inventory entries

    Accepts a JSON list of inventory entries and adds them to the JSON file.
    Each entry should be in the same format as the single create endpoint.

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
                "groups": ["all", "local"],
                "sudo_user": "user",
                "sudo_password": "password"
            }
        ],
        [
            {
                "host": "192.168.1.77",
                "username": "admin",
                "password": "admin123"
            },
            {
                "groups": ["all", "remote"],
                "sudo_user": "admin",
                "sudo_password": "admin123"
            }
        ]
    ]
    ```

    ### Response

    Returns a list of created entry IDs.
    """
    created_ids = []
    config = Config()
    data = config.get_inventory()

    # Process each entry in the bulk data
    for entry_data in entries:
        # Validate the entry structure
        if not isinstance(entry_data, list) or len(entry_data) != 2:
            raise HTTPException(
                status_code=400,
                detail=f"Each entry must be a list of exactly two dictionaries. Got: {entry_data}",
            )

        if not all(isinstance(item, dict) for item in entry_data):
            raise HTTPException(
                status_code=400,
                detail=f"Both items in the entry must be dictionaries. Got: {entry_data}",
            )

        if "host" not in entry_data[0]:
            raise HTTPException(
                status_code=400,
                detail="First dictionary in each entry must contain 'host' parameter",
            )

        # Convert to the format expected by validation functions
        data_as_list = [(entry_data[0], entry_data[1])]

        # Validate host parameter
        validate_host_parameter(data_as_list)

        # Extract the host value
        host_value = entry_data[0]["host"]

        # Check if this host already exists in the JSON data
        check_host_uniqueness_across_database({host_value},  data_as_list)

        # Generate new ID
        entry_id = get_next_id(data)
        created_ids.append(entry_id)

        # Add to data
        data.append({"id": entry_id, "data": entry_data})

    # Save all entries
    config.set_inventory(data)


    return {
        "message": f"Successfully created {len(created_ids)} entries",
        "created_ids": created_ids,
    }


@router.post("/entries/", tags=["Inventory"], response_model=Dict)
def create_inventory(entry: InventoryEntry):
    """
    # Create a new entry in the JSON file

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
    # Get the data from the root model
    entry_data = entry.root

    # Convert to the format expected by validation functions
    data_as_list = [(entry_data[0], entry_data[1])]

    # Validate host parameter presence and uniqueness within the new entry
    validate_host_parameter(data_as_list)

    # Extract the host value
    host_value = entry_data[0]["host"]

    # Load existing data
    config = Config()
    data = config.get_inventory()

    # Check if this host already exists in the JSON data
    check_host_uniqueness_across_database({host_value}, data_as_list)


    # Generate new ID
    entry_id = get_next_id(data)

    # Add new entry
    data.append({"id": entry_id, "data": entry_data})

    # Save data
    config.set_inventory(data)

    return {"id": entry_id, "data": entry_data}


@router.get("/entries/", tags=["Inventory"], response_model=List[Dict])
def read_the_inventory():
    """# Read all entries from the JSON file

    Returns a list of entries, each with id and data fields.
    Data is a list of two dictionaries.
    """
    config = Config()
    return config.get_inventory()


@router.get("/entries/{entry_id}", tags=["Inventory"], response_model=Dict)
def get_an_inventory_entry(entry_id: int):
    """# Read a single entry by ID

    Returns the entry with the specified ID.
    Data is a list of two dictionaries.
    """
    config = Config()
    data = config.get_inventory()

    for entry in data:
        if entry.get("id") == entry_id:
            return entry

    raise HTTPException(status_code=404, detail="Entry not found")


@router.put("/entries/{entry_id}", tags=["Inventory"], response_model=Dict)
def update_an_inventory_entry(entry_id: int, entry: InventoryEntry):
    """# Update an existing entry by ID

    Updates the entry with the specified ID.
    Accepts a list of two dictionaries.
    """
    # Get the data from the root model
    entry_data = entry.root

    # Convert to the format expected by validation functions
    data_as_list = [(entry_data[0], entry_data[1])]

    # Validate host parameter presence and uniqueness within the updated entry
    validate_host_parameter(data_as_list)

    # Extract the host value
    updated_host = entry_data[0]["host"]

    # Load existing data
    config = Config()
    data = config.get_inventory()

    # Find the entry to update
    entry_found = False
    current_host = None

    for i, existing_entry in enumerate(data):
        if existing_entry.get("id") == entry_id:
            entry_found = True
            # Get current host value
            if (
                "data" in existing_entry
                and isinstance(existing_entry["data"], list)
                and len(existing_entry["data"]) > 0
            ):
                current_host = existing_entry["data"][0].get("host")

            # If host has changed, check if new host already exists in other entries
            if current_host != updated_host:
                check_host_uniqueness_across_database(
                    {updated_host}, data_as_list,  exclude_id=entry_id
                )

            # Update the entry
            data[i] = {"id": entry_id, "data": entry_data}
            break

    if not entry_found:
        raise HTTPException(status_code=404, detail="Entry not found for update")

    # Save updated data
    config.set_inventory(data)

    return {"id": entry_id, "data": entry_data}


@router.delete("/entries/{entry_id}", tags=["Inventory"])
def delete_an_inventory_entry(entry_id: int):
    """# Delete an entry by ID"""
    config = Config()
    data = config.get_inventory()

    # Filter out the entry to delete
    filtered_data = [entry for entry in data if entry.get("id") != entry_id]

    if len(filtered_data) == len(data):
        raise HTTPException(status_code=404, detail="Entry not found")

    # Save filtered data
    config.set_inventory(filtered_data)

    return {"message": "Entry deleted"}




def get_unique_host_user(group: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Returns (true, host, username) if the group string exists within one of the groups lists
    in the inventory and the string only appears within the groups list of one host.
    Otherwise, returns (false, None, None).

    Args:
        group: The group string to search for

    Returns:
        Tuple[bool, Optional[str], Optional[str]]: (True, host_value, username) if group is unique to one host,
                                    (False, None, None) otherwise
    """
    try:
        config = Config()
        data = config.get_inventory()

        group_host_mapping = {}
        host_username_mapping = {}

        # Process each entry in the JSON data
        for entry in data:
            if (
                "data" not in entry
                or not isinstance(entry["data"], list)
                or len(entry["data"]) != 2
            ):
                continue

            host_params = entry["data"][0]
            global_params = entry["data"][1]

            # Check if host parameter exists
            if not isinstance(host_params, dict) or "host" not in host_params:
                continue

            host_value = host_params["host"]
            username = host_params.get("username")

            # Check if groups list exists in global parameters
            if (
                isinstance(global_params, dict)
                and "groups" in global_params
                and isinstance(global_params["groups"], list)
            ):
                groups_list = global_params["groups"]

                # Check if the target group is in this host's groups list
                if group in groups_list:
                    # Store username for this host
                    if host_value not in host_username_mapping:
                        host_username_mapping[host_value] = username

                    # If group already found in another host, mark as not unique
                    if group in group_host_mapping:
                        group_host_mapping[group] = None  # Mark as duplicate
                    else:
                        # First time seeing this group with this host
                        group_host_mapping[group] = host_value

        # Check the result for the specific group
        if group in group_host_mapping:
            host_value = group_host_mapping[group]
            if host_value is not None:
                # Group exists and is unique to one host
                username = host_username_mapping.get(host_value)
                return True, host_value, username
            else:
                # Group exists but in multiple hosts
                return False, None, None
        else:
            # Group doesn't exist in any host
            return False, None, None

    except Exception as e:
        logging.error(
            f"Error in get_unique_host for group '{group}': {e}", exc_info=True
        )
        return False, None, None
