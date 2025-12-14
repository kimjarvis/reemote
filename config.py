import json
from pathlib import Path
from typing import Any, Dict, List, Union

# Import validation functions
from inventory import check_host_uniqueness_across_database, validate_host_parameter


class Config:
    # Default data directory (can be overridden)
    data_dir = Path.home() / ".reemote"

    @property
    def config_path(self) -> Path:
        """Dynamic property for the configuration file path."""
        return self.data_dir / "config.json"

    @property
    def default_log_path(self) -> Path:
        """Dynamic property for the default logging file path."""
        return self.data_dir / "reemote.log"

    @property
    def default_inventory_path(self) -> Path:
        """Dynamic property for the default inventory file path."""
        return self.data_dir / "inventory.json"

    def __init__(self):
        # Create the data directory if it doesn't exist
        self.data_dir.mkdir(exist_ok=True)

        # Check if the config file exists; initialize it if it doesn't
        if not self.config_path.exists():
            self._initialize_config_file()

            # Create default log and inventory files if they don't exist
            self._create_default_files()

    def _initialize_config_file(self) -> None:
        """Initialize the configuration file with dynamic paths."""
        config_data = {
            "logging": str(self.default_log_path),
            "inventory": str(self.default_inventory_path),
        }
        with open(self.config_path, "w") as f:
            json.dump(config_data, f, indent=2)

    def _create_default_files(self) -> None:
        """Create default log and inventory files if they don't exist."""
        # Create empty log file
        if not self.default_log_path.exists():
            self.default_log_path.touch()

        # Create empty inventory JSON file
        if not self.default_inventory_path.exists():
            with open(self.default_inventory_path, "w") as f:
                json.dump([], f)

    def _read_config(self) -> Dict[str, Any]:
        """Read and return the configuration data from the config file."""
        if not self.config_path.exists():
            # If config file doesn't exist, create it with defaults
            self._initialize_config_file()

        try:
            with open(self.config_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            # If config file is corrupted or missing, recreate it
            self._initialize_config_file()
            with open(self.config_path, "r") as f:
                return json.load(f)

    def _write_config(self, config_data: Dict[str, Any]) -> None:
        """Write configuration data to the config file."""
        with open(self.config_path, "w") as f:
            json.dump(config_data, f, indent=2)

    def get_inventory_path(self) -> str:
        """Returns the inventory path from the config file."""
        config_data = self._read_config()
        return config_data.get("inventory", str(self.default_inventory_path))

    def set_inventory(self, inventory_data: List) -> None:
        """Write inventory data to the current inventory file."""
        inventory_path = self.get_inventory_path()

        # Ensure the directory exists
        Path(inventory_path).parent.mkdir(parents=True, exist_ok=True)

        # Validate the data before writing
        self._validate_inventory_json(inventory_data)

        # Write the JSON content to the file
        with open(inventory_path, "w") as f:
            json.dump(inventory_data, f, indent=2)

    def set_inventory_path(self, inventory_path: str) -> None:
        """Replaces the inventory path in the config file."""
        inventory_path_obj = Path(inventory_path)

        # Check if file exists
        if not inventory_path_obj.exists():
            raise ValueError(f"Inventory file does not exist: {inventory_path}")

        # Validate JSON structure
        if not self._validate_json_file(inventory_path):
            raise ValueError(f"Invalid JSON file: {inventory_path}")

        # Load and validate inventory data
        with open(inventory_path, "r") as f:
            try:
                inventory_data = json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in inventory file: {e}")

        # Validate inventory structure using imported functions
        self._validate_inventory_json(inventory_data)

        # Update config with new path
        config_data = self._read_config()
        config_data["inventory"] = str(inventory_path)
        self._write_config(config_data)

    def get_logging(self) -> str:
        """Returns the logging path from the config file."""
        config_data = self._read_config()
        return config_data.get("logging", str(self.default_log_path))

    def set_logging(self, logging_path: str) -> None:
        """Replaces the logging path in the config file."""
        config_data = self._read_config()
        config_data["logging"] = str(logging_path)

        logging_path_obj = Path(logging_path)

        # Create parent directories if they don't exist
        logging_path_obj.parent.mkdir(parents=True, exist_ok=True)

        # Create an empty file if it doesn't exist
        if not logging_path_obj.exists():
            logging_path_obj.touch()

        self._write_config(config_data)

    @staticmethod
    def _validate_json_file(file_path: str) -> bool:
        """
        Validate that a file contains valid JSON.

        Args:
            file_path: Path to the JSON file

        Returns:
            True if valid JSON, False otherwise
        """
        try:
            with open(file_path, "r") as f:
                json.load(f)
            return True
        except (json.JSONDecodeError, FileNotFoundError):
            return False

    @staticmethod
    def _validate_inventory_json(inventory_data: Any) -> None:
        """
        Validate inventory JSON structure.

        Args:
            inventory_data: Parsed inventory JSON data

        Raises:
            ValueError: If inventory data is invalid
        """
        # Check if it's a list
        if not isinstance(inventory_data, list):
            raise ValueError("Inventory must be a list of entries")

        # Validate each entry
        for entry in inventory_data:
            if not isinstance(entry, list) or len(entry) != 2:
                raise ValueError("Each inventory entry must be a list of 2 items")

            # Each item in the entry should be a dictionary
            if not isinstance(entry[0], dict) or not isinstance(entry[1], dict):
                raise ValueError("Each entry item must be a dictionary")

            # First dictionary must contain host, username, password
            required_keys = ["host", "username", "password"]
            for key in required_keys:
                if key not in entry[0]:
                    raise ValueError(f"First dictionary must contain '{key}' key")

            # Second dictionary must contain groups
            if "groups" not in entry[1]:
                raise ValueError("Second dictionary must contain 'groups' key")

            # Validate using the imported functions
            data_as_list = [(entry[0], entry[1])]
            validate_host_parameter(data_as_list)

            # Check host uniqueness (for this single entry)
            host_value = entry[0]["host"]
            check_host_uniqueness_across_database({host_value})