# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#

class Install_rust:
    def execute(self):
        from reemote.facts.server.get_os import Get_OS
        os = (yield Get_OS("NAME")).cp.stdout
        print(os)
        if "Alpine" in os:
            from reemote.operations.apk.packages import Operation_packages
            from reemote.operations.apk.update import Update
            r = yield Update()
            from reemote.operations.server.shell import Shell
            r = yield Operation_packages(packages=["bash", "curl", "gcc", "musl-dev", "openssl-dev"], present=True, su=True)
        if "Ubuntu" in os:
            from reemote.operations.apk.packages import Operation_packages
            from reemote.operations.apk.update import Update
            r = yield Update()
            yield Operation_packages(packages=["build-essential", "curl"], present=True, su=True)
        r = yield Shell("curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y")
