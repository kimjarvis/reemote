import sys
import argparse

from reemote.utilities.make_inventory import make_inventory


def parse_arguments():
    """Parse command line arguments using argparse."""
    parser = argparse.ArgumentParser(
        description='Create an inventory file for VM configuration',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example usage:
  python script.py --inventory_filename /home/kim/inventory_debian-1.py \\
                   --image 'images:debian/13' \\
                   --vm debian-1 \\
                   --name 'Kim Jarvis' \\
                   --user kim \\
                   --user_password passwd \\
                   --root_password secret \\
                   --ip_address 10.4.6.78
        """
    )

    # Add all required arguments
    parser.add_argument(
        '--inventory_filename',
        type=str,
        required=True,
        help='Path to the inventory file (e.g., /home/kim/inventory_debian-1.py)'
    )

    parser.add_argument(
        '--image',
        type=str,
        required=True,
        help='VM image description (e.g., images:debian/13)'
    )

    parser.add_argument(
        '--vm',
        type=str,
        required=True,
        help='VM name (e.g., debian-1)'
    )

    parser.add_argument(
        '--name',
        type=str,
        required=True,
        help="User's full name (e.g., 'Kim Jarvis')"
    )

    parser.add_argument(
        '--user',
        type=str,
        required=True,
        help='Username (e.g., kim)'
    )

    parser.add_argument(
        '--user_password',
        type=str,
        required=True,
        help='User password (e.g., passwd)'
    )

    parser.add_argument(
        '--root_password',
        type=str,
        required=True,
        help='Root password (e.g., secret)'
    )

    parser.add_argument(
        '--ip_address',
        type=str,
        required=True,
        help='IP address of the VM (e.g., 10.4.6.78)'
    )

    return parser.parse_args()


def main():
    """Main function to demonstrate the make_inventory function with command line arguments."""
    # Parse command line arguments
    args = parse_arguments()

    print("Creating inventory file with the following parameters:")
    print(f"  inventory_filename: {args.inventory_filename}")
    print(f"  image: {args.image}")
    print(f"  vm: {args.vm}")
    print(f"  name: {args.name}")
    print(f"  user: {args.user}")
    print(f"  user_password: {args.user_password}")
    print(f"  root_password: {args.root_password}")
    print(f"  ip_address: {args.ip_address}")
    print()

    # Call the function with parsed arguments
    try:
        make_inventory(
            inventory_filename=args.inventory_filename,
            image=args.image,
            vm=args.vm,
            name=args.name,
            user=args.user,
            user_password=args.user_password,
            root_password=args.root_password,
            ip_address=args.ip_address
        )

        print("\nInventory file created successfully!")

        # Display the content that was written
        print("\nContent of the inventory file:")
        print("-" * 50)
        with open(args.inventory_filename, 'r') as f:
            print(f.read())

    except Exception as e:
        print(f"Failed to create inventory file: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()