import pytest

from reemote.execute import endpoint_execute


# block insert examples/apt/get/Packages_test.generated
@pytest.mark.asyncio
async def test_apt_get_packages_example(setup_inventory, setup_directory):
    from reemote.execute import execute
    from reemote import apt1

    responses = await endpoint_execute(lambda: apt1.get.Packages())

    adduser_present = all(any(item.name == "adduser" for item in response.value.root) for response in responses)
    assert adduser_present == True, "Expected the coroutine to return a list of packages containing the package adduser on each host"

    return responses
# block end

# block insert examples/apt/put/Packages_test.generated
@pytest.mark.asyncio
async def test_apt_put_packages_example(setup_inventory, setup_directory):
    from reemote.execute import execute
    from reemote import apt1

    responses = await endpoint_execute(lambda: apt1.put.Packages())

    print(responses)

    return responses
# block end


