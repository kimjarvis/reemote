def construct_host_ops(operations, results):
    """
    Construct a list of dictionaries grouping operations and results by host.

    Args:
        operations (list): List of Operation objects.
        results (list): List of Result objects.

    Returns:
        list: A list of dictionaries in the desired format.
    """
    # Dictionary to group operations and results by host
    host_dict = {}

    # Iterate over operations and results simultaneously
    for operation, result in zip(operations, results):
        host = operation.host_info['host']  # Extract host from operation
        stdout = result.cp.stdout.strip() if result.cp.stdout else "<no stdout>"  # Extract stdout from result

        # Group by host
        if host not in host_dict:
            host_dict[host] = []
        host_dict[host].append(
            ({"command": operation.command},
             {"stdout": stdout, "returncode": result.cp.returncode,
              "changed": result.changed}))

    # Convert the dictionary into the desired list of dictionaries format
    host_ops_list = [{"host": host, "ops": ops} for host, ops in host_dict.items()]

    return host_ops_list
