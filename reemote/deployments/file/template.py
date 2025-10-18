# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
class Write_text_to_file:
    def execute(self):
        from reemote.operations.template import Template
        from reemote.utilities.template_render import TemplateRenderer
        from datetime import datetime

        template_dir = "/home/kim/reemote/reemote/deployments/nginx/templates"
        renderer = TemplateRenderer(template_dir)

        # Use the working discovery method and load builtin explicitly
        variables_files = renderer.discover_variables_files()
        template_vars = {}

        # Load each variables builtin explicitly
        for file_name, file_path in variables_files.items():
            print(f"Loading variables from: {file_path}")
            file_vars = renderer._load_yaml_variables(file_path)  # Directly call the loader
            template_vars.update(file_vars)
            print(f"Loaded {len(file_vars)} variables from {file_name}")

        print(f"Total variables loaded: {len(template_vars)}")
        print(f"Variable keys: {list(template_vars.keys())}")

        # Add template-specific variables
        template_vars.update({
            "date_time": 7,
            "datetime": 7,
            "ansible_date_time": {
                "iso8601": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
            },
            "ansible_hostname": "server",
            "ansible_os_family": "Linux"
        })

        yield Template(
            src="/home/kim/reemote/reemote/deployments/nginx/templates/nginx.conf.j2",
            dest="/etc/nginx/nginx.conf",
            vars=template_vars,
            attrs={"permissions": 0o644}
        )