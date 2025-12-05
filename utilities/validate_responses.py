from typing import Any, Dict, Tuple, Optional, Mapping, List, Union
from pydantic import BaseModel, ValidationError, Field, validator

class PackageInfo(BaseModel):
    name: str
    version: str

class Response(BaseModel):
    # Host and operation info (from Result)
    host: str | None = None
    changed: bool = False
    executed: bool = False
    output: List[PackageInfo] = []
    error: str | None = None

    # Fields from Command (r.op)
    name: str | None = None
    command: str | None = None
    group: str | None = None
    guard: bool = True
    local: bool = False
    callback_str: str | None = Field(None, alias="callback")
    caller_str: str | None = Field(None, alias="caller")
    sudo: bool = False
    su: bool = False
    get_pty: bool = False
    host_info: Optional[Dict[str, str]] = None
    global_info: Optional[Union[str, List[str]]] = None

    # Fields from SSHCompletedProcess (r.cp)
    stdout: str | None = None
    stderr: str | None = None
    return_code: int | None = Field(None, alias="returncode")
    env: Optional[Dict[str, str]] = None
    subsystem: str | None = None
    exit_status: int | None = None
    exit_signal: Optional[Tuple[str, bool, str, str]] = None
    stdout_bytes: Optional[bytes] = None
    stderr_bytes: Optional[bytes] = None

    class Config:
        arbitrary_types_allowed = True

    @validator('output', pre=True)
    def validate_output(cls, v):
        """Ensure output is always a list of dicts."""
        if v is None:
            return []
        if isinstance(v, list):
            # Ensure each item in the list is a dictionary
            validated_list = []
            for item in v:
                if isinstance(item, dict):
                    # Convert all values in the dict to JSON-serializable types
                    serializable_dict = {}
                    for key, value in item.items():
                        serializable_dict[key] = cls._make_json_serializable(value)
                    validated_list.append(serializable_dict)
                else:
                    # If item is not a dict, wrap it in a dict
                    validated_list.append({"value": cls._make_json_serializable(item)})
            return validated_list
        else:
            # If output is not a list, wrap it in a list with a dict
            return [{"value": cls._make_json_serializable(v)}]

    @staticmethod
    def _make_json_serializable(value: Any) -> Any:
        """Convert non-JSON-serializable types to serializable ones."""
        if value is None:
            return None
        elif isinstance(value, (str, int, float, bool)):
            return value
        elif isinstance(value, (list, tuple)):
            return [Response._make_json_serializable(item) for item in value]
        elif isinstance(value, dict):
            return {k: Response._make_json_serializable(v) for k, v in value.items()}
        elif isinstance(value, bytes):
            try:
                return value.decode('utf-8')
            except:
                return str(value)
        elif callable(value):
            try:
                return f"<callback {value.__name__}>"
            except AttributeError:
                return f"<callback {type(value).__name__}>"
        else:
            # For any other type, convert to string
            try:
                return str(value)
            except:
                return "<unserializable>"

    @validator('callback_str', pre=True)
    def validate_callback(cls, v):
        if v is None:
            return None
        if callable(v):
            try:
                name = v.__name__
            except AttributeError:
                name = str(v)
            return f"<callback {name}>"
        return str(v)

    @validator('caller_str', pre=True)
    def validate_caller(cls, v):
        if v is None:
            return None
        return str(v)

    @validator('global_info', pre=True)
    def validate_global_info(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            return v
        elif isinstance(v, list):
            return [str(item) for item in v]
        else:
            return str(v)


async def validate_responses(responses: list[Any]) -> list[Response]:
    try:
        validated_responses = []
        for r in responses:
            # Handle stdout/stderr conversion from bytes to string if needed
            stdout_str = None
            stderr_str = None
            stdout_bytes = None
            stderr_bytes = None

            cp = getattr(r, 'cp', None)
            op = getattr(r, 'op', None)

            if cp:
                if cp.stdout:
                    if isinstance(cp.stdout, bytes):
                        stdout_str = cp.stdout.decode('utf-8')
                        stdout_bytes = cp.stdout
                    else:
                        stdout_str = cp.stdout

                if cp.stderr:
                    if isinstance(cp.stderr, bytes):
                        stderr_str = cp.stderr.decode('utf-8')
                        stderr_bytes = cp.stderr
                    else:
                        stderr_str = cp.stderr

            response = Response(
                # Host and operation info (from Result)
                host=getattr(r, 'host', None),
                changed=getattr(r, 'changed', False),
                executed=getattr(r, 'executed', False),
                output=getattr(r, 'output', []),  # Now expects a list
                error=getattr(r, 'error', None),

                # Fields from Command (r.op)
                name=getattr(op, 'name', None) if op else None,
                command=getattr(op, 'command', None) if op else None,
                group=getattr(op, 'group', None) if op else None,
                guard=getattr(op, 'guard', True) if op else True,
                local=getattr(op, 'local', False) if op else False,
                callback=getattr(op, 'callback', None) if op else None,
                caller=getattr(op, 'caller', None) if op else None,
                sudo=getattr(op, 'sudo', False) if op else False,
                su=getattr(op, 'su', False) if op else False,
                get_pty=getattr(op, 'get_pty', False) if op else False,
                host_info=getattr(op, 'host_info', None) if op else None,
                global_info=getattr(op, 'global_info', None) if op else None,

                # Fields from SSHCompletedProcess (r.cp)
                stdout=stdout_str,
                stderr=stderr_str,
                returncode=cp.returncode if cp else None,
                env=cp.env if cp else None,
                subsystem=cp.subsystem if cp else None,
                exit_status=cp.exit_status if cp else None,
                exit_signal=cp.exit_signal if cp else None,
                stdout_bytes=stdout_bytes,
                stderr_bytes=stderr_bytes,
            )
            validated_responses.append(response)

    except ValidationError as e:
        print(f"Validation failed: {e}")
        validated_responses = []
    except AttributeError as e:
        print(f"Missing attribute: {e}")
        validated_responses = []
    except Exception as e:
        print(f"Unexpected error: {e}")
        validated_responses = []

    return validated_responses