from pydantic import BaseModel, model_validator, Field
from typing import List, Dict, Any, Optional


class Connection(BaseModel):
    host: str = Field(
        ..., description="The hostname or IP address of the remote host."
    )
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
                "description": "Arguments to pass to Asyncssh connect().",
            },
        },
    }

    def to_json_serializable(self):
        return self.model_dump()


class Session(BaseModel):
    model_config = {
        "extra": "allow",
        "json_schema_extra": {
            "additionalProperties": {
                "type": "string",
                "description": "Arguments to pass to Asyncssh create_session().",
            },
        },
    }

    def to_json_serializable(self):
        return self.model_dump()


class Authentication(BaseModel):
    sudo_password: Optional[str] = Field(
        "", description="The ssh password for authenticating with the remote host."
    )
    su_user: Optional[str] = Field(
        "", description="The su user for authenticating with the remote host."
    )
    su_password: Optional[str] = Field(
        "", description="The su password for authenticating with the remote host."
    )

class InventoryItem(BaseModel):
    connection: Connection = Field(
        ..., description="The connection details for the remote host."
    )
    authentication: Optional[Authentication] = Field(
        default_factory=Authentication,
        description="The authentication details for the remote host."
    )
    session: Optional[Session] = Field(
        default_factory=Session,
        description="The session details for the remote host."
    )
    groups: List[str] = Field(
        [], description="The groups to which the remote host belongs."
    )

    def to_json_serializable(self) -> Dict[str, Any]:
        return self.model_dump()

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
