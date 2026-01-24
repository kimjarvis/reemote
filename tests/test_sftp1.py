import pytest

from reemote.execute import endpoint_execute

"""
❯ tree testdata
testdata
├── dir_a
│   ├── file_a.txt
│   └── link_b -> ../file_b.txt
├── file_b.txt
└── link_a -> dir_a
"""

@pytest.mark.asyncio
async def test_sftp_is_dir(setup_inventory, setup_directory):
    from reemote.sftp1.get.IsDir import IsDir
    r = await endpoint_execute(lambda: IsDir(path="testdata/dir_a"))
    assert all(d.value for d in r)
    r = await endpoint_execute(lambda: IsDir(path="testdata/dir_x"))
    assert all(not d.value for d in r), "dir_x does not exist"
    r = await endpoint_execute(lambda: IsDir(path="testdata/file_b.txt"))
    assert all(not d.value for d in r)
    r = await endpoint_execute(lambda: IsDir(path="testdata/link_a"))
    assert all(d.value for d in r), "link_a is a link to a directory not a directory"
    r = await endpoint_execute(lambda: IsDir(path="testdata/dir_a/link_b"))
    assert all(not d.value for d in r), "link_b is a link to a file not a directory"

# block insert examples/sftp/get/IsDir_test.generated
@pytest.mark.asyncio
async def test_sftp_is_dir(setup_inventory, setup_directory):
    from reemote.execute import execute
    from reemote import sftp1

    responses = await endpoint_execute(lambda: sftp1.get.IsDir(path=".."))
    for item in responses:
        assert item.value, (
            "Expected the coroutine to report that the current working directory exists on all hosts."
        )

    return responses
# block end



