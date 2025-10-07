from reemote.command import Command

class Get_cp:
    def __repr__(self):
        return f"Get_cp()"

    async def _get_cp_callback(self, host_info, global_info, command, cp, caller):
        return cp

    def execute(self):
        r = yield Command(
            f"{self}",
            local=True,
            callback=self._get_cp_callback,
            caller=self
        )