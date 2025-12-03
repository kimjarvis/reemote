from fastapi import Depends, Query
from pydantic import BaseModel

class CommonParams(BaseModel):
    group: str | None = None  # Optional field with default value None
    name: str | None = None  # Optional field with default value None
    sudo: bool = False
    su: bool = False
    get_pty: bool = False

def common_params(
    group: str | None = Query(None, description="Optional inventory group"),
    name: str | None = Query(None, description="Optional name"),
    sudo: bool = Query(False, description="Whether to use sudo"),
    su: bool = Query(False, description="Whether to use su"),
    get_pty: bool = Query(False, description="Whether to get a PTY")
) -> CommonParams:
    return CommonParams(group=group, name=name, sudo=sudo, su=su, get_pty=get_pty)