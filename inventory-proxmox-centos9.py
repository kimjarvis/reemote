from typing import List, Tuple, Dict, Any

def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
    return [
        (
            {
                'host': '192.168.1.39',  # images:debian/13 debian-0
                'username': 'kim',  # Kim Jarvis
                'password': 'xnjs',  # Password
            },
            {
                'sudo_user': 'kim',  # Sudo user
                'sudo_password': 'xnjs',  # Password
            }
        )
    ]
