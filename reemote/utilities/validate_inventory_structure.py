# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from typing import List, Tuple, Dict, Any

def validate_inventory_structure(inventory: List[Tuple[Dict[str, Any], Dict[str, str]]]) -> bool:
    """Validates the structure of an inventory data object.

    This function checks if the provided inventory data conforms to a specific
    nested structure. It ensures the top-level object is a list, and that
    each item within that list is a tuple containing exactly two dictionaries.

    This validation is purely structural and does not inspect the keys or
    values within the dictionaries themselves. The expected format is:
    `List[Tuple[Dict, Dict]]`.

    Args:
        inventory (List[Tuple[Dict[str, Any], Dict[str, str]]]): The inventory
            data structure to validate.

    Returns:
        bool: `True` if the inventory structure is valid, `False` otherwise.
    """
    # Check if the input is a list
    if not isinstance(inventory, list):
        return False

    # Iterate through each item in the list
    for item in inventory:
        # Check if the item is a tuple
        if not isinstance(item, tuple):
            return False

        # Check if the tuple has exactly two elements
        if len(item) != 2:
            return False

        # Check if both elements in the tuple are dictionaries
        if not (isinstance(item[0], dict) and isinstance(item[1], dict)):
            return False

    # If all checks pass, the structure is valid
    return True

