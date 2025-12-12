# inventory.py
import logging
from fastapi import APIRouter, HTTPException
import sqlite3
import json
from typing import List, Tuple, Dict, Optional, Any
from pydantic import BaseModel, field_validator, RootModel
from pydantic_core.core_schema import FieldValidationInfo

# Create an APIRouter instance
router = APIRouter()

# Database setup
conn = sqlite3.connect("api_data.db", check_same_thread=False)
cursor = conn.cursor()

# Create table if not exists
cursor.execute('''
               CREATE TABLE IF NOT EXISTS entries
               (
                   id
                   INTEGER
                   PRIMARY
                   KEY
                   AUTOINCREMENT,
                   data
                   TEXT
               )
               ''')
conn.commit()


# Define Pydantic root model for inventory entries using RootModel
class InventoryEntry(RootModel):
    root: List[Dict]

    @field_validator('root')
    @classmethod
    def validate_entry_structure(cls, v: List[Dict]) -> List[Dict]:
        if len(v) != 2:
            raise ValueError('Entry must contain exactly two dictionaries')
        if not all(isinstance(item, dict) for item in v):
            raise ValueError('Both items must be dictionaries')
        if 'host' not in v[0]:
            raise ValueError("First dictionary must contain 'host' parameter")
        return v

def serialize_data(data: List[Tuple[Dict, Dict]]) -> str:
    return json.dumps(data, default=str)


# Deserialize JSON string back to Python structure
def deserialize_data(serialized_data: str) -> List[Tuple[Dict, Dict]]:
    return json.loads(serialized_data)


def validate_host_parameter(data: List[Tuple[Dict, Dict]]) -> None:
    """
    Validate that each host parameter is present and unique across all entries.

    Args:
        data: The inventory data to validate

    Raises:
        HTTPException: If host parameter is missing or not unique
    """
    hosts_seen = set()

    for item in data:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise HTTPException(
                status_code=400,
                detail="Each inventory item must be a tuple of two dictionaries"
            )

        host_params = item[0]

        # Check if host parameter is present
        if 'host' not in host_params:
            raise HTTPException(
                status_code=400,
                detail="The 'host' parameter is required in the first dictionary of each tuple"
            )

        host_value = host_params['host']

        # Check if host value is unique
        if host_value in hosts_seen:
            raise HTTPException(
                status_code=400,
                detail=f"Host '{host_value}' is not unique. Each host must have a unique value."
            )

        hosts_seen.add(host_value)


def check_host_uniqueness_across_database(hosts_to_check: set) -> None:
    """
    Check if any of the hosts already exist in the database.

    Args:
        hosts_to_check: Set of host values to check for uniqueness

    Raises:
        HTTPException: If any host already exists in the database
    """
    try:
        cursor.execute("SELECT data FROM entries")
        rows = cursor.fetchall()

        existing_hosts = set()
        for row in rows:
            serialized_data = row[0]
            deserialized_data = deserialize_data(serialized_data)

            for item in deserialized_data:
                host_params = item[0]
                if 'host' in host_params:
                    existing_hosts.add(host_params['host'])

        # Check for duplicates
        duplicates = hosts_to_check.intersection(existing_hosts)
        if duplicates:
            raise HTTPException(
                status_code=400,
                detail=f"Host(s) already exist in database: {', '.join(duplicates)}"
            )

    except Exception as e:
        logging.error(f"Error checking host uniqueness: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Error validating host uniqueness"
        )


# CRUD Operations


# Bulk create endpoint
@router.post("/create/", tags=["Inventory"], response_model=Dict)
def bulk_create_inventory(entries: List[List[Dict]]):
    """
    # Bulk create multiple inventory entries

    Accepts a JSON list of inventory entries and adds them to the database.
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

    # Process each entry in the bulk data
    for entry_data in entries:
        # Validate the entry structure
        if not isinstance(entry_data, list) or len(entry_data) != 2:
            raise HTTPException(
                status_code=400,
                detail=f"Each entry must be a list of exactly two dictionaries. Got: {entry_data}"
            )

        if not all(isinstance(item, dict) for item in entry_data):
            raise HTTPException(
                status_code=400,
                detail=f"Both items in the entry must be dictionaries. Got: {entry_data}"
            )

        if 'host' not in entry_data[0]:
            raise HTTPException(
                status_code=400,
                detail="First dictionary in each entry must contain 'host' parameter"
            )

        # Convert to the format expected by validation functions
        data_as_list = [(entry_data[0], entry_data[1])]

        # Validate host parameter
        validate_host_parameter(data_as_list)

        # Extract the host value
        host_value = entry_data[0]['host']

        # Check if this host already exists in the database
        check_host_uniqueness_across_database({host_value})

        # Proceed with creation if validation passes
        serialized_data = serialize_data(data_as_list)
        cursor.execute("INSERT INTO entries (data) VALUES (?)", (serialized_data,))
        conn.commit()
        entry_id = cursor.lastrowid  # Get the ID of the newly inserted row
        created_ids.append(entry_id)

    return {
        "message": f"Successfully created {len(created_ids)} entries",
        "created_ids": created_ids
    }



@router.post("/entries/", tags=["Inventory"], response_model=Dict)
def create_inventory(entry: InventoryEntry):
    """
    # Create a new entry in the database

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
    # We need to wrap it in a list and convert to tuple to match List[Tuple[Dict, Dict]]
    data_as_list = [(entry_data[0], entry_data[1])]

    # Validate host parameter presence and uniqueness within the new entry
    validate_host_parameter(data_as_list)

    # Extract the host value (since we're only adding one entry at a time)
    host_value = entry_data[0]['host']

    # Check if this host already exists in the database
    check_host_uniqueness_across_database({host_value})

    # Proceed with creation if validation passes
    serialized_data = serialize_data(data_as_list)
    cursor.execute("INSERT INTO entries (data) VALUES (?)", (serialized_data,))
    conn.commit()
    entry_id = cursor.lastrowid  # Get the ID of the newly inserted row

    return {
        "id": entry_id,
        "data": entry_data  # Return the original data
    }


@router.get("/entries/", tags=["Inventory"], response_model=List[Dict])
def read_the_inventory():
    """# Read all entries from the database

    Returns a list of entries, each with id and data fields.
    Data is a list of two dictionaries.
    """
    cursor.execute("SELECT id, data FROM entries")
    rows = cursor.fetchall()

    entries = []
    for row in rows:
        entry_id, serialized_data = row
        # Deserialize and extract the tuple, then convert to list
        deserialized_data = deserialize_data(serialized_data)
        if deserialized_data and len(deserialized_data) > 0:
            # Get the first tuple (should be only one per entry)
            entry_tuple = deserialized_data[0]
            # Convert tuple to list for the response
            entry_data = list(entry_tuple)
            entries.append({
                "id": entry_id,
                "data": entry_data
            })

    return entries


@router.get("/entries/{entry_id}", tags=["Inventory"], response_model=Dict)
def get_an_inventory_entry(entry_id: int):
    """# Read a single entry by ID

    Returns the entry with the specified ID.
    Data is a list of two dictionaries.
    """
    cursor.execute("SELECT id, data FROM entries WHERE id = ?", (entry_id,))
    row = cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    entry_id, serialized_data = row
    deserialized_data = deserialize_data(serialized_data)

    if not deserialized_data or len(deserialized_data) == 0:
        raise HTTPException(status_code=404, detail="Entry data is empty")

    # Convert tuple to list for the response
    entry_tuple = deserialized_data[0]
    entry_data = list(entry_tuple)

    return {
        "id": entry_id,
        "data": entry_data
    }


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
    updated_host = entry_data[0]['host']

    # Get the current entry to check if host has changed
    cursor.execute("SELECT data FROM entries WHERE id = ?", (entry_id,))
    row = cursor.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Entry not found for update")

    current_data = deserialize_data(row[0])
    current_host = current_data[0][0]['host'] if current_data and len(current_data) > 0 else None

    # If host has changed, check if new host already exists in other entries
    if current_host != updated_host:
        # Check database for any other entries containing the new host
        cursor.execute("SELECT id, data FROM entries WHERE id != ?", (entry_id,))
        other_rows = cursor.fetchall()

        existing_hosts = set()
        for other_row in other_rows:
            other_data = deserialize_data(other_row[1])
            for item in other_data:
                host_params = item[0]
                if 'host' in host_params:
                    existing_hosts.add(host_params['host'])

        # Check if new host already exists in other entries
        if updated_host in existing_hosts:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot update: Host '{updated_host}' already exists in another entry"
            )

    # Proceed with update if validation passes
    serialized_data = serialize_data(data_as_list)
    cursor.execute("UPDATE entries SET data = ? WHERE id = ?", (serialized_data, entry_id))
    conn.commit()

    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Entry not found")

    return {
        "id": entry_id,
        "data": entry_data
    }


@router.delete("/entries/{entry_id}", tags=["Inventory"])
def delete_an_inventory_entry(entry_id: int):
    """# Delete an entry by ID"""
    cursor.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    conn.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"message": "Entry deleted"}


def get_inventory() -> List[Tuple[Dict, Dict]]:
    """
    Fetches the entire inventory from the database and returns it as a Python object:
    A list of tuples, where each tuple contains two dictionaries.
    """
    try:
        # Query all entries from the database
        cursor.execute("SELECT data FROM entries")
        rows = cursor.fetchall()

        # Deserialize the data into Python objects
        inventory = []
        for row in rows:
            serialized_data = row[0]  # Extract the serialized data
            deserialized_data = deserialize_data(serialized_data)
            inventory.extend(deserialized_data)  # Add to the inventory list

        return inventory

    except Exception as e:
        logging.error(f"Error fetching inventory: {e}", exc_info=True)
        return []


def get_unique_host_user(group: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Returns (true, host, username) if the group string exists within one of the groups lists
    in the inventory and the string only appears within the groups list of one host.
    Otherwise returns (false, None, None).

    Args:
        group: The group string to search for

    Returns:
        Tuple[bool, Optional[str], Optional[str]]: (True, host_value, username) if group is unique to one host,
                                    (False, None, None) otherwise
    """
    try:
        group_host_mapping = {}
        host_username_mapping = {}

        # Query all entries from the database
        cursor.execute("SELECT data FROM entries")
        rows = cursor.fetchall()

        # Process each entry in the database
        for row in rows:
            serialized_data = row[0]
            deserialized_data = deserialize_data(serialized_data)

            # Process each inventory item in the entry
            for item in deserialized_data:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    continue

                host_params = item[0]
                global_params = item[1]

                # Check if host parameter exists
                if 'host' not in host_params:
                    continue

                host_value = host_params['host']

                # Extract username from host_params if it exists
                username = host_params.get('username') if 'username' in host_params else None

                # Check if groups list exists in global parameters
                if 'groups' in global_params and isinstance(global_params['groups'], list):
                    groups_list = global_params['groups']

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
        logging.error(f"Error in get_unique_host for group '{group}': {e}", exc_info=True)
        return False, None, None