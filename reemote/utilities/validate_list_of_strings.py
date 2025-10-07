from typing import List


def validate_list_of_strings(x) -> List[str]:
    """
    Validates that input is a list of strings.
    
    Args:
        x: Input to validate. Must be a list of strings.
        
    Returns:
        List[str]: The validated list of strings.
        
    Raises:
        TypeError: If x is not a list, or if any list element is not a string.
        
    Examples:
        >>> validate_list_of_strings(["pkg1", "pkg2"])
        ['pkg1', 'pkg2']
        >>> validate_list_of_strings("package")
        TypeError: Input must be a list of strings
    """
    if not isinstance(x, list):
        raise TypeError("Input must be a list of strings")
    
    # Validate that all elements are strings
    if not all(isinstance(item, str) for item in x):
        raise TypeError("All elements in the list must be strings")
    
    return x