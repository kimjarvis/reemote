class Hello_world:
    def execute(self):
        from reemote.operations.server.shell import Shell
        r = yield Shell("echo Hello World!")
        print(r.cp.stdout)