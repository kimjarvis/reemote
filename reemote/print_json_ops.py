import json


def print_json_ops(host_ops):
    pretty_output = json.dumps(host_ops, indent=4)
    # Print the formatted output
    print(pretty_output)
