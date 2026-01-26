import pytest

from reemote.execute import endpoint_execute


# block insert examples/apt/get/Packages_test.generated
@pytest.mark.asyncio
async def test_apt_get_packages_example(setup_inventory, setup_directory):
    from reemote.execute import execute
    from reemote import apt1

    from reemote import apt1
    from reemote.execute import execute

    responses = await endpoint_execute(lambda: apt1.get.Packages())

    package_present = all(
        any(item.name == "adduser" for item in response.value.root)
        for response in responses
    )
    assert package_present == True, (
        "Expected the coroutine to return a list of packages and versions installed"
    )

    return responses
# block end


# block insert examples/apt/put/Packages_test.generated
@pytest.mark.asyncio
async def test_apt_put_packages_example(setup_inventory, setup_directory):
    from reemote import apt1
    from reemote.execute import execute

    responses = await endpoint_execute(
        lambda: apt1.put.Packages(
            packages=["tree"],
            present=False,
            sudo=True,
        ),
        
    )
    return responses
# block end


@pytest.mark.asyncio
async def test_hfhgh(setup_inventory, setup_directory):
    from reemote import apt1
    from reemote.execute import execute

    responses = await endpoint_execute(
        lambda: apt1.put.Packages(
            packages=["tree"],
            present=False,
            sudo=True,
        )
    )

    print(responses)

    return responses
