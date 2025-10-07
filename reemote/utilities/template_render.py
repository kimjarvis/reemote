import yaml
import json
import os
from pathlib import Path


class TemplateRenderer:
    def __init__(self, template_dir):
        self.template_dir = template_dir

    def discover_variables_files(self):
        """Discover available variables files in the template directory."""
        template_path = Path(self.template_dir)
        variables_files = {}

        # Look for YAML variables files
        for yaml_file in template_path.glob("*.vars.yml"):
            variables_files[yaml_file.stem.replace('.vars', '')] = str(yaml_file)

        for yaml_file in template_path.glob("*.vars.yaml"):
            variables_files[yaml_file.stem.replace('.vars', '')] = str(yaml_file)

        # Look for JSON variables files
        for json_file in template_path.glob("*.vars.json"):
            variables_files[json_file.stem.replace('.vars', '')] = str(json_file)

        print("template directory",self.template_dir)
        print("variables_files",variables_files)
        return variables_files

    def _load_yaml_variables(self, file_path):
        """Load variables from YAML file and return as dictionary."""
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
        """Load variables from JSON file and return as dictionary."""
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
        """Get variables dictionary from file with optional overrides."""
        # Load base variables from file
        template_vars = self.load_variables(variables_file)

        # Merge with additional variables (additional_vars takes precedence)
        if additional_vars:
            template_vars.update(additional_vars)

        return template_vars

    def debug_variables(self, variables_file=None):
        """Debug method to inspect variables before rendering."""
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