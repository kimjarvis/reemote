# inventory.py
import logging
from fastapi import APIRouter, HTTPException
import sqlite3
import json
from typing import List, Tuple, Dict, Optional
from pydantic import BaseModel

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


# Define Pydantic model
class Entry(BaseModel):
    id: int  # Include the ID field
    data: List[Tuple[Dict, Dict]]  # The list of tuples containing dictionaries


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

@router.post("/entries/", tags=["Inventory"], response_model=Entry)
def create_inventory(entry: Entry):
    """
    # Create a new entry in the database

    Inventory has type `List[Tuple[Dict, Dict]]`.

    The first dictionary in the tuple contains parameters that are passed directly to *AsynchSSH*.

    - `host` the target host (required and must be unique)
    - `username` the ssh user name
    - `password` the ssh password
    - etc...

    The second dictionary contains global parameters that are available in all classes.

    This dictionary may contain sudo information.

    - `sudo_user` the sudo user id.
    - `sudo_password` the sudo password.
    - etc...

    ### Example Inventory

    ```json
    {
      "id": 0,
      "data": [
        [
            {
            "host": "192.168.1.76",
            "username": "user",
            "password": "password"
            },
            {
            "groups": ["all","local"],
            "sudo_user": "user",
            "sudo_password": "password"
            }
        ]
      ]
    }
    ```
    """
    # Validate host parameter presence and uniqueness within the new entry
    validate_host_parameter(entry.data)

    # Extract all host values from the new entry
    new_hosts = {item[0]['host'] for item in entry.data}

    # Check if any of these hosts already exist in the database
    check_host_uniqueness_across_database(new_hosts)

    # Proceed with creation if validation passes
    serialized_data = serialize_data(entry.data)
    cursor.execute("INSERT INTO entries (data) VALUES (?)", (serialized_data,))
    conn.commit()
    entry_id = cursor.lastrowid  # Get the ID of the newly inserted row
    return {"id": entry_id, "data": entry.data}


@router.get("/entries/", tags=["Inventory"], response_model=List[Entry])
def read_the_inventory():
    """# Read all entries from the database"""
    cursor.execute("SELECT id, data FROM entries")  # Fetch both id and data
    rows = cursor.fetchall()
    entries = [{"id": row[0], "data": deserialize_data(row[1])} for row in rows]
    return entries


@router.get("/entries/{entry_id}", tags=["Inventory"], response_model=Entry)
def get_an_inventory_entry(entry_id: int):
    """# Read a single entry by ID"""
    cursor.execute("SELECT id, data FROM entries WHERE id = ?", (entry_id,))
    row = cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"id": row[0], "data": deserialize_data(row[1])}


@router.put("/entries/{entry_id}", tags=["Inventory"], response_model=Entry)
def update_an_inventory_entry(entry_id: int, entry: Entry):
    """# Update an existing entry by ID"""
    # Validate host parameter presence and uniqueness within the updated entry
    validate_host_parameter(entry.data)

    # Extract all host values from the updated entry
    updated_hosts = {item[0]['host'] for item in entry.data}

    # Get the current entry to exclude its hosts from uniqueness check
    cursor.execute("SELECT data FROM entries WHERE id = ?", (entry_id,))
    row = cursor.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Entry not found for update")

    current_data = deserialize_data(row[0])
    current_hosts = {item[0]['host'] for item in current_data if 'host' in item[0]}

    # Only check hosts that are new (not in the current entry)
    new_hosts = updated_hosts - current_hosts

    if new_hosts:
        # Check database for any other entries containing these new hosts
        cursor.execute("SELECT id, data FROM entries WHERE id != ?", (entry_id,))
        other_rows = cursor.fetchall()

        existing_hosts = set()
        for other_row in other_rows:
            other_data = deserialize_data(other_row[1])
            for item in other_data:
                host_params = item[0]
                if 'host' in host_params:
                    existing_hosts.add(host_params['host'])

        # Check for duplicates with new hosts
        duplicates = new_hosts.intersection(existing_hosts)
        if duplicates:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot update: Host(s) already exist in other entries: {', '.join(duplicates)}"
            )

    # Proceed with update if validation passes
    serialized_data = serialize_data(entry.data)
    cursor.execute("UPDATE entries SET data = ? WHERE id = ?", (serialized_data, entry_id))
    conn.commit()

    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Entry not found")

    return {"id": entry_id, "data": entry.data}


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


def get_unique_host(group: str) -> Tuple[bool, Optional[str], Optional[str]]:
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