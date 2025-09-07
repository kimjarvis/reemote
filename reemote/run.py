from reemote.pre_order_generator import pre_order_generator
from reemote.run_command_on_host import run_command_on_host

async def run(inventory, obj):
    operations = []
    responses = []

    roots = []
    inventory_items = []
    for inventory_item in inventory:
        roots.append(obj)
        inventory_items.append(inventory_item)  # Store the inventory item
    # Create generators for step-wise traversal of each tree
    generators = [pre_order_generator(root) for root in roots]
    # Result of the previous operation to send
    results = {gen: None for gen in generators}  # Initialize results as None
    # Perform step-wise traversal
    done = False
    while not done:
        all_done = True

        for gen, inventory_item in zip(generators, inventory_items):
            try:
                # print(f"Sending result to generator: {results[gen]}")
                operation = gen.send(results[gen])
                operation.host_info, operation.sudo_info = inventory_item
                results[gen] = await run_command_on_host(operation)

                # print(f"Operation: {operation}")
                operations.append(operation)
                # print(f"Result: {results[gen]}")
                responses.append(results[gen])

                all_done = False

            except StopIteration:
                pass
        # If all generators are done, exit the loop
        done = all_done
    return operations, responses
