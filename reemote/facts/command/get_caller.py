from reemote.command import Command

class Get_caller:
    def __repr__(self):
        return f"Get_caller()"

    async def _get_caller_callback(self, host_info, global_info, command, cp, caller):
        return caller

    def execute(self):
        r = yield Command(
            f"{self}",
            local=True,
            callback=self._get_caller_callback,
            caller=self
        )