# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import asyncio
from reemote.main import main

class Deployment_uri:
    def execute(self):
        from reemote.operations.builtin.uri import Uri

        # Example 1: Check that you can connect (GET) to a page and it returns a status 200
        yield Uri(
            url="http://www.example.com",
            method="GET"
        )

if __name__ == "__main__":
    asyncio.run(main(Deployment_uri))


