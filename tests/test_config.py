import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from reemote.config import Config

# Import the Config class (adjust import as needed)
# from your_module import Config


@pytest.fixture
def temp_config_dir():
    """Fixture to create a temporary directory for config files"""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Store original data_dir
        original_data_dir = getattr(Config, 'data_dir', None)

        # Set temporary data_dir for testing
        Config.data_dir = Path(temp_dir) / ".reemote"

        yield temp_dir

        # Restore original data_dir
        if original_data_dir is not None:
            Config.data_dir = original_data_dir
        else:
            delattr(Config, 'data_dir')


@pytest.fixture
def valid_inventory_data():
    """Fixture providing valid inventory data"""
    return [
        [
            {
                "host": "192.168.1.76",
                "username": "user",
                "password": "password"
            },
            {
                "groups": ["all", "local"],
                "sudo_user": "user",
                "sudo_password": "password"
            }
        ],
        [
            {
                "host": "192.168.1.77",
                "username": "admin",
                "password": "admin123"
            },
            {
                "groups": ["all", "remote"],
                "sudo_user": "admin",
                "sudo_password": "admin123"
            }
        ]
    ]


def test_config_initialization(temp_config_dir):
    """Test that Config initializes properly"""
    config = Config()

    # Check if data directory was created
    assert Config.data_dir.exists()
    assert Config.data_dir.is_dir()

    # Check if config file was created
    assert config.config_path.exists()

    # Check if default files were created
    assert config.default_log_path.exists()
    assert config.default_inventory_path.exists()

    # Read config to verify defaults
    config_data = config._read_config()
    assert "logging" in config_data
    assert "inventory" in config_data
    assert config_data["logging"] == str(config.default_log_path)
    assert config_data["inventory"] == str(config.default_inventory_path)


def test_config_initialization_with_existing_config(temp_config_dir):
    """Test Config initialization when config file already exists"""
    # First, create a config with custom values
    Config.data_dir.mkdir(exist_ok=True)

    custom_config = {
        "logging": "/custom/path/to/log.log",
        "inventory": "/custom/path/to/inventory.json"
    }

    config_file = Config.data_dir / "config.json"
    with open(config_file, 'w') as f:
        json.dump(custom_config, f)

    # Initialize Config
    config = Config()

    # Should read existing config, not create new defaults
    config_data = config._read_config()
    assert config_data["logging"] == "/custom/path/to/log.log"
    assert config_data["inventory"] == "/custom/path/to/inventory.json"


def test_get_inventory_path(temp_config_dir):
    """Test get_inventory_path method"""
    config = Config()
    inventory_path = config.get_inventory_path()

    # Should return the default inventory path from config
    assert inventory_path == str(config.default_inventory_path)


def test_set_logging(temp_config_dir):
    """Test set_logging method"""
    config = Config()

    # Create a new logging path
    new_log_path = Path(temp_config_dir) / "custom.log"

    # Set the new logging path
    config.set_logging(str(new_log_path))

    # Verify it was set
    assert new_log_path.exists()
    logging_path = config.get_logging()
    assert logging_path == str(new_log_path)


def test_set_logging_with_nested_directory(temp_config_dir):
    """Test set_logging creates nested directories"""
    config = Config()

    # Test with nested directory
    nested_log_path = Path(temp_config_dir) / "logs" / "app" / "app.log"
    config.set_logging(str(nested_log_path))

    assert nested_log_path.parent.exists()
    assert nested_log_path.exists()


def test_get_logging(temp_config_dir):
    """Test get_logging method"""
    config = Config()

    # Should return default logging path initially
    logging_path = config.get_logging()
    assert logging_path == str(config.default_log_path)

    # Change it and verify get_logging returns new path
    new_log_path = Path(temp_config_dir) / "new.log"
    config.set_logging(str(new_log_path))

    updated_logging_path = config.get_logging()
    assert updated_logging_path == str(new_log_path)


def test_set_inventory_path_valid(temp_config_dir, valid_inventory_data):
    """Test set_inventory_path with valid inventory"""
    config = Config()

    # Create a valid inventory file
    inventory_file = Path(temp_config_dir) / "custom_inventory.json"

    with open(inventory_file, 'w') as f:
        json.dump(valid_inventory_data, f, indent=2)

    # Set the new inventory path
    config.set_inventory_path(str(inventory_file))

    # Verify it was set
    new_inventory_path = config.get_inventory_path()
    assert new_inventory_path == str(inventory_file)


def test_set_inventory_path_invalid_json(temp_config_dir):
    """Test set_inventory_path with invalid JSON"""
    config = Config()

    # Create a file with invalid JSON
    invalid_file = Path(temp_config_dir) / "invalid.json"
    with open(invalid_file, 'w') as f:
        f.write("{ invalid json }")

    # Should raise ValueError
    with pytest.raises(ValueError, match="Invalid JSON"):
        config.set_inventory_path(str(invalid_file))


def test_set_inventory_path_missing_file(temp_config_dir):
    """Test set_inventory_path with non-existent file"""
    config = Config()

    # Try to set non-existent file
    missing_file = Path(temp_config_dir) / "nonexistent.json"

    # Should raise ValueError
    with pytest.raises(ValueError, match="does not exist"):
        config.set_inventory_path(str(missing_file))


def test_set_inventory_path_invalid_structure(temp_config_dir):
    """Test set_inventory_path with invalid inventory structure"""
    config = Config()

    # Create a file with valid JSON but invalid structure
    invalid_structure_file = Path(temp_config_dir) / "bad_structure.json"
    invalid_data = {"not": "a", "valid": "inventory"}

    with open(invalid_structure_file, 'w') as f:
        json.dump(invalid_data, f)

    # Should raise ValueError
    with pytest.raises(ValueError, match="Inventory must be a list"):
        config.set_inventory_path(str(invalid_structure_file))


def test_set_inventory(temp_config_dir, valid_inventory_data):
    """Test set_inventory method with valid data"""
    config = Config()

    # Set the inventory data
    config.set_inventory(valid_inventory_data)

    # Verify the inventory file was written
    inventory_path = config.get_inventory_path()
    with open(inventory_path, 'r') as f:
        loaded_data = json.load(f)

    assert loaded_data == valid_inventory_data


def test__validate_json_file_valid(temp_config_dir):
    """Test _validate_json_file with valid JSON"""
    config = Config()

    # Create a valid JSON file
    valid_file = Path(temp_config_dir) / "valid.json"
    with open(valid_file, 'w') as f:
        json.dump({"key": "value"}, f)

    assert config._validate_json_file(str(valid_file)) is True


def test__validate_json_file_invalid(temp_config_dir):
    """Test _validate_json_file with invalid JSON"""
    config = Config()

    # Create an invalid JSON file
    invalid_file = Path(temp_config_dir) / "invalid.json"
    with open(invalid_file, 'w') as f:
        f.write("{ invalid json")

    assert config._validate_json_file(str(invalid_file)) is False


def test__validate_json_file_missing(temp_config_dir):
    """Test _validate_json_file with missing file"""
    config = Config()

    missing_file = Path(temp_config_dir) / "missing.json"
    assert config._validate_json_file(str(missing_file)) is False


def test__validate_inventory_json_valid(valid_inventory_data):
    """Test _validate_inventory_json with valid data"""
    config = Config()

    # Should not raise any exception
    config._validate_inventory_json(valid_inventory_data)


def test__validate_inventory_json_not_list():
    """Test _validate_inventory_json with non-list data"""
    config = Config()

    with pytest.raises(ValueError, match="Inventory must be a list"):
        config._validate_inventory_json({"not": "a list"})


def test__validate_inventory_json_invalid_entry_length():
    """Test _validate_inventory_json with entry that's not length 2"""
    config = Config()

    invalid_data = [
        [{"host": "test"}, {"groups": []}],  # Valid
        [{"host": "test"}]  # Invalid - only 1 item
    ]

    with pytest.raises(ValueError, match="must be a list of 2 items"):
        config._validate_inventory_json(invalid_data)


def test__validate_inventory_json_entry_not_dict():
    """Test _validate_inventory_json with entry items that are not dicts"""
    config = Config()

    invalid_data = [
        ["not a dict", {"groups": []}]
    ]

    with pytest.raises(ValueError, match="must be a dictionary"):
        config._validate_inventory_json(invalid_data)


def test__validate_inventory_json_missing_host_key():
    """Test _validate_inventory_json with missing host key"""
    config = Config()

    invalid_data = [
        [
            {"username": "user", "password": "pass"},  # Missing host
            {"groups": []}
        ]
    ]

    with pytest.raises(ValueError, match="must contain 'host' key"):
        config._validate_inventory_json(invalid_data)


def test__validate_inventory_json_missing_groups_key():
    """Test _validate_inventory_json with missing groups key"""
    config = Config()

    invalid_data = [
        [
            {"host": "test", "username": "user", "password": "pass"},
            {"not_groups": []}  # Missing groups
        ]
    ]

    with pytest.raises(ValueError, match="must contain 'groups' key"):
        config._validate_inventory_json(invalid_data)


def test_config_persistence(temp_config_dir):
    """Test that config changes persist between instances"""
    # Create first config instance and change inventory path
    config1 = Config()
    new_inventory_path = Path(temp_config_dir) / "persistence_test.json"

    # Create a valid inventory file
    with open(new_inventory_path, 'w') as f:
        json.dump([], f)

    config1.set_inventory_path(str(new_inventory_path))

    # Create second config instance
    config2 = Config()

    # Should have the same inventory path
    assert config2.get_inventory_path() == str(new_inventory_path)


def test_set_inventory_overwrites_existing(temp_config_dir, valid_inventory_data):
    """Test that set_inventory overwrites existing inventory file"""
    config = Config()

    # First set some data
    first_data = [[
        {"host": "old.host", "username": "old", "password": "old"},
        {"groups": ["old"]}
    ]]

    config.set_inventory(first_data)

    # Then set new data
    config.set_inventory(valid_inventory_data)

    # Verify new data was written
    inventory_path = config.get_inventory_path()
    with open(inventory_path, 'r') as f:
        loaded_data = json.load(f)

    assert loaded_data == valid_inventory_data
    assert loaded_data != first_data


@patch('json.dump')
def test_set_inventory_json_error(mock_json_dump, temp_config_dir, valid_inventory_data):
    """Test set_inventory handles JSON write errors"""
    mock_json_dump.side_effect = Exception("JSON write error")

    config = Config()

    # Should propagate the exception
    with pytest.raises(Exception, match="JSON write error"):
        config.set_inventory(valid_inventory_data)


def test_config_initialization_with_existing_config(temp_config_dir):
    """Test Config initialization when config file already exists"""
    # Set temporary data_dir
    original_data_dir = getattr(Config, "data_dir", None)
    Config.data_dir = Path(temp_config_dir) / ".reemote"

    # Create the data directory
    Config.data_dir.mkdir(exist_ok=True)

    custom_config = {
        "logging": "/custom/path/to/log.log",
        "inventory": "/custom/path/to/inventory.json",
    }

    config_file = Config.data_dir / "config.json"
    with open(config_file, "w") as f:
        json.dump(custom_config, f)

    # Initialize Config
    config = Config()
    print(config)

    # Should read existing config, not create new defaults
    config_data = config._read_config()
    assert config_data["logging"] == "/custom/path/to/log.log"
    assert config_data["inventory"] == "/custom/path/to/inventory.json"

    # Restore original data_dir
    if original_data_dir is not None:
        Config.data_dir = original_data_dir



def test_get_logging(temp_config_dir):
    """Test get_logging method"""
    # Set temporary data_dir
    original_data_dir = getattr(Config, "data_dir", None)
    Config.data_dir = Path(temp_config_dir) / ".reemote"

    config = Config()

    # Should return default logging path initially
    logging_path = config.get_logging()
    expected_default_log_path = str(Path(temp_config_dir) / ".reemote" / "reemote.log")
    assert logging_path == expected_default_log_path

    # Change it and verify get_logging returns new path
    new_log_path = Path(temp_config_dir) / "new.log"
    config.set_logging(str(new_log_path))

    updated_logging_path = config.get_logging()
    assert updated_logging_path == str(new_log_path)

    # Restore original data_dir
    if original_data_dir is not None:
        Config.data_dir = original_data_dir

def test_set_inventory(temp_config_dir, valid_inventory_data):
    """Test set_inventory method with valid data"""
    # Set temporary data_dir
    original_data_dir = getattr(Config, "data_dir", None)
    Config.data_dir = Path(temp_config_dir) / ".reemote"

    config = Config()

    # Set the inventory data
    config.set_inventory(valid_inventory_data)

    # Verify the inventory file was written
    inventory_path = config.get_inventory_path()
    with open(inventory_path, "r") as f:
        loaded_data = json.load(f)

    assert loaded_data == valid_inventory_data

    # Restore original data_dir
    if original_data_dir is not None:
        Config.data_dir = original_data_dir


def test__validate_inventory_json_invalid_entry_length():
    """Test _validate_inventory_json with entry that's not length 2"""
    config = Config()

    invalid_data = [
        [{"host": "test", "username": "user", "password": "pass"}, {"groups": []}],  # Valid
        [{"host": "test", "username": "user", "password": "pass"}],  # Invalid - only 1 item
    ]

    with pytest.raises(ValueError, match="must be a list of 2 items"):
        config._validate_inventory_json(invalid_data)


def test_set_inventory_overwrites_existing(temp_config_dir, valid_inventory_data):
    """Test that set_inventory overwrites existing inventory file"""
    # Set temporary data_dir
    original_data_dir = getattr(Config, "data_dir", None)
    Config.data_dir = Path(temp_config_dir) / ".reemote"

    config = Config()

    # First set some data
    first_data = [
        [
            {"host": "old.host", "username": "old", "password": "old"},
            {"groups": ["old"]},
        ]
    ]

    config.set_inventory(first_data)

    # Then set new data
    config.set_inventory(valid_inventory_data)

    # Verify new data was written
    inventory_path = config.get_inventory_path()
    with open(inventory_path, "r") as f:
        loaded_data = json.load(f)

    assert loaded_data == valid_inventory_data
    assert loaded_data != first_data

    # Restore original data_dir
    if original_data_dir is not None:
        Config.data_dir = original_data_dir


@pytest.mark.parametrize("valid_inventory_data", [
    [[{'host': '192.168.1.76', 'password': 'password', 'username': 'user'}, {'groups': ['all', 'local'], 'sudo_password': 'password', 'sudo_user': 'user'}]],
    [[{'host': '10.0.0.1', 'password': 'admin123', 'username': 'admin'}, {'groups': ['all', 'remote'], 'sudo_password': 'admin123', 'sudo_user': 'admin'}]]
])
def test_set_inventory_json_error(temp_config_dir, valid_inventory_data):
    """Test set_inventory handles JSON write errors."""
    # Create a temporary file path
    temp_file = Path(temp_config_dir) / "test.json"
    temp_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file.touch()

    # Initialize Config
    config = Config()

    # Mock json.dump specifically for the set_inventory method
    with patch("json.dump") as mock_json_dump:
        # Configure the mock to raise an exception
        mock_json_dump.side_effect = Exception("JSON write error")

        # Attempt to set inventory data
        with pytest.raises(Exception, match="JSON write error"):
            config.set_inventory(valid_inventory_data)

        # Ensure the mock was called once
        mock_json_dump.assert_called_once()