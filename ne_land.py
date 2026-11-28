"""Scratch: build the scoped node_exporter role commit on main's tip (no worktree change)."""

import subprocess
import sys

MAIN_FILES = {
    "mkdocs.yml": [
        (
            "          - elasticsearch: roles/elasticsearch.md\n",
            "          - elasticsearch: roles/elasticsearch.md\n"
            "          - node_exporter: roles/node_exporter.md\n",
        ),
    ],
    "src/reemote/roles/__init__.py": [
        (
            "from .nginx import NginxVars, nginx\n",
            "from .nginx import NginxVars, nginx\n"
            "from .node_exporter import NodeExporterVars, node_exporter\n",
        ),
        (
            '    "NginxVars",\n',
            '    "NginxVars",\n    "NodeExporterVars",\n',
        ),
        (
            '    "nginx",\n',
            '    "nginx",\n    "node_exporter",\n',
        ),
    ],
    "docs/roles/index.md": [
        (
            "reemote ships thirty-two configuration roles as **library code**.",
            "reemote ships thirty-three configuration roles as **library code**.",
        ),
        (
            "The thirty-two roles group by what they configure.",
            "The thirty-three roles group by what they configure.",
        ),
        (
            "| [elasticsearch](elasticsearch.md) | Elasticsearch — the dependencies,"
            " the Elastic signing key and apt repository, the `elasticsearch` package,"
            " the rendered config and version-appropriate heap file, the service and"
            " port wait, one restart handler |\n",
            "| [elasticsearch](elasticsearch.md) | Elasticsearch — the dependencies,"
            " the Elastic signing key and apt repository, the `elasticsearch` package,"
            " the rendered config and version-appropriate heap file, the service and"
            " port wait, one restart handler |\n"
            "| [node_exporter](node_exporter.md) | node_exporter — the release tarball"
            " unpacked and the binary moved into place, the service group and account,"
            " the rendered systemd unit, the running/enabled unit, the restart handler"
            " and the metrics-endpoint check |\n",
        ),
        (
            "| [elasticsearch](elasticsearch.md) |"
            " [`geerlingguy.elasticsearch`](https://github.com/geerlingguy/ansible-role-elasticsearch)"
            " | Ubuntu | 2 changed / 2 converged (the wait always reports) | none |"
            " **yes** | 1 |\n",
            "| [elasticsearch](elasticsearch.md) |"
            " [`geerlingguy.elasticsearch`](https://github.com/geerlingguy/ansible-role-elasticsearch)"
            " | Ubuntu | 2 changed / 2 converged (the wait always reports) | none |"
            " **yes** | 1 |\n"
            "| [node_exporter](node_exporter.md) |"
            " [`geerlingguy.node_exporter`](https://github.com/geerlingguy/ansible-role-node_exporter)"
            " | Ubuntu | 3 fresh (4 resolving `latest`) / 3 converged | none | no | 1 |\n",
        ),
        (
            "  honest always-changed line (D6).\n",
            "  honest always-changed line (D6).\n"
            "- **[node_exporter](node_exporter.md)** — the release tarball unpacked in\n"
            "  `/tmp` and the binary moved to `/usr/local/bin`, the service group and\n"
            "  account with their optional fixed ids, the rendered `node_exporter.service`\n"
            "  unit, the running/enabled unit, its restart handler and the metrics-endpoint\n"
            "  check. The all-four-barriers role: an `act` probe decides whether the\n"
            "  download pair enters the stream at all, a `control` fetch resolves `latest`,\n"
            "  the `checkpoint` reads the notification, and a `fact` verifies the running\n"
            "  exporter — so the wave count moves with the host and the converged pass\n"
            "  carries no download request rather than an unchanged one.\n",
        ),
    ],
}

NEW_FILES = [
    "src/reemote/roles/node_exporter.py",
    "examples/roles/node_exporter.py",
    "tests/roles/test_node_exporter.py",
    "docs/roles/node_exporter.md",
]

MESSAGE = """roles: migrate geerlingguy.node_exporter to a library role (Ubuntu)

The role becomes `reemote.roles.node_exporter`: one factory over the
wave/barrier model whose body carries all four barrier types — the
`--version` probe as an `act(Shell)` gate that decides whether the
download half runs at all, a `control` fetch of the GitHub releases API
for `version: latest`, the `checkpoint("configure done")` notification
read that drives the restart handler, and the `server.HttpEndpoint`
fact that reproduces the role's `failed_when` metrics check.

* `src/reemote/roles/node_exporter.py` — `NodeExporterVars` (the twelve
  `node_exporter_*` defaults), `install()`/`configure()` sub-deploys and
  the twelve documented deviations D1–D12.
* `src/reemote/roles/__init__.py` — the two exports.
* `examples/roles/node_exporter.py` — the standalone quickstart runner.
* `tests/roles/test_node_exporter.py` — 37 offline tests: both pinned
  wave trees, the notification re-fire, the stopped-state convergence,
  the metrics gate and the vars validation.
* `docs/roles/node_exporter.md` + the nav row and the catalogue entries,
  with transcripts captured from a real harness run.

Zero new library gaps: every task maps to a shipped operation, barrier
or documented recipe.

Gates: tests/roles 969 passed, full suite 6698 passed, ruff clean,
`mkdocs build --strict` exit 0 with no warnings."""


def git(*args: str, stdin: bytes | None = None) -> str:
    proc = subprocess.run(
        ("git",) + args, check=True, capture_output=True, input=stdin
    )
    return proc.stdout.decode()


def main() -> None:
    main_sha = git("rev-parse", "main").strip()
    print(f"main = {main_sha}")

    blobs: dict[str, str] = {}
    for path, edits in MAIN_FILES.items():
        text = git("show", f"{main_sha}:{path}")
        for old, new in edits:
            count = text.count(old)
            if count != 1:
                sys.exit(f"ANCHOR FAIL {path}: {count} matches for {old[:60]!r}")
            text = text.replace(old, new, 1)
        blobs[path] = text

    index = "/tmp/ne_gate_index"
    # read-tree / write-tree / commit-tree against the temporary index
    run_env = {"GIT_INDEX_FILE": index}

    def gi(*args: str) -> str:
        import os

        env = dict(os.environ, **run_env)
        proc = subprocess.run(
            ("git",) + args, check=True, capture_output=True, env=env
        )
        return proc.stdout.decode()

    gi("read-tree", main_sha)
    for path, text in blobs.items():
        blob = git("hash-object", "-w", "--stdin", stdin=text.encode()).strip()
        gi("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}")
    for path in NEW_FILES:
        blob = git("hash-object", "-w", path).strip()
        gi("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}")
    tree = gi("write-tree").strip()
    commit = gi("commit-tree", tree, "-p", main_sha, "-m", MESSAGE).strip()
    print(f"tree   = {tree}")
    print(f"commit = {commit}")
    print(git("diff", "--stat", main_sha, commit))


main()
