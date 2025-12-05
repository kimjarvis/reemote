from typing import Any
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

async def validate_responses(responses: list[Any]) -> list[Response]:
    try:
        validated_responses = [
            Response(
                host=r.host,
                name=r.op.name,
                command=r.op.command,
                stdout=r.cp.stdout,
                stderr=r.cp.stderr,
                changed=r.changed,
                return_code=r.cp.returncode,
                error=r.error
            )
            for r in responses
        ]
    except ValidationError as e:
        print(f"Validation failed: {e}")
    return validated_responses
