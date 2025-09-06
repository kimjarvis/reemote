from typing import List, Tuple, Dict, Any

def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
    return [
        (
            {
                'host': '192.168.122.197',     # debian
                'username': 'joe',             # User name
                'password': 'passwd'               # Password
            },
            {
                'sudo_password': 'passwd'       # Password
            }
        ),
        (
            {
                'host': '192.168.122.7',         # ubuntu
                'username': 'anne',             # Another user
                'client_keys': ['/home/anne/.ssh/id_ed25519'],  # Path to the private SSH key
                'passphrase': 'secret'            # Password for the SSH key
            },
            {
                'sudo_password': 'secret'       # Password
            }
        )
    ]
