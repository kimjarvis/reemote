import asyncio
from reemote.execute import endpoint_execute
from reemote.operations.sftp import Directory
from reemote.facts.sftp import Isdir, Stat, Getmtime, Getatime


class Root:
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


async def main():
    await endpoint_execute(lambda: Root())

if __name__ == "__main__":
    asyncio.run(main())
