# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from nicegui import ui

from reemote.gui.local_file_picker.local_file_picker import local_file_picker
from reemote.utilities.get_classes_in_source import get_classes_in_source

class Sources_upload:
    def __init__(self):
        self.source= "/"
        self._classes = []
        self.deployment = ""
        self.kwargs = ""

    @ui.refreshable
    def classes(self):
        return ui.select(self._classes).bind_value(self, 'deployment')

    def _kwargs(self):
        return ui.input(label='kwargs', placeholder='kwargs string').bind_value(self, 'kwargs')

    async def pick_file(self) -> None:
        result = await local_file_picker('~', multiple=False)
        ui.notify(f'Uploading file {result}')
        self.source = result[0]
        self._classes = get_classes_in_source(result[0])
        self.classes.refresh()
