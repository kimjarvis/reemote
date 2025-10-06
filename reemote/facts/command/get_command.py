from reemote.command import Command

class Get_command:
    def __repr__(self):
        return f"Get_command()"

    async def _get_command_callback(self, host_info, global_info, command, cp, caller):
        return command

    def execute(self):
        r = yield Command(
            f"{self}",
            local=True,
            callback=self._get_command_callback,
            caller=self
        )