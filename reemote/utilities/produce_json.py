import json
import os

from reemote.result import serialize_result


def produce_json(results) -> tuple[str, str]:

    json_output = json.dumps(results, default=serialize_result, indent=4)

    # # Ensure the output directory exists
    # output_dir = "development/output"
    # os.makedirs(output_dir, exist_ok=True)
    #
    # # Write the JSON output to out.json
    # json_file_path = os.path.join(output_dir, "out.json")
    # with open(json_file_path, "w") as json_file:
    #     json_file.write(json_output)
    return json_output
