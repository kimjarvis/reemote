Configure servers with a Python library, not a CLI
Do not submit url.

Author here. Reemote is configuration management as a Python library instead of a CLI. You write a deploy as a generator that yields operation requests, call await run(inventory, deploy), and get back (plans, report, hosts) — real objects, not scraped stdout.

It runs in two phases: prepare a plan against live facts, then apply. It is async end to end on asyncssh, and it ships a docker connector so you can try the whole loop locally with no remote host or SSH key. The deploy stays a plain synchronous generator — you never write async inside an operation.

    import asyncio
    from reemote import files, format_plan, op, run

    INVENTORY = [{"host": "demo",
                  "connection": {"connector": "docker",
                                 "identifier": "alpine:3.20",
                                 "docker_cmd": "podman"}}]   # or "docker"

    def deploy(host):
        yield op(files.install, name="Greeting",
                 content="Hello from reemote!\n",
                 dest="/tmp/reemote-hello.txt", mode="0644")

    async def main():
        plans, report, _ = await run(INVENTORY, deploy)
        print(format_plan(plans))

    asyncio.run(main())

run returns structured results: every applied operation carries changed, reason and the exact commands that were sent, so you consume data instead of parsing terminal output. dry=True stops after planning and gives a real preview against live state, not a simulation.

What it is not: no reemote command (you write the script), no inventory format (a Python list of dicts — load it from JSON, a database, or inline), and no Galaxy-style role ecosystem. It is not an OS translation layer either — Python does not make nginx installable by the same name on Alpine and Debian, so you still pick the right module (apt, dnf, apk, systemd, and so on).

Alpha (v0.1.4), EPL-2.0, inspired by pyinfra's design concepts but independently written. Docs: https://reemote.org/ Source: https://github.com/kimjarvis/reemote Happy to answer questions — especially about the two-phase model and where the API still feels rough.
