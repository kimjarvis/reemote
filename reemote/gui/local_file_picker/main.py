# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
#!/usr/bin/env python3
from local_file_picker import local_file_picker

from nicegui import ui, native


async def pick_file() -> None:
    result = await local_file_picker('~', multiple=True)
    ui.notify(f'You chose {result}')


@ui.page('/')
def index():
    ui.button('Choose builtin', on_click=pick_file, icon='folder')


ui.run(title="File Controller", reload=False, port=native.find_open_port(),
        storage_secret='private key to secure the browser session cookie')