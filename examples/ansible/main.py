#!/usr/bin/python

from ansible.module_utils.basic import AnsibleModule

def run_module():
    # Define the module's parameters
    module_args = dict(
        name=dict(type='str', required=True),  # Name of the package
        state=dict(type='str', default='present', choices=['present', 'absent', 'latest']),  # Desired state
        disable_gpg_check=dict(type='bool', default=False)  # Optional: Disable GPG check
    )

    # Initialize the AnsibleModule object
    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True  # Allows --check mode
    )

    # Get the input parameters
    name = module.params['name']
    state = module.params['state']
    disable_gpg_check = module.params['disable_gpg_check']

    # Prepare arguments for the dnf module
    dnf_args = {
        'name': name,
        'state': state,
        'disable_gpg_check': disable_gpg_check
    }

    # Call the dnf module programmatically
    result = module.run_command(['dnf', 'install', '-y', name])

    # Check the result of the command
    if result[0] == 0:  # Exit code 0 indicates success
        module.exit_json(
            changed=True,
            msg=f"Package '{name}' successfully installed/updated.",
            stdout=result[1],
            stderr=result[2]
        )
    else:
        module.fail_json(
            msg=f"Failed to install/update package '{name}'.",
            stdout=result[1],
            stderr=result[2]
        )

def main():
    run_module()

if __name__ == '__main__':
    main()