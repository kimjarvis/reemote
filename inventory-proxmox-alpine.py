# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from typing import List, Tuple, Dict, Any

def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
    return [
        (
            {
                'host': '192.168.1.56',  # images:debian/13 debian-0
                'username': 'kim',  # Kim Jarvis
                'password': 'kim',  # Password
            },
            {
                'sudo_user': 'kim',  # Sudo user
                'sudo_password': 'kim',  # Password
            }
        )
    ]
