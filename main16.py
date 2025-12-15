import asyncio
from pathlib import Path

import asyncssh

async def run_client():
    try:
        # Explicit parameters
        await asyncssh.scp(
            srcpaths=[('192.168.1.76', '/home/user/main14.py')],
            dstpath=Path('/tmp/main15.py'),
            username="user",
        )
        print("File transferred successfully!")

    except Exception as exc:
        print(f"Error: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()

asyncssh.set_debug_level(1)
asyncio.run(run_client())