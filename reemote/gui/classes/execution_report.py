from nicegui import ui


class Execution_report:
    def __init__(self):
        self.columns = [{'headerName': 'Command', 'field': 'command'}]
        self.rows = []

    def set(self, columns, rows):
        self.columns = columns
        self.rows = rows

    @ui.refreshable
    def execution_report(self):
        return ui.label("Execution Report"),ui.aggrid({
            'columnDefs': self.columns,
            'rowData': self.rows,
        }).classes('max-h-40  overflow-y-auto')
