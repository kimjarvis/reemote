from typing import List, Tuple, Dict, Any

def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
    return [
        (
            {
                'host': 'localhost',
                'username': 'user',              # ssh User name
                'password': 'password'          # ssh Password
            },
            {
                'sudo_user': 'user',             # Sudo user name
                'sudo_password': 'password',    # Sudo Password
                'localhost': True               # This is the local host
            }
        )
    ]
