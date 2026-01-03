import pytest


@pytest.mark.asyncio
async def test_directory1(setup_inventory, setup_directory):
    from reemote.sftp import Directory
    from reemote.sftp import Isdir, Stat, Getmtime, Getatime

    class Child:
        async def execute(self):
            yield Directory(
                present=True,
                path="testdata/new_dir",
                permissions=0o700,
                atime=10,
                mtime=20,
            )
            r = yield Isdir(path="testdata/new_dir")
            if r:
                assert r["value"]
            r = yield Stat(path="testdata/new_dir")
            if r:
                assert r["value"]["permissions"] == 0o700
            r = yield Getmtime(path="testdata/new_dir")
            if r:
                assert r["value"] == 20
            r = yield Getatime(path="testdata/new_dir")
            if r:
                assert r["value"] == 10
