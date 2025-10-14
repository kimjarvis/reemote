# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from typing import List


def validate_list_of_strings(x) -> List[str]:
    """Verifies that the input is a list containing only string elements.

    This function acts as a type-guard, ensuring that an input value is
    a list composed exclusively of strings. It is primarily used to
    validate data before it is processed by other parts of an application
    that rely on this specific data structure.

    The function performs two key checks:
    - It verifies that the input `x` is a `list`.
    - It iterates through the list to confirm that every element is a `str`.

    If either check fails, a `TypeError` is raised with a message
    indicating the nature of the validation failure.

    Args:
        x (Any): The input value to be validated.

    Returns:
        List[str]: The original list, if it passes validation.

    Raises:
        TypeError: If the input is not a list, or if the list contains
            any non-string elements.

    Examples:
        >>> validate_list_of_strings(["pkg1", "pkg2"])
        ['pkg1', 'pkg2']
        >>> validate_list_of_strings("package")
        Traceback (most recent call last):
        ...
        TypeError: Input must be a list of strings
        >>> validate_list_of_strings(["item", 123])
        Traceback (most recent call last):
        ...
        TypeError: All elements in the list must be strings
    """
    if not isinstance(x, list):
        raise TypeError("Input must be a list of strings")
    
    # Validate that all elements are strings
    if not all(isinstance(item, str) for item in x):
        raise TypeError("All elements in the list must be strings")
    
    return x