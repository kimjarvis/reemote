from asyncssh import SSHCompletedProcess
from reemote.result import Result

async def run_command_on_local(operation):
    # Define the asynchronous function to connect to a host and run a command
    host_info = operation.host_info
    global_info = operation.global_info
    command = operation.command
    cp = SSHCompletedProcess()
    executed = False
    caller = operation.caller

    cp.stdout= await operation.callback(host_info, global_info, command, cp, caller)

    return Result(cp=cp, host=host_info.get("host"), op=operation, executed=executed)
