# inventory.py
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Tuple, Dict
import sqlite3
import json

# Create an APIRouter instance
router = APIRouter()

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Tuple, Dict
import sqlite3
import json


# Database setup
conn = sqlite3.connect("api_data.db", check_same_thread=False)
cursor = conn.cursor()

# Create table if not exists
cursor.execute('''
    CREATE TABLE IF NOT EXISTS entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data TEXT
    )
''')
conn.commit()

from typing import List, Tuple, Dict
from pydantic import BaseModel

# Define Pydantic model
class Entry(BaseModel):
    id: int  # Include the ID field
    data: List[Tuple[Dict, Dict]]  # The list of tuples containing dictionaries

def serialize_data(data: List[Tuple[Dict, Dict]]) -> str:
    return json.dumps(data, default=str)

# Deserialize JSON string back to Python structure
def deserialize_data(serialized_data: str) -> List[Tuple[Dict, Dict]]:
    return json.loads(serialized_data)

# CRUD Operations

@router.post("/entries/", tags=["Inventory"], response_model=Entry)
def create_inventory(entry: Entry):
    """
    # Create a new entry in the database

    Inventory has type `List[Tuple[Dict, Dict]]`.

    The first dictionary in the tuple contains parameters that are passed directly to *AsynchSSH*.

    - `host` the target host
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
