from pydantic import BaseModel
class Response(BaseModel):
    host: str | None = None
    name: str | None = None
    command: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    changed: bool = True
    return_code: int | None = None
    error: str | None = None