import asyncio
import subprocess

import pytest

from reemote.config import Config
from reemote.inventory import Authentication, Connection, Inventory, InventoryItem


@pytest.fixture
def setup_inventory():
    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                ),
                authentication=Authentication(sudo_password="password"),
                groups=["all", "server104"],
            ),
        ]
    )
    config = Config()
    config.set_inventory(inventory.to_json_serializable())


@pytest.fixture
def setup_directory():
    async def inner_fixture():
        """
        Asynchronous inner fixture to rsync the tests/testdata directory to remote hosts.
        """
        # Define source, target, and remote hosts
        source_directory = "tests/testdata"  # Ensure no trailing slash
        target_directory = "/home/user/"  # Ensure trailing slash for clarity
        remote_hosts = ["server104", "server105"]

        async def rsync_to_remote_hosts(source_dir, target_dir, hosts):
            """
            Rsyncs a directory to multiple remote hosts asynchronously.
            """
            for host in hosts:
                # Build the rsync command
                command = [
                    "rsync",
                    "-rltv",  # Recursive, copy symlinks, preserve times, verbose
                    source_dir,
                    f"{host}:{target_dir}",
                ]
                try:
                    # Execute the rsync command
                    print(f"Syncing to {host}...")
                    await asyncio.to_thread(subprocess.run, command, check=True)
                    print(f"Successfully synced to {host}.")
                except subprocess.CalledProcessError as e:
                    print(f"Failed to sync to {host}: {e}")
                    raise  # Re-raise the exception to fail the test session

        # Perform the rsync operation
        print("Setting up rsync for test session...")
        await rsync_to_remote_hosts(source_directory, target_directory, remote_hosts)
        print("Rsync setup complete.")

    # Run the asynchronous inner fixture using asyncio
    return asyncio.run(inner_fixture())
