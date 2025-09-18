from typing import List, Tuple, Dict, Any

def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
    return [({'host': 'localhost',  # Host
                'username': 'username',  # User name
                'password': 'password'  # Password
    },{})]
