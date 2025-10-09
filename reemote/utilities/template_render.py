import yaml
import json
import os
from pathlib import Path


class TemplateRenderer:
    def __init__(self, template_dir):
        self.template_dir = template_dir

    def discover_variables_files(self):
        """Discovers available variable file in the template directory.

        This method scans the `template_dir` for file that follow a specific
        naming convention: `*.vars.yml`, `*.vars.yaml`, or `*.vars.json`.
        It then creates a dictionary that maps a "short name" (the filename
        without the `.vars.ext` suffix) to the file's full path.

        This is useful for presenting a list of available variable sets to a user.

        Returns:
            dict[str, str]: A dictionary where keys are the base names of the
                            variable file and values are their full file paths.
        """
        template_path = Path(self.template_dir)
        variables_files = {}

        # Look for YAML variables file
        for yaml_file in template_path.glob("*.vars.yml"):
            variables_files[yaml_file.stem.replace('.vars', '')] = str(yaml_file)

        for yaml_file in template_path.glob("*.vars.yaml"):
            variables_files[yaml_file.stem.replace('.vars', '')] = str(yaml_file)

        # Look for JSON variables file
        for json_file in template_path.glob("*.vars.json"):
            variables_files[json_file.stem.replace('.vars', '')] = str(json_file)

        print("template directory",self.template_dir)
        print("variables_files",variables_files)
        return variables_files

    def _load_yaml_variables(self, file_path):
        """Loads variables from a specified YAML file.

         This private helper method opens and parses a YAML file using
         `yaml.safe_load`. It returns an empty dictionary if the file is empty.
         It provides specific error handling for parsing and file-read issues.

         Args:
             file_path (str): The full path to the YAML file to load.

         Returns:
             dict: A dictionary containing the variables loaded from the file.

         Raises:
             Exception: If the file cannot be read or if a YAML parsing
                        error occurs.
         """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                variables = yaml.safe_load(f) or {}
            print(f"✓ Loaded {len(variables)} variables from {file_path}")
            return variables
        except yaml.YAMLError as e:
            raise Exception(f"YAML parsing error in {file_path}: {e}")
        except Exception as e:
            raise Exception(f"Failed to read {file_path}: {e}")

    def _load_json_variables(self, file_path):
        """Loads variables from a specified JSON file.

          This private helper method opens and parses a JSON file. It provides
          specific error handling for JSON decoding errors and file-read issues.

          Args:
              file_path (str): The full path to the JSON file to load.

          Returns:
              dict: A dictionary containing the variables loaded from the file.

          Raises:
              Exception: If the file cannot be read or if a JSON parsing
                         error occurs.
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                variables = json.load(f)
            print(f"✓ Loaded {len(variables)} variables from {file_path}")
            return variables
        except json.JSONDecodeError as e:
            raise Exception(f"JSON parsing error in {file_path}: {e}")
        except Exception as e:
            raise Exception(f"Failed to read {file_path}: {e}")

    def get_variables(self, variables_file=None, additional_vars=None):
        """Gets a combined dictionary of variables from a file and optional overrides.

         This function loads variables from a specified file (if provided) and
         then merges them with a dictionary of additional variables. If a key
         exists in both the file and `additional_vars`, the value from
         `additional_vars` will take precedence.

         Note:
             This method relies on a `self.load_variables` method (not defined
             in this snippet) to correctly dispatch to the YAML or JSON loader
             based on the file extension.

         Args:
             variables_file (str, optional): The path to the variables file
                 (YAML or JSON) to load. Defaults to None.
             additional_vars (dict, optional): A dictionary of variables to
                 merge. These will override any variables with the same key
                 from the loaded file. Defaults to None.

         Returns:
             dict: A single dictionary containing the merged variables.
         """
        template_vars = self.load_variables(variables_file)

        # Merge with additional variables (additional_vars takes precedence)
        if additional_vars:
            template_vars.update(additional_vars)

        return template_vars

    def debug_variables(self, variables_file=None):
        """Prints a debug report for variables, highlighting complex types.

         This utility method is designed for inspecting the variables that will
         be used for rendering. It loads the variables and prints a detailed
         report to the console, which includes:

         - The total number of variables.
         - A list of all variable keys.
         - A detailed breakdown of each variable's key, type, and value.
         - Special warnings for any variables that are dictionaries or lists
           containing dictionaries, as these can be complex in templates.

         Args:
             variables_file (str, optional): The path to the variables file
                 to load for debugging. Defaults to None.

         Returns:
             dict: The dictionary of variables that was inspected, allowing for
                   further use or method chaining.
         """
        template_vars = self.get_variables(variables_file)

        print("=== VARIABLES DEBUG INFO ===")
        print(f"Total variables: {len(template_vars)}")
        print(f"Variable keys: {list(template_vars.keys())}")

        # Check for any dictionaries in the variables
        dict_vars = {}
        list_with_dicts = {}

        for key, value in template_vars.items():
            print(f"  {key}: {type(value).__name__} = {value}")

            if isinstance(value, dict):
                dict_vars[key] = value
                print(f"⚠️  Dictionary variable found: {key}")
            elif isinstance(value, list):
                # Check if list contains dictionaries
                dict_in_list = [item for item in value if isinstance(item, dict)]
                if dict_in_list:
                    list_with_dicts[key] = dict_in_list
                    print(f"⚠️  List with dictionaries found: {key} contains {len(dict_in_list)} dict(s)")

        if dict_vars:
            print(f"Found {len(dict_vars)} dictionary variables: {list(dict_vars.keys())}")
        if list_with_dicts:
            print(f"Found {len(list_with_dicts)} lists containing dictionaries: {list(list_with_dicts.keys())}")

        print("=============================")
        return template_vars