import asyncio
from reemote.execute import execute
from reemote.utilities.produce_json import produce_json
from reemote.utilities.produce_table import produce_table

from typing import List, Tuple, Dict, Any

def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
     return [
        (
            {
                'host': '10.156.135.16',  # alpine
                'username': 'user',  # User name
                'password': 'user'  # Password
            },
            {
                'su_user': 'root',
                'su_password': 'root'  # Password
            }
        )
        ,
        (
            {
                'host': '10.156.135.19',  # alpine
                'username': 'user',  # User name
                'password': 'user'  # Password
            },
            {
                'su_user': 'root',
                'su_password': 'root'  # Password
            }
        )
    ]


class Check_facts:
    def execute(self):
        # from reemote.facts.server import Get_OS, Get_Arch, Get_Date
        from reemote.facts.server.get_os import Get_OS
        print((yield Get_OS()).cp.stdout)
        from reemote.facts.server.get_arch import Get_Arch
        print((yield Get_Arch()).cp.stdout)
        from reemote.facts.server.get_date import Get_Date
        print((yield Get_Date()).cp.stdout)
        from reemote.facts.server.get_user import Get_User
        print((yield Get_User()).cp.stdout)
        from reemote.facts.server.get_home import Get_Home
        print((yield Get_Home()).cp.stdout)
        from reemote.facts.server.get_path import Get_Path
        print((yield Get_Path()).cp.stdout)
        from reemote.facts.server.get_tempdir import Get_TmpDir
        print((yield Get_TmpDir()).cp.stdout)
        from reemote.facts.server.get_hostname import Get_Hostname
        print((yield Get_Hostname()).cp.stdout)
        from reemote.facts.server.get_kernel import Get_Kernel
        print((yield Get_Kernel()).cp.stdout)
        from reemote.facts.server.get_kernelversion import Get_KernelVersion
        print((yield Get_KernelVersion()).cp.stdout)


async def main():
    responses = await execute(inventory(), Check_facts())
    print(produce_table(produce_json(responses)))


if __name__ == "__main__":
    asyncio.run(main())
