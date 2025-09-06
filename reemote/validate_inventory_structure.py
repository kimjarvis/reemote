from typing import List, Tuple, Dict, Any

def validate_inventory_structure(inventory: List[Tuple[Dict[str, Any], Dict[str, str]]]) -> bool:
    """
    Validates the structure of the inventory object.

    Args:
        inventory (List[Tuple[Dict[str, Any], Dict[str, str]]]): The inventory object to validate.

    Returns:
        bool: True if the structure is valid, False otherwise.
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

