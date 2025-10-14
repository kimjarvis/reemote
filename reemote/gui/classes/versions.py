# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from nicegui import ui


class Versions:
    def __init__(self):
        self.columns = []
        self.rows = []

    def get_versions(self, responses):
        host_packages = []
        host_names = []

        for i, r in enumerate(responses):
            # print(r.cp.stdout)
            host_name = r.host
            host_names.append(host_name)
            pkg_dict = {}
            for v in r.cp.stdout:
                pkg_dict[v["name"]] = v["version"]
            host_packages.append(pkg_dict)

        # print(host_packages)
        # print(host_names)

        # Get all unique package names across all hosts
        all_package_names = set()
        for pkg_dict in host_packages:
            all_package_names.update(pkg_dict.keys())
        all_package_names = sorted(all_package_names)
        # print(all_package_names)

        # Build column definitions: Name + one per host
        columnDefs = [
            {"headerName": "Package Name", "field": "name", 'filter': 'agTextColumnFilter', 'floatingFilter': True}]
        for host_name in host_names:
            columnDefs.append({"headerName": host_name, "field": host_name.replace(".", "_")})

        # Build row data
        rowData = []
        for pkg_name in all_package_names:
            row = {"name": pkg_name}
            for i, host_name in enumerate(host_names):
                row[host_name.replace(".", "_")] = host_packages[i].get(pkg_name, "")  # empty if not installed
            rowData.append(row)

        self.columns = columnDefs
        self.rows = rowData

    @ui.refreshable
    def version_report(self):
        return ui.label("Version Report"),ui.aggrid({
            'columnDefs': self.columns,
            'rowData': self.rows,
        }).classes('max-h-40  overflow-y-auto')
