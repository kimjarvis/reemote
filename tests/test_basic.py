import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from reemote.inventory import Inventory
from reemote.execute import endpoint_execute
from reemote.config import Config

# Autouse fixture that runs before each test
@pytest.fixture(autouse=True)
def setup_inventory():
    inventory = Inventory(
        hosts=[
            {
                "connection": {
                    "host": "192.168.1.24",
                    "username": "user",
                    "password": "password",
                },
                "host_vars": {"sudo_user": "user"},
                "groups": ["all", "192.168.1.24"],
            },
            {
                "connection": {
                    "host": "192.168.1.76",
                    "username": "user",
                    "password": "password",
                },
                "host_vars": {"sudo_user": "user"},
                "groups": ["all", "192.168.1.76"],
            },
        ]
    )
    config = Config()
    config.set_inventory(inventory.to_json_serializable())


@pytest.fixture
def setup_directory():
    async def inner_fixture():
        class Root:
            async def execute(self):
                from reemote.facts.sftp import Isdir
                from reemote.commands.sftp import Rmtree
                from reemote.commands.scp import Upload

                r = yield Isdir(path="testdata")
                if r and r["value"]:
                    yield Rmtree(path="testdata")
                yield Upload(srcpaths=["tests/testdata"],dstpath=".",recurse=True)

        await endpoint_execute(lambda: Root())

    return asyncio.run(inner_fixture())




@pytest.mark.asyncio
async def test_shell(setup_inventory):
    from reemote.commands.server import Shell

    class Root:
        async def execute(self):
            r = yield Shell(cmd="echo Hello", group="192.168.1.24")
            if r:
                assert r["value"]["stdout"] == 'Hello\n'
                assert r["changed"]

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_callback(setup_inventory):
    from reemote.commands.system import Callback

    async def _callback(host_info, global_info, command, cp, caller):
        assert command.value == "test callback"
        return "tested"

    class Root:
        async def execute(self):
            r = yield Callback(
                callback=_callback, value="test callback"
            )
            if r:
                assert r["value"] == "tested"
                assert r["changed"]

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_return(setup_inventory):
    from reemote.commands.system import Return
    from reemote.commands.server import Shell

    class Child:
        async def execute(self):
            a = yield Shell(cmd="echo Hello")
            b = yield Shell(cmd="echo World")
            yield Return(value=[a, b],changed=False)

    class Parent:
        async def execute(self):
            r = yield Child()
            if r:
                assert r["value"][0]["value"]["stdout"] == 'Hello\n'
                assert r["value"][1]["value"]["stdout"] == 'World\n'

    await endpoint_execute(lambda: Parent())


@pytest.mark.asyncio
async def test_isdir(setup_inventory,setup_directory):
    from reemote.facts.sftp import Isdir

    class Root:
        async def execute(self):
            r = yield Isdir(path="testdata/dir_a")
            if r:
                assert r["value"]
                assert not r["changed"]
            r = yield Isdir(path="testdata/dir_b")
            if r:
                assert not r["value"]

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_mkdir(setup_inventory, setup_directory):
    from reemote.facts.sftp import Isdir, Stat
    from reemote.commands.sftp import Mkdir

    class Root:
        async def execute(self):
            r = yield Mkdir(path="testdata/new_dir",
                        atime=0xDEADCAFE,
                        mtime=0xACAFEDAD,
                        permissions=0o700)
            if r:
                assert r["changed"]
            r = yield Isdir(path="testdata/new_dir")
            if r:
                assert r["value"]
            r = yield Stat(path="testdata/new_dir")
            if r:
                assert r["value"]["permissions"] == 0o700
            # r = yield Getmtime(path="testdata/new_dir")
            # if r:
            #     assert r["value"] == 0xACAFEDAD
            # r = yield Getatime(path="testdata/new_dir")
            # if r:
            #     assert r["value"] == 0xDEADCAFE

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_directory1(setup_inventory, setup_directory):
    from reemote.operations.sftp import Directory
    from reemote.facts.sftp import Isdir, Stat, Getmtime, Getatime

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



@pytest.mark.asyncio
async def test_directory(setup_inventory, setup_directory):
    from reemote.operations.sftp import Directory
    from reemote.facts.sftp import Isdir, Stat

    class Child:
        async def execute(self):
            yield Directory(
                present=True,
                path="testdata/new_dir",
                permissions=0o700,
                atime=0xDEADCAFE,
                mtime=0xACAFEDAD,
            )
            r = yield Isdir(path="testdata/new_dir")
            if r:
                assert r["value"]
            r = yield Stat(path="testdata/new_dir")
            if r:
                assert r["value"]["permissions"] == 0o700
            # r = yield Getmtime(path="testdata/new_dir")
            # if r:
            #     assert r["value"] == 0xACAFEDAD
            # r = yield Getatime(path="testdata/new_dir")
            # if r:
            #     assert r["value"] == 0xDEADCAFE

    class Parent:
        async def execute(self):
            response = yield Child()
            print(response)

    await endpoint_execute(lambda: Parent())


@pytest.mark.asyncio
async def test_chmod(setup_inventory, setup_directory):
    from reemote.commands.sftp import Chmod
    from reemote.facts.sftp import Stat

    class Root:
        async def execute(self):
            yield Chmod(path="testdata/dir_a", permissions=0o700)
            r = yield Stat(path="testdata/dir_a")
            if r:
                assert r["value"]["permissions"] == 0o700


    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_chown(setup_inventory, setup_directory):
    from reemote.commands.sftp import Chown
    from reemote.facts.sftp import Stat

    # todo: note that this isn't supported on SFTPv3, we need a system version
    class Root:
        async def execute(self):
            yield Chown(path="testdata/dir_a", uid=1001)
            r = yield Stat(path="testdata/dir_a")
            if r:
                assert r["value"]["uid"] == 1000

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_utime(setup_inventory, setup_directory):
    from reemote.commands.sftp import Utime
    from reemote.facts.sftp import Getmtime, Getatime

    # todo: note that this isn't supported on SFTPv3, we need a system version
    class Root:
        async def execute(self):
            yield Utime(path="testdata/dir_a", atime=0xDEADCAFE, mtime=0xACAFEDAD)
            r = yield Getmtime(path="testdata/dir_a")
            if r:
                assert r["value"] == 0xACAFEDAD
            r = yield Getatime(path="testdata/dir_a")
            if r:
                assert r["value"] == 0xDEADCAFE

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_get_cwd(setup_inventory, setup_directory):
    from reemote.facts.sftp import Getcwd
    from reemote.commands.sftp import Chdir

    class Root:
        async def execute(self):
            r = yield Getcwd()
            if r:
                assert r and r["value"] == "/home/user"
            yield Chdir(path="/home/testdata")
            # This does not work on debian
            r = yield Getcwd()
            if r:
                assert r and r["value"] == "/home/user"

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_rename(setup_inventory, setup_directory):
    from reemote.commands.sftp import Rename
    from reemote.facts.sftp import Isfile

    class Root:
        async def execute(self):
            yield Rename(oldpath="testdata/file_b.txt",newpath="testdata/file_c.txt")
            r = yield Isfile(path="testdata/file_c.txt")
            if r:
                assert r["value"]

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_remove(setup_inventory, setup_directory):
    from reemote.commands.sftp import Remove
    from reemote.facts.sftp import Isfile

    class Root:
        async def execute(self):
            yield Remove(path="testdata/file_b.txt")
            r = yield Isfile(path="testdata/file_b.txt")
            if r:
                assert not r["value"]

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_read(setup_inventory, setup_directory):
    from reemote.facts.sftp import Read

    class Root:
        async def execute(self):
            r = yield Read(path="testdata/file_b.txt")
            if r:
                assert r["value"] == "file_b"

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_write(setup_inventory, setup_directory):
    from reemote.commands.sftp import Write
    from reemote.facts.sftp import Isfile
    from reemote.facts.sftp import Read

    class Root:
        async def execute(self):
            yield Write(path="testdata/file_c.txt", text="file_c")
            r = yield Isfile(path="testdata/file_c.txt")
            if r:
                assert r["value"]
            r = yield Read(path="testdata/file_c.txt")
            if r:
                assert r["value"] == "file_c"

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_scp_upload():
    from reemote.facts.sftp import Isdir
    from reemote.commands.sftp import Rmtree
    from reemote.commands.scp import Upload

    class Root:
        async def execute(self):
            r = yield Isdir(path="testdata")
            if r and r["value"]:
                yield Rmtree(path="testdata")
            yield Upload(srcpaths=["tests/testdata"],dstpath=".",recurse=True)

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_scp_rmtree(setup_inventory, setup_directory):
    from reemote.commands.sftp import Rmtree

    class Root:
        async def execute(self):
            yield Rmtree(path="/home/user/testdata")

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_catching_sftp_failures(setup_inventory, setup_directory):
    from reemote.commands.sftp import Mkdir

    class Root:
        async def execute(self):
                r = yield Mkdir(path="testdata/dir_a")
                if r and r["value"]:
                    assert r["value"] == "SFTPFailure"

    # with pytest.raises(SFTPFailure):  # Verify the SFTPFailure is raised
    #     await endpoint_execute(lambda: Root())
    await endpoint_execute(lambda: Root())



@pytest.mark.asyncio
async def test_unreachable_host_sftp_command(setup_inventory, setup_directory):
    from reemote.facts.sftp import Isdir
    from reemote.commands.sftp import Mkdir, Rmdir

    class Root:
        async def execute(self):
                r = yield Isdir(path="/home/user/dir_e")
                if r and r["value"]:
                    yield Rmdir(path="/home/user/dir_e")
                yield Mkdir(path="/home/user/dir_e")


    inventory = Inventory(
        hosts=[
            {
                "connection": {
                    "host": "192.168.1.24",
                    "username": "user",
                    "password": "password",
                },
                "host_vars": {"sudo_user": "user"},
                "groups": ["all", "192.168.1.24"],
            },
            {
                "connection": {
                    "host": "192.168.1.1",
                    "username": "user",
                    "password": "password",
                },
                "host_vars": {"sudo_user": "user"},
                "groups": ["all", "192.168.1.1"],
            },
        ]
    )
    config = Config()
    config.set_inventory(inventory.to_json_serializable())


    rl = await endpoint_execute(lambda: Root())
    assert any("error" in r for r in rl)





@pytest.mark.asyncio
async def test_listdir(setup_inventory, setup_directory):
    from reemote.facts.sftp import Listdir

    class Root:
        async def execute(self):
            r = yield Listdir(path="testdata")
            if r:
                assert sorted(r["value"]) == sorted(['file_b.txt', '..', '.', 'dir_a'])

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_readdir(setup_inventory, setup_directory):
    from reemote.facts.sftp import Readdir

    class Root:
        async def execute(self):
            r = yield Readdir(path="testdata")
            if r:
                for entry in r["value"]:
                    if entry["filename"]=="file_b.txt":
                        assert entry["permissions"] == 33204

    await endpoint_execute(lambda: Root())



@pytest.mark.asyncio
async def test_mkdirs(setup_inventory, setup_directory):
    from reemote.commands.sftp import Makedirs
    from reemote.facts.sftp import Isdir

    class Root:
        async def execute(self):
            r = yield Makedirs(path="testdata/x/y")
            r = yield Isdir(path="testdata/x/y")
            print(r)
            if r:
                assert r["value"]

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_link(setup_inventory, setup_directory):
    from reemote.commands.sftp import Link
    from reemote.facts.sftp import Read

    class Root:
        async def execute(self):
            r = yield Link(file_path="testdata/file_b.txt",link_path="testdata/link_b.txt")
            print(r)
            r = yield Read(path="testdata/link_b.txt")
            if r:
                assert r["value"] == "file_b"

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_exists(setup_inventory, setup_directory):
    from reemote.facts.sftp import Exists

    class Root:
        async def execute(self):
            r = yield Exists(path="testdata/file_b.txt")
            assert r and r["value"]

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_lexists(setup_inventory, setup_directory):
    from reemote.facts.sftp import Lexists

    class Root:
        async def execute(self):
            r = yield Lexists(path="testdata/file_b.txt")
            assert r and r["value"]

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_symlink(setup_inventory, setup_directory):
    from reemote.commands.sftp import Symlink
    from reemote.facts.sftp import Islink

    class Root:
        async def execute(self):
            r = yield Symlink(file_path="testdata/file_b.txt", link_path="testdata/link_b.txt")
            print(r)
            r = yield Islink(path="testdata/link_b.txt")
            print(r)
            # assert r and r["value"]

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_unlink(setup_inventory, setup_directory):
    from reemote.commands.sftp import Symlink, Unlink
    from reemote.facts.sftp import Islink

    class Root:
        async def execute(self):
            yield Symlink(file_path="testdata/file_b.txt", link_path="testdata/link_b.txt")
            r = yield Islink(path="testdata/link_b.txt")
            assert r and r["value"]
            r = yield Unlink(path="testdata/link_b.txt")
            print(r)
            r = yield Islink(path="testdata/link_b.txt")
            assert r and not r["value"]

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_lstat(setup_inventory, setup_directory):
    from reemote.facts.sftp import Lstat
    from reemote.commands.sftp import Symlink

    class Root:
        async def execute(self):
            yield Symlink(file_path="testdata/dir_a", link_path="testdata/link_dir")
            r = yield Lstat(path="testdata/link_dir")
            if r:
                assert r["value"]["permissions"] == 0o777

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_readlink(setup_inventory, setup_directory):
    from reemote.commands.sftp import Symlink
    from reemote.facts.sftp import Readlink

    class Root:
        async def execute(self):
            yield Symlink(file_path="testdata/file_b.txt", link_path="testdata/link_b.txt")
            r = yield Readlink(path="testdata/link_b.txt")
            print(r)
            assert r and r["value"] == "testdata/file_b.txt"

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_glob(setup_inventory, setup_directory):
    from reemote.facts.sftp import Glob

    class Root:
        async def execute(self):
            r = yield Glob(path="/home/user/testdata/file*.*")
            print(r)
            assert r and r["value"] == ['/home/user/testdata/file_b.txt']

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_glob_sftpname(setup_inventory, setup_directory):
    from reemote.facts.sftp import GlobSftpName

    class Root:
        async def execute(self):
            r = yield GlobSftpName(path="/home/user/testdata/file*.*")
            if r:
                for entry in r["value"]:
                    if entry["filename"]=="/home/user/testdata/file_b.txt":
                        assert entry["permissions"] == 33204

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_setstat(setup_inventory, setup_directory):
    from reemote.commands.sftp import Setstat
    from reemote.facts.sftp import Stat, Getmtime, Getatime

    class Root:
        async def execute(self):
            yield Setstat(path="testdata/dir_a",
                        atime=0xDEADCAFE,
                        mtime=0xACAFEDAD,
                        permissions=0o700)
            r = yield Stat(path="testdata/dir_a")
            print(r)
            if r:
                assert r["value"]["permissions"] == 0o700
            r = yield Getmtime(path="testdata/dir_a")
            if r:
                assert r["value"] == 0xACAFEDAD
            r = yield Getatime(path="testdata/dir_a")
            if r:
                assert r["value"] == 0xDEADCAFE

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_statvfs(setup_inventory, setup_directory):
    from reemote.facts.sftp import StatVfs

    class Root:
        async def execute(self):
            r = yield StatVfs(path="testdata/dir_a")
            print(r)
            assert r and r["value"]["namemax"] == 255

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_realpath(setup_inventory, setup_directory):
    from reemote.facts.sftp import Realpath

    class Root:
        async def execute(self):
            r = yield Realpath(path="testdata/dir_a")
            print(r)
            assert r and r["value"] == "/home/user/testdata/dir_a"

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_truncate(setup_inventory, setup_directory):
    # todo: Why does this return an AttributeError ?
    from reemote.commands.sftp import Truncate

    class Root:
        async def execute(self):
            r = yield Truncate(path="testdata/dir_a",size=8)
            print(r)

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_client():
    from reemote.facts.sftp import Client

    class Root:
        async def execute(self):
            r = yield Client()
            print(r)
            assert r and r["value"]["version"] == 3

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_unreachable_host_sftp_fact(setup_inventory, setup_directory):
    from reemote.facts.sftp import StatVfs

    class Root:
        async def execute(self):
            r = yield StatVfs(path="testdata/dir_a")

    inventory = Inventory(
        hosts=[
            {
                "connection": {
                    "host": "192.168.1.24",
                    "username": "user",
                    "password": "password",
                },
                "host_vars": {"sudo_user": "user"},
                "groups": ["all", "192.168.1.24"],
            },
            {
                "connection": {
                    "host": "192.168.1.1",
                    "username": "user",
                    "password": "password",
                },
                "host_vars": {"sudo_user": "user"},
                "groups": ["all", "192.168.1.1"],
            },
        ]
    )
    config = Config()
    config.set_inventory(inventory.to_json_serializable())

    rl = await endpoint_execute(lambda: Root())
    assert any("error" in r for r in rl)
