# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import platform
from pathlib import Path
from typing import Optional
import os
import threading
from queue import Queue

from nicegui import events, ui


class local_file_picker(ui.dialog):

    def __init__(self, directory: str, *,
                 upper_limit: Optional[str] = ..., multiple: bool = False,
                 show_hidden_files: bool = False, max_files: int = 1000) -> None:
        super().__init__()

        self.path = Path(directory).expanduser()
        if upper_limit is None:
            self.upper_limit = None
        else:
            self.upper_limit = Path(directory if upper_limit == ... else upper_limit).expanduser()
        self.show_hidden_files = show_hidden_files
        self.max_files = max_files
        self.current_files = []
        self.loading = False

        with self, ui.card():
            # Add a loading indicator
            self.loading_indicator = ui.spinner(size='lg')
            self.loading_indicator.visible = False

            self.grid = ui.aggrid({
                'columnDefs': [{'field': 'name', 'headerName': 'File'}],
                'rowSelection': 'multiple' if multiple else 'single',
                'rowModelType': 'clientSide',
            }, html_columns=[0]).classes('w-96').on('cellDoubleClicked', self.handle_double_click)

            with ui.row().classes('w-full justify-end'):
                ui.button('Cancel', on_click=self.close).props('outline')
                ui.button('Ok', on_click=self._handle_ok)

        # Load files in a separate thread to avoid blocking
        self.load_files_async()

    def load_files_async(self):
        """Load files asynchronously to avoid blocking the main thread"""
        self.loading = True
        self.loading_indicator.visible = True
        self.grid.visible = False

        def load_files():
            try:
                files = []
                count = 0

                # Use scandir for efficient directory reading
                with os.scandir(self.path) as entries:
                    for entry in entries:
                        if count >= self.max_files:
                            break

                        if not self.show_hidden_files and entry.name.startswith('.'):
                            continue

                        files.append({
                            'name': f'📁 <strong>{entry.name}</strong>' if entry.is_dir() else entry.name,
                            'path': entry.path,
                            'is_dir': entry.is_dir()
                        })
                        count += 1

                # Sort directories first, then files
                files.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))

                # Add parent directory if applicable
                if (self.upper_limit is None and self.path != self.path.parent) or \
                        (self.upper_limit is not None and self.path != self.upper_limit):
                    files.insert(0, {
                        'name': '📁 <strong>..</strong>',
                        'path': str(self.path.parent),
                        'is_dir': True
                    })

                self.current_files = files

                # Update UI in main thread
                ui.timer(0.1, once=True, callback=self.update_grid_ui)

            except (PermissionError, OSError) as e:
                ui.timer(0.1, once=True, callback=lambda: self.show_error(f"Error reading directory: {e}"))

        # Start loading in background thread
        thread = threading.Thread(target=load_files, daemon=True)
        thread.start()

    def update_grid_ui(self):
        """Update the grid UI after files are loaded"""
        self.grid.options['rowData'] = self.current_files
        self.grid.update()
        self.loading = False
        self.loading_indicator.visible = False
        self.grid.visible = True

    def show_error(self, message):
        """Show error message"""
        self.loading = False
        self.loading_indicator.visible = False
        ui.notify(message)

    def handle_double_click(self, e: events.GenericEventArguments) -> None:
        new_path = Path(e.args['data']['path'])
        if e.args['data'].get('is_dir', False):
            self.path = new_path
            self.load_files_async()
        else:
            self.submit([str(new_path)])

    async def _handle_ok(self):
        rows = await self.grid.get_selected_rows()
        self.submit([r['path'] for r in rows])