from typing import List, Tuple, Dict, Any


def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
    return [
        (
            {
                'host': '192.168.122.47',  # alpine
                'username': 'youruser',  # User name
                'password': 'yourpassword'  # Password
            },
            {
                'su_user': 'root',
                'su_password': 'rootuser'  # Password
            }
        ),
        (
            {
                'host': '192.168.122.24',  # alpine
                'username': 'youruser',  # User name
                'password': 'yourpassword'  # Password
            },
            {
                'su_user': 'root',
                'su_password': 'rootuser'  # Password
            }
        )
    ]
