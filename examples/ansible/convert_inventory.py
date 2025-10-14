import argparse
import importlib.util
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Any


def load_inventory_from_file(file_path: str) -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
    """
    Load inventory data from a Python file
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Inventory file not found: {file_path}")

    # Import the module dynamically
    module_name = file_path.stem
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None:
        raise ImportError(f"Could not load module from {file_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module

    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise ImportError(f"Error executing module {file_path}: {e}")

    # Get the inventory function
    if not hasattr(module, 'inventory'):
        raise AttributeError(f"Inventory file {file_path} must contain an 'inventory()' function")

    inventory_func = module.inventory

    # Call the function and return the data
    try:
        return inventory_func()
    except Exception as e:
        raise RuntimeError(f"Error calling inventory() function: {e}")


def convert_to_ansible_inventory(inventory_data: List[Tuple[Dict[str, Any], Dict[str, str]]]) -> Dict[str, Any]:
    """
    Convert the inventory format to Ansible-compatible format
    """
    ansible_hosts = {}

    for i, (ssh_config, sudo_config) in enumerate(inventory_data):
        # Extract hostname from comment or generate one
        host_comment = None
        for key, value in ssh_config.items():
            if isinstance(value, str) and '#' in value:
                comment_part = value.split('#')[-1].strip()
                if comment_part and not comment_part.startswith(('host', 'username', 'password')):
                    host_comment = comment_part
                    break

        # Generate hostname if no comment found
        if host_comment:
            hostname = host_comment.split()[-1]  # Take last part as hostname
        else:
            hostname = f"host-{i + 1}"

        # Clean up hostname (remove special characters that might cause issues)
        hostname = hostname.replace(':', '-').replace('/', '-').replace(' ', '-')

        # Create Ansible host configuration
        ansible_hosts[hostname] = {
            'ansible_host': ssh_config['host'].split('#')[0].strip() if '#' in ssh_config['host'] else ssh_config[
                'host'],
            'ansible_user': ssh_config['username'].split('#')[0].strip() if '#' in ssh_config['username'] else
            ssh_config['username'],
            'ansible_ssh_pass': ssh_config['password'].split('#')[0].strip() if '#' in ssh_config['password'] else
            ssh_config['password'],
            'ansible_become_user': sudo_config.get('sudo_user', 'root').split('#')[0].strip() if '#' in sudo_config.get(
                'sudo_user', 'root') else sudo_config.get('sudo_user', 'root'),
            'ansible_become_pass': sudo_config.get('sudo_password', '').split('#')[0].strip() if '#' in sudo_config.get(
                'sudo_password', '') else sudo_config.get('sudo_password', ''),
            'ansible_become_method': 'sudo'
        }

    return ansible_hosts


def generate_yaml_inventory(ansible_hosts: Dict[str, Any]) -> str:
    """
    Generate YAML format inventory
    """
    yaml_content = "# Ansible Inventory - Generated from Python inventory\n---\nall:\n  hosts:\n"

    for hostname, config in ansible_hosts.items():
        yaml_content += f"    {hostname}:\n"
        for key, value in config.items():
            yaml_content += f"      {key}: {value}\n"

    return yaml_content


def save_yaml_inventory(ansible_hosts: Dict[str, Any], output_file: str = "inventory.yaml"):
    """
    Save inventory in YAML format to file
    """
    yaml_content = generate_yaml_inventory(ansible_hosts)

    with open(output_file, 'w') as f:
        f.write(yaml_content)

    print(f"Inventory successfully written to: {output_file}")

    # Also print to console
    print("\nGenerated inventory content:")
    print("=" * 50)
    print(yaml_content)


def main():
    """
    Main function to convert inventory
    """
    parser = argparse.ArgumentParser(description='Convert Python inventory to Ansible YAML format')
    parser.add_argument('-i', '--inventory', required=True,
                        help='Path to Python file containing inventory data')
    parser.add_argument('-o', '--output', default='inventory.yaml',
                        help='Output file name (default: inventory.yaml)')

    args = parser.parse_args()

    try:
        # Load inventory data from the specified file
        print(f"Loading inventory from: {args.inventory}")
        inventory_data = load_inventory_from_file(args.inventory)

        # Convert to Ansible format
        ansible_hosts = convert_to_ansible_inventory(inventory_data)

        # Save to YAML file
        save_yaml_inventory(ansible_hosts, args.output)

        print(f"\nSuccessfully converted {len(ansible_hosts)} hosts to {args.output}")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()