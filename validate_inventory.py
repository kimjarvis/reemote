import json
import logging
from typing import List, Tuple, Dict, Optional, Set

from fastapi import HTTPException

def get_next_id(data: List[Dict]) -> int:
    """
    Get the next available ID for a new entry.
    """
    if not data:
        return 1
    max_id = max((entry.get("id", 0) for entry in data), default=0)
    return max_id + 1

def validate_host_parameter(data: List[Tuple[Dict, Dict]]) -> None:
    """
    Validate that each host parameter is present and unique across all entries.
    """
    hosts_seen = set()

    for item in data:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise HTTPException(
                status_code=400,
                detail="Each inventory item must be a tuple of two dictionaries",
            )

        host_params = item[0]

        # Check if host parameter is present
        if "host" not in host_params:
            raise HTTPException(
                status_code=400,
                detail="The 'host' parameter is required in the first dictionary of each tuple",
            )

        host_value = host_params["host"]

        # Check if host value is unique
        if host_value in hosts_seen:
            raise HTTPException(
                status_code=400,
                detail=f"Host '{host_value}' is not unique. Each host must have a unique value.",
            )

        hosts_seen.add(host_value)


def check_host_uniqueness_across_database(
    hosts_to_check: Set[str],
    data: List[Tuple[Dict, Dict]],
    exclude_id: Optional[int] = None,
) -> None:
    """
    Check if any of the hosts already exist in the JSON data.
    """
    try:
        existing_hosts = set()

        for entry in data:
            # Skip the entry being updated
            if exclude_id is not None and entry.get("id") == exclude_id:
                continue

            if (
                "data" in entry
                and isinstance(entry["data"], list)
                and len(entry["data"]) == 2
            ):
                host_params = entry["data"][0]
                if isinstance(host_params, dict) and "host" in host_params:
                    existing_hosts.add(host_params["host"])

        # Check for duplicates
        duplicates = hosts_to_check.intersection(existing_hosts)
        if duplicates:
            raise HTTPException(
                status_code=400,
                detail=f"Host(s) already exist: {', '.join(duplicates)}",
            )

    except Exception as e:
        logging.error(f"Error checking host uniqueness: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error validating host uniqueness")