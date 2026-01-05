import pytest

from reemote.execute import endpoint_execute

@pytest.mark.asyncio
async def test_shell_sudo(setup_inventory):
    from reemote.shell import Shell

    class Root:
        async def execute(self):
            r = yield Shell(cmd="ls /root",sudo=True)
            if r:
                assert not r["error"]
            print(r)

    await endpoint_execute(lambda: Root())
