from typing import Optional
from fastapi import Query
from pydantic import BaseModel


class CommonParams(BaseModel):
    group: Optional[str] = "all"  # Default to "all"
    name: Optional[str] = None    # Optional field
    sudo: bool = False            # Default to False
    su: bool = False              # Default to False
    get_pty: bool = False         # Default to False

def common_params(
    group: Optional[str] = Query("all", description="Optional inventory group (defaults to 'all')"),
    name: Optional[str] = Query(None, description="Optional name"),
    sudo: bool = Query(False, description="Whether to use sudo"),
    su: bool = Query(False, description="Whether to use su"),
    get_pty: bool = Query(False, description="Whether to get a PTY")
) -> CommonParams:
    return CommonParams(group=group, name=name, sudo=sudo, su=su, get_pty=get_pty)