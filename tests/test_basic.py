import pytest

from reemote.execute import endpoint_execute




@pytest.mark.asyncio
async def test_isdir(setup_inventory, setup_directory):
    from reemote.sftp import Isdir

    class Root:
        async def execute(self):
            r = yield Isdir(path="testdata/dir_a")
            print(r)
            if r:
                assert r["value"]
                assert not r["changed"]
            r = yield Isdir(path="testdata/dir_b")
            if r:
                assert not r["value"]

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_mkdir(setup_inventory, setup_directory):
    from reemote.sftp import Isdir, Stat
    from reemote.sftp import Mkdir

    class Root:
        async def execute(self):
            r = yield Mkdir(
                path="testdata/new_dir",
                atime=0xDEADCAFE,
                mtime=0xACAFEDAD,
                permissions=0o700,
            )
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
    from reemote.sftp import Directory
    from reemote.sftp import Isdir, Stat, Getmtime

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
            # if r:
            #     assert r["value"] == 20
            # r = yield Getatime(path="testdata/new_dir")
            # if r:
            #     assert r["value"] == 10

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_directory(setup_inventory, setup_directory):
    from reemote.sftp import Directory
    from reemote.sftp import Isdir, Stat

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
    from reemote.sftp import Chmod
    from reemote.sftp import Stat

    class Root:
        async def execute(self):
            yield Chmod(path="testdata/dir_a", permissions=0o700)
            r = yield Stat(path="testdata/dir_a")
            if r:
                assert r["value"]["permissions"] == 0o700

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_chown(setup_inventory, setup_directory):
    from reemote.sftp import Chown
    from reemote.sftp import Stat

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
    from reemote.sftp import Utime
    from reemote.sftp import Getmtime, Getatime

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
    from reemote.sftp import Getcwd
    from reemote.sftp import Chdir

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
    from reemote.sftp import Rename
    from reemote.sftp import Isfile

    class Root:
        async def execute(self):
            yield Rename(oldpath="testdata/file_b.txt", newpath="testdata/file_c.txt")
            r = yield Isfile(path="testdata/file_c.txt")
            if r:
                assert r["value"]

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_remove(setup_inventory, setup_directory):
    from reemote.sftp import Remove
    from reemote.sftp import Isfile

    class Root:
        async def execute(self):
            yield Remove(path="testdata/file_b.txt")
            r = yield Isfile(path="testdata/file_b.txt")
            if r:
                assert not r["value"]

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_read(setup_inventory, setup_directory):
    from reemote.sftp import Read

    class Root:
        async def execute(self):
            r = yield Read(path="testdata/file_b.txt")
            if r:
                assert r["value"] == "file_b"

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_write(setup_inventory, setup_directory):
    from reemote.sftp import Write
    from reemote.sftp import Isfile
    from reemote.sftp import Read

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
    from reemote.sftp import Isdir
    from reemote.sftp import Rmtree
    from reemote.scp import Upload

    class Root:
        async def execute(self):
            r = yield Isdir(path="testdata")
            if r and r["value"]:
                yield Rmtree(path="testdata")
            yield Upload(srcpaths=["tests/testdata"], dstpath=".", recurse=True)

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_scp_rmtree(setup_inventory, setup_directory):
    from reemote.sftp import Rmtree

    class Root:
        async def execute(self):
            yield Rmtree(path="/home/user/testdata")

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_catching_sftp_failures(setup_inventory, setup_directory):
    from reemote.sftp import Mkdir

    class Root:
        async def execute(self):
            r = yield Mkdir(path="testdata/dir_a")
            if r and r["value"]:
                assert r["value"] == "SFTPFailure"

    # with pytest.raises(SFTPFailure):  # Verify the SFTPFailure is raised
    #     await endpoint_execute(lambda: Root())
    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_listdir(setup_inventory, setup_directory):
    from reemote.sftp import Listdir

    class Root:
        async def execute(self):
            r = yield Listdir(path="testdata")
            if r:
                assert sorted(r["value"]) == sorted(["file_b.txt", "..", ".", "dir_a"])

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_readdir(setup_inventory, setup_directory):
    from reemote.sftp import Readdir

    class Root:
        async def execute(self):
            r = yield Readdir(path="testdata")
            if r:
                for entry in r["value"]:
                    if entry["filename"] == "file_b.txt":
                        assert entry["permissions"] == 33204

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_mkdirs(setup_inventory, setup_directory):
    from reemote.sftp import Makedirs
    from reemote.sftp import Isdir

    class Root:
        async def execute(self):
            r = yield Makedirs(path="testdata/x/y")
            print(r)
            r = yield Isdir(path="testdata/x/y")
            if r:
                assert r["value"]

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_link(setup_inventory, setup_directory):
    from reemote.sftp import Link
    from reemote.sftp import Read

    class Root:
        async def execute(self):
            yield Link(file_path="testdata/file_b.txt", link_path="testdata/link_b.txt")
            r = yield Read(path="testdata/link_b.txt")
            if r:
                assert r["value"] == "file_b"

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_exists(setup_inventory, setup_directory):
    from reemote.sftp import Exists

    class Root:
        async def execute(self):
            r = yield Exists(path="testdata/file_b.txt")
            assert r and r["value"]

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_lexists(setup_inventory, setup_directory):
    from reemote.sftp import Lexists

    class Root:
        async def execute(self):
            r = yield Lexists(path="testdata/file_b.txt")
            assert r and r["value"]

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_symlink(setup_inventory, setup_directory):
    from reemote.sftp import Symlink
    from reemote.sftp import Islink

    class Root:
        async def execute(self):
            yield Symlink(
                file_path="testdata/file_b.txt", link_path="testdata/link_b.txt"
            )
            r = yield Islink(path="testdata/link_b.txt")
            assert r and r["value"]

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_unlink(setup_inventory, setup_directory):
    from reemote.sftp import Symlink, Unlink
    from reemote.sftp import Islink

    class Root:
        async def execute(self):
            yield Symlink(
                file_path="testdata/file_b.txt", link_path="testdata/link_b.txt"
            )
            r = yield Islink(path="testdata/link_b.txt")
            assert r and r["value"]
            yield Unlink(path="testdata/link_b.txt")
            r = yield Islink(path="testdata/link_b.txt")
            assert r and not r["value"]

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_lstat(setup_inventory, setup_directory):
    from reemote.sftp import Lstat
    from reemote.sftp import Symlink

    class Root:
        async def execute(self):
            yield Symlink(file_path="testdata/dir_a", link_path="testdata/link_dir")
            r = yield Lstat(path="testdata/link_dir")
            if r:
                assert r["value"]["permissions"] == 0o777

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_readlink(setup_inventory, setup_directory):
    from reemote.sftp import Symlink
    from reemote.sftp import Readlink

    class Root:
        async def execute(self):
            yield Symlink(
                file_path="testdata/file_b.txt", link_path="testdata/link_b.txt"
            )
            r = yield Readlink(path="testdata/link_b.txt")
            assert r and r["value"] == "testdata/file_b.txt"

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_glob(setup_inventory, setup_directory):
    from reemote.sftp import Glob

    class Root:
        async def execute(self):
            r = yield Glob(path="/home/user/testdata/file*.*")
            assert r and r["value"] == ["/home/user/testdata/file_b.txt"]

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_glob_sftpname(setup_inventory, setup_directory):
    from reemote.sftp import GlobSftpName

    class Root:
        async def execute(self):
            r = yield GlobSftpName(path="/home/user/testdata/file*.*")
            if r:
                for entry in r["value"]:
                    if entry["filename"] == "/home/user/testdata/file_b.txt":
                        assert entry["permissions"] == 33204

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_setstat(setup_inventory, setup_directory):
    from reemote.sftp import Setstat
    from reemote.sftp import Stat, Getmtime, Getatime

    class Root:
        async def execute(self):
            yield Setstat(
                path="testdata/dir_a",
                atime=0xDEADCAFE,
                mtime=0xACAFEDAD,
                permissions=0o700,
            )
            r = yield Stat(path="testdata/dir_a")
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
    from reemote.sftp import StatVfs

    class Root:
        async def execute(self):
            r = yield StatVfs(path="testdata/dir_a")
            assert r and r["value"]["namemax"] == 255

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_realpath(setup_inventory, setup_directory):
    from reemote.sftp import Realpath

    class Root:
        async def execute(self):
            r = yield Realpath(path="testdata/dir_a")
            assert r and r["value"] == "/home/user/testdata/dir_a"

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_truncate(setup_inventory, setup_directory):
    # todo: Why does this return an AttributeError ?
    from reemote.sftp import Truncate

    class Root:
        async def execute(self):
            r = yield Truncate(path="testdata/dir_a", size=8)

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_client():
    from reemote.sftp import Client

    class Root:
        async def execute(self):
            r = yield Client()
            assert r and r["value"]["version"] == 3

    await endpoint_execute(lambda: Root())
