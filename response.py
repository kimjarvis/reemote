# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import logging
from typing import Any, Dict, Tuple, Optional, List, Union, Generator
from asyncssh import SSHCompletedProcess
from pydantic import BaseModel, Field, validator, ConfigDict
from command import Command
from pydantic.json import pydantic_encoder
import json


class PackageInfo(BaseModel):
    name: str
    version: str


class Response(BaseModel):
    # Core execution results (from original Result)
    cp: Optional[SSHCompletedProcess] = Field(default=None, exclude=True)
    host: Optional[str] = None
    op: Optional[Command] = Field(default=None, exclude=True)
    changed: bool = False
#    output: List[Union[PackageInfo, Dict[str, Any]]] = []
    output: Optional[Any] = None  # Accept any type
    error: Optional[str] = None

    # Fields from Command (r.op)
    name: Optional[str] = None
    command: Optional[str] = None
    group: Optional[str] = None
    local: bool = False
    callback_str: Optional[str] = Field(None, alias="callback")
    caller_str: Optional[str] = Field(None, alias="caller")
    sudo: bool = False
    su: bool = False
    get_pty: bool = False
    host_info: Optional[Dict[str, str]] = None
    global_info: Optional[Union[str, List[str]]] = None

    # Process fields (extracted from SSHCompletedProcess)
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    return_code: Optional[int] = Field(None, alias="returncode")
    env: Optional[Dict[str, str]] = None
    subsystem: Optional[str] = None
    exit_status: Optional[int] = None
    exit_signal: Optional[Tuple[str, bool, str, str]] = None
    stdout_bytes: Optional[bytes] = None
    stderr_bytes: Optional[bytes] = None

    # New fields
    id: Optional[int] = None
    parents: Optional[List[Tuple[int, str]]] = None  # Changed from parent to parents

    # Pydantic v2 config
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        ignored_types=(Generator,),
    )

    def __init__(self, **data):
        # Extract fields from cp and op if provided
        cp = data.get('cp')
        op = data.get('op')

        # Populate process fields from cp
        if cp and isinstance(cp, SSHCompletedProcess):
            data['stdout'] = self._bytes_to_str(cp.stdout) if cp.stdout else None
            data['stderr'] = self._bytes_to_str(cp.stderr) if cp.stderr else None
            data['return_code'] = cp.returncode
            data['env'] = getattr(cp, 'env', None)
            data['subsystem'] = getattr(cp, 'subsystem', None)
            data['exit_status'] = getattr(cp, 'exit_status', None)
            data['exit_signal'] = getattr(cp, 'exit_signal', None)
            data['stdout_bytes'] = cp.stdout if isinstance(cp.stdout, bytes) else None
            data['stderr_bytes'] = cp.stderr if isinstance(cp.stderr, bytes) else None

        # Populate command fields from op
        if op and isinstance(op, Command):
            data['name'] = getattr(op, 'name', None)
            data['command'] = getattr(op, 'command', None)
            data['group'] = getattr(op, 'group', None)
            data['local'] = getattr(op, 'local', False)
            data['callback'] = self._callback_to_str(getattr(op, 'callback', None))
            data['caller'] = self._caller_to_str(getattr(op, 'caller', None))
            data['sudo'] = getattr(op, 'sudo', False)
            data['su'] = getattr(op, 'su', False)
            data['get_pty'] = getattr(op, 'get_pty', False)
            data['host_info'] = getattr(op, 'host_info', None)
            data['global_info'] = getattr(op, 'global_info', None)
            # Populate id from op.id if available
            data['id'] = getattr(op, 'id', data.get('id', None))
            data['parents'] = getattr(op, 'parents', data.get('parents', None))  # Changed from parent to parents

        super().__init__(**data)
        logging.debug(f"{self}")

    @staticmethod
    def _bytes_to_str(value: Any) -> str:
        """Convert bytes to string if needed."""
        if isinstance(value, bytes):
            try:
                return value.decode('utf-8')
            except:
                return str(value)
        return str(value) if value is not None else None

    @staticmethod
    def _callback_to_str(value: Any) -> Optional[str]:
        """Convert callback function to string representation."""
        if value is None:
            return None
        if callable(value):
            try:
                name = value.__name__
            except AttributeError:
                name = str(value)
            return f"<callback {name}>"
        return str(value)

    @staticmethod
    def _caller_to_str(value: Any) -> Optional[str]:
        """Convert callback function to string representation."""
        if value is None:
            return None
        if callable(value):
            try:
                name = value.__name__
            except AttributeError:
                name = str(value)
            return f"<caller {name}>"
        return str(value)

    @validator('output', pre=True)
    def validate_output(cls, v):
        """Ensure output is always a list of dicts or PackageInfo objects."""
        if v is None:
            return []
        if isinstance(v, list):
            validated_list = []
            for item in v:
                if isinstance(item, dict):
                    # Check if this looks like a PackageInfo
                    if 'name' in item and 'version' in item:
                        validated_list.append(PackageInfo(**item))
                    else:
                        # Convert all values to JSON-serializable types
                        serializable_dict = {}
                        for key, value in item.items():
                            serializable_dict[key] = cls._make_json_serializable(value)
                        validated_list.append(serializable_dict)
                elif isinstance(item, PackageInfo):
                    validated_list.append(item)
                else:
                    validated_list.append({"value": cls._make_json_serializable(item)})
            return validated_list
        else:
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
            try:
                return str(value)
            except:
                return "<unserializable>"

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

    @validator('id', pre=True)
    def validate_id(cls, v):
        """Validate that id is either None or an integer."""
        if v is None:
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            # If it can't be converted to int, return None
            return None

    @validator('parents', pre=True)
    def validate_parents(cls, v):
        """Validate that parents is either None or a list of (int, str) tuples."""
        if v is None:
            return None

        if isinstance(v, list):
            validated_list = []
            for item in v:
                if isinstance(item, tuple) and len(item) == 2:
                    try:
                        # Ensure first element is int, second is str
                        parent_id = int(item[0]) if item[0] is not None else None
                        parent_name = str(item[1]) if item[1] is not None else None
                        if parent_id is not None and parent_name is not None:
                            validated_list.append((parent_id, parent_name))
                    except (ValueError, TypeError):
                        # Skip invalid items
                        continue
            return validated_list if validated_list else None

        # If it's not a list, try to convert from other formats
        try:
            # If it's a single tuple
            if isinstance(v, tuple) and len(v) == 2:
                parent_id = int(v[0]) if v[0] is not None else None
                parent_name = str(v[1]) if v[1] is not None else None
                if parent_id is not None and parent_name is not None:
                    return [(parent_id, parent_name)]
        except (ValueError, TypeError):
            pass

        return None

    def __str__(self) -> str:
        """String representation for debugging."""
        return self.__repr__()

    def __repr__(self) -> str:
        """Detailed representation."""
        return_code = self.cp.returncode if self.cp else self.return_code
        stdout = self.cp.stdout if self.cp else self.stdout
        stderr = self.cp.stderr if self.cp else self.stderr

        return (f"Response(host={self.host!r}, "
                f"name={self.name!r}, "
                f"command={self.command!r}, "
                f"changed={self.changed!r}, "
                f"return_code={return_code!r}, "
                f"stdout={stdout!r}, "
                f"stderr={stderr!r}, "
                f"output={self.output!r}, "
                f"error={self.error!r}, "
                f"id={self.id!r}, "
                f"parents={self.parents!r})")  # Changed from parent to parents


async def validate_responses(responses: list[Any]) -> list[Response]:
    """Convert any response-like objects to UnifiedResult instances."""
    validated_responses = []

    for r in responses:
        try:
            if isinstance(r, Response):
                # Already a UnifiedResult, just add it
                validated_responses.append(r)
            else:
                # Convert to UnifiedResult
                unified_result = Response(
                    cp=getattr(r, 'cp', None),
                    host=getattr(r, 'host', None),
                    op=getattr(r, 'op', None),
                    changed=getattr(r, 'changed', False),
                    output=getattr(r, 'output', []),
                    error=getattr(r, 'error', None),
                    id=getattr(r, 'id', None),
                    parents=getattr(r, 'parents', None)  # Changed from parent to parents
                )
                validated_responses.append(unified_result)
        except Exception as e:
            logging.error(f"Error converting response: {e}", exc_info=True)
            # Create a minimal error result
            error_result = Response(
                error=f"Failed to convert response: {str(e)}",
                host=getattr(r, 'host', None) if hasattr(r, 'host') else None,
                id=getattr(r, 'id', None) if hasattr(r, 'id') else None,
                parents=getattr(r, 'parents', None) if hasattr(r, 'parents') else None  # Changed from parent to parents
            )
            validated_responses.append(error_result)

    return validated_responses