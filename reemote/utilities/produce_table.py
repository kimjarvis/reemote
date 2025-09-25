import json

from reemote.utilities.generate_execution_results import generate_table


def produce_table(json_output: tuple[str, str]):
    # Ensure the output directory exists
    # output_dir = "development/output"
    # os.makedirs(output_dir, exist_ok=True)
    # Parse the JSON data first, then pass it to generate_table
    # Generate the RST table and write it to out.rst
    try:
        parsed_data = json.loads(json_output)
        table = generate_table(parsed_data)
        # rst_file_path = os.path.join(output_dir, "out.rst")
        # with open(rst_file_path, "w") as rst_file:
        #     rst_file.write(table)

    except json.JSONDecodeError as e:
        print("Error: Invalid JSON format.")
        raise e
    return table
