    from reemote import sftp1

    responses = await execute(lambda: sftp1.get.IsDir(path=".."), inventory)
    for item in responses:
        assert item.value, (
            "The coroutine should report that the current working directory exists on the host."
        )


