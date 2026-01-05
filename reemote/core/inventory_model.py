from fastapi import APIRouter, Body, Path
from pydantic import BaseModel, ValidationError, model_validator, Field
from typing import List, Dict, Any
from reemote.core.config import Config


class Connection(BaseModel):
    host: str = Field(
        ..., description="The hostname or IP address of the remote host."
    )
    # model_config = {"extra": "allow"}

    # Allow arbitrary additional fields
    model_config = {
        "extra": "allow",
        "json_schema_extra": {
            "properties": {
                "host": {
                    "description": "The hostname or IP address of the remote host."
                },
                "username": {
                    "description": "The ssh username for authenticating with the remote host."
                },
                "password": {
                    "description": "The ssh password for authenticating with the remote host."
                },
                "port": {
                    "description": "The ssh port number for connecting to the remote host."
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
        ..., description="The ssh connection details for the remote host."
    )
    host_vars: Dict[str, Any] = Field(
        {}, description="Additional variables to be set for the remote host."
    )
    groups: List[str] = Field(
        [], description="The groups to which the remote host belongs."
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
        description="A list of inventory items representing remote hosts.",
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
