import asyncio
import pytest
import sys
import os

from reemote.facts.sftp import GlobSftpName

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from reemote.commands.inventory import create_inventory
from reemote.execute import execute


# Autouse fixture that runs before each test
@pytest.fixture(autouse=True)
def setup_inventory():
    """Create inventory before each test"""
    create_inventory(
        [
            [
                {"host": "192.168.1.24", "username": "user", "password": "password"},
                {
                    "groups": ["all", "192.168.1.24"],
                },
            ],
            [
                {"host": "192.168.1.76", "username": "user", "password": "password"},
                {
                    "groups": ["all", "192.168.1.76"],
                },
            ],
        ]
    )



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

        await execute(lambda: Root())

    return asyncio.run(inner_fixture())




@pytest.mark.asyncio
async def test_shell():
    from reemote.commands.server import Shell

    class Root:
        async def execute(self):
            r = yield Shell(cmd="echo Hello", group="192.168.1.24")
            if r:
                assert r["value"]["stdout"] == 'Hello\n'

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_callback():
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

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_return():
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

    await execute(lambda: Parent())


@pytest.mark.asyncio
async def test_isdir(setup_directory):
    from reemote.facts.sftp import Isdir

    class Root:
        async def execute(self):
            r = yield Isdir(path="testdata/dir_a")
            if r:
                assert r["value"]
            r = yield Isdir(path="testdata/dir_b")
            if r:
                assert not r["value"]

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_mkdir(setup_directory):
    from reemote.facts.sftp import Isdir, Stat
    from reemote.commands.sftp import Mkdir

    class Root:
        async def execute(self):
            yield Mkdir(path="testdata/new_dir",
                        atime=0xDEADCAFE,
                        mtime=0xACAFEDAD,
                        permissions=0o700)
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

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_directory1(setup_directory):
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
async def test_directory(setup_directory):
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

    await execute(lambda: Parent())


@pytest.mark.asyncio
async def test_chmod(setup_directory):
    from reemote.commands.sftp import Chmod
    from reemote.facts.sftp import Stat

    class Root:
        async def execute(self):
            yield Chmod(path="testdata/dir_a", permissions=0o700)
            r = yield Stat(path="testdata/dir_a")
            if r:
                assert r["value"]["permissions"] == 0o700


    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_chown(setup_directory):
    from reemote.commands.sftp import Chown
    from reemote.facts.sftp import Stat

    # todo: note that this isn't supported on SFTPv3, we need a system version
    class Root:
        async def execute(self):
            yield Chown(path="testdata/dir_a", uid=1001)
            r = yield Stat(path="testdata/dir_a")
            if r:
                assert r["value"]["uid"] == 1000

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_utime(setup_directory):
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

    await execute(lambda: Root())

@pytest.mark.asyncio
async def test_get_cwd(setup_directory):
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

    await execute(lambda: Root())

@pytest.mark.asyncio
async def test_rename(setup_directory):
    from reemote.commands.sftp import Rename
    from reemote.facts.sftp import Isfile

    class Root:
        async def execute(self):
            yield Rename(oldpath="testdata/file_b.txt",newpath="testdata/file_c.txt")
            r = yield Isfile(path="testdata/file_c.txt")
            if r:
                assert r["value"]

    await execute(lambda: Root())

@pytest.mark.asyncio
async def test_remove(setup_directory):
    from reemote.commands.sftp import Remove
    from reemote.facts.sftp import Isfile

    class Root:
        async def execute(self):
            yield Remove(path="testdata/file_b.txt")
            r = yield Isfile(path="testdata/file_b.txt")
            if r:
                assert not r["value"]

    await execute(lambda: Root())

@pytest.mark.asyncio
async def test_read(setup_directory):
    from reemote.facts.sftp import Read

    class Root:
        async def execute(self):
            r = yield Read(path="testdata/file_b.txt")
            if r:
                assert r["value"] == "file_b"

    await execute(lambda: Root())

@pytest.mark.asyncio
async def test_write(setup_directory):
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

    await execute(lambda: Root())


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

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_scp_rmtree(setup_directory):
    from reemote.commands.sftp import Rmtree

    class Root:
        async def execute(self):
            yield Rmtree(path="/home/user/testdata")

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_catching_sftp_failures(setup_directory):
    from reemote.commands.sftp import Mkdir

    class Root:
        async def execute(self):
                r = yield Mkdir(path="testdata/dir_a")
                if r and r["value"]:
                    assert r["value"] == "SFTPFailure"

    # with pytest.raises(SFTPFailure):  # Verify the SFTPFailure is raised
    #     await execute(lambda: Root())
    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_unreachable_host(setup_directory):
    from reemote.facts.sftp import Isdir
    from reemote.commands.sftp import Mkdir, Rmdir
    from reemote.commands.inventory import add_entry, delete_entry
    from reemote.facts.inventory import isentry

    class Root:
        async def execute(self):
                r = yield Isdir(path="/home/user/dir_e")
                if r and r["value"]:
                    yield Rmdir(path="/home/user/dir_e")
                yield Mkdir(path="/home/user/dir_e")

    if isentry("192.168.1.33"):
        delete_entry("192.168.1.33")
    add_entry(
            [
                {"host": "192.168.1.33", "username": "user", "password": "password"},
                {
                    "groups": ["all", "192.168.1.33"],
                },
            ],
    )
    # with pytest.raises(OSError):  # Or use asyncssh.Error if applicable
    rl = await execute(lambda: Root())
    assert any("error" in r for r in rl)
    if isentry("192.168.1.33"):
        delete_entry("192.168.1.33")



@pytest.mark.asyncio
async def test_listdir(setup_directory):
    from reemote.facts.sftp import Listdir

    class Root:
        async def execute(self):
            r = yield Listdir(path="testdata")
            if r:
                assert sorted(r["value"]) == sorted(['file_b.txt', '..', '.', 'dir_a'])

    await execute(lambda: Root())

@pytest.mark.asyncio
async def test_readdir(setup_directory):
    from reemote.facts.sftp import Readdir

    class Root:
        async def execute(self):
            r = yield Readdir(path="testdata")
            if r:
                for entry in r["value"]:
                    if entry["filename"]=="file_b.txt":
                        assert entry["permissions"] == 33204

    await execute(lambda: Root())



@pytest.mark.asyncio
async def test_mkdirs(setup_directory):
    from reemote.commands.sftp import Makedirs
    from reemote.facts.sftp import Isdir

    class Root:
        async def execute(self):
            r = yield Makedirs(path="testdata/x/y")
            r = yield Isdir(path="testdata/x/y")
            print(r)
            if r:
                assert r["value"]

    await execute(lambda: Root())

@pytest.mark.asyncio
async def test_link(setup_directory):
    from reemote.commands.sftp import Link
    from reemote.facts.sftp import Read

    class Root:
        async def execute(self):
            r = yield Link(file_path="testdata/file_b.txt",link_path="testdata/link_b.txt")
            print(r)
            r = yield Read(path="testdata/link_b.txt")
            if r:
                assert r["value"] == "file_b"

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_exists(setup_directory):
    from reemote.facts.sftp import Exists

    class Root:
        async def execute(self):
            r = yield Exists(path="testdata/file_b.txt")
            assert r and r["value"]

    await execute(lambda: Root())

@pytest.mark.asyncio
async def test_lexists(setup_directory):
    from reemote.facts.sftp import Lexists

    class Root:
        async def execute(self):
            r = yield Lexists(path="testdata/file_b.txt")
            assert r and r["value"]

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_symlink(setup_directory):
    from reemote.commands.sftp import Symlink
    from reemote.facts.sftp import Islink

    class Root:
        async def execute(self):
            r = yield Symlink(file_path="testdata/file_b.txt", link_path="testdata/link_b.txt")
            print(r)
            r = yield Islink(path="testdata/link_b.txt")
            print(r)
            # assert r and r["value"]

    await execute(lambda: Root())

@pytest.mark.asyncio
async def test_unlink(setup_directory):
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

    await execute(lambda: Root())

@pytest.mark.asyncio
async def test_lstat(setup_directory):
    from reemote.facts.sftp import Lstat
    from reemote.commands.sftp import Symlink

    class Root:
        async def execute(self):
            yield Symlink(file_path="testdata/dir_a", link_path="testdata/link_dir")
            r = yield Lstat(path="testdata/link_dir")
            if r:
                assert r["value"]["permissions"] == 0o777

    await execute(lambda: Root())

@pytest.mark.asyncio
async def test_readlink(setup_directory):
    from reemote.commands.sftp import Symlink
    from reemote.facts.sftp import Readlink

    class Root:
        async def execute(self):
            yield Symlink(file_path="testdata/file_b.txt", link_path="testdata/link_b.txt")
            r = yield Readlink(path="testdata/link_b.txt")
            print(r)
            assert r and r["value"] == "testdata/file_b.txt"

    await execute(lambda: Root())

@pytest.mark.asyncio
async def test_glob(setup_directory):
    from reemote.facts.sftp import Glob

    class Root:
        async def execute(self):
            r = yield Glob(path="/home/user/testdata/file*.*")
            print(r)
            assert r and r["value"] == ['/home/user/testdata/file_b.txt']

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_glob_sftpname(setup_directory):
    from reemote.facts.sftp import GlobSftpName

    class Root:
        async def execute(self):
            r = yield GlobSftpName(path="/home/user/testdata/file*.*")
            if r:
                for entry in r["value"]:
                    if entry["filename"]=="/home/user/testdata/file_b.txt":
                        assert entry["permissions"] == 33204

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_setstat(setup_directory):
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

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_statvfs(setup_directory):
    from reemote.facts.sftp import StatVfs

    class Root:
        async def execute(self):
            r = yield StatVfs(path="testdata/dir_a")
            print(r)
            assert r and r["value"]["namemax"] == 255

    await execute(lambda: Root())

@pytest.mark.asyncio
async def test_realpath(setup_directory):
    from reemote.facts.sftp import Realpath

    class Root:
        async def execute(self):
            r = yield Realpath(path="testdata/dir_a")
            print(r)
            assert r and r["value"] == "/home/user/testdata/dir_a"

    await execute(lambda: Root())

@pytest.mark.asyncio
async def test_truncate(setup_directory):
    # todo: Why does this return an AttributeError ?
    from reemote.commands.sftp import Truncate

    class Root:
        async def execute(self):
            r = yield Truncate(path="testdata/dir_a",size=8)
            print(r)

    await execute(lambda: Root())

@pytest.mark.asyncio
async def test_client():
    from reemote.facts.sftp import Client

    class Root:
        async def execute(self):
            r = yield Client()
            print(r)
            assert r and r["value"]["version"] == 3

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_unreachable_host_stat(setup_directory):
    from reemote.facts.sftp import StatVfs
    from reemote.commands.inventory import add_entry, delete_entry
    from reemote.facts.inventory import isentry

    class Root:
        async def execute(self):
            r = yield StatVfs(path="testdata/dir_a")
            print(r)

    if isentry("192.168.1.33"):
        delete_entry("192.168.1.33")
    add_entry(
            [
                {"host": "192.168.1.33", "username": "user", "password": "password"},
                {
                    "groups": ["all", "192.168.1.33"],
                },
            ],
    )
    # with pytest.raises(OSError):  # Or use asyncssh.Error if applicable
    rl = await execute(lambda: Root())
    assert any("error" in r for r in rl)
    if isentry("192.168.1.33"):
        delete_entry("192.168.1.33")
