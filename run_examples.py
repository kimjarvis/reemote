# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run every documented example and capture the output in a log.

This is a *usage* helper, not part of the library. It discovers every
``*.py`` script under ``examples/`` (recursively, sorted by path, minus
any script under the directories listed in ``EXCLUDED_DIRS`` -- currently
the state-mutating ``examples/modules/selinux/`` namespace -- and minus
the shared helpers named in ``EXCLUDED_NAMES``) -- the full
tree documented in ``docs/examples.md``: ``examples/modules/<ns>/``,
``examples/actions/`` and ``examples/demonstrations/<topic>/`` -- runs
each one as a subprocess with the current interpreter, and writes the
combined stdout/stderr of every run -- framed by a header naming the
script and its exit status -- to ``examples.log`` in the project root,
overwriting any previous log.

Pass ``--path <dir>`` to scope the sweep to the directory tree under
``<dir>`` instead, e.g. ``--path examples/modules/lxd`` for every lxd
example. An explicit path runs *every* script beneath it, including the
directories the full sweep excludes; the log and summary are written the
same way.

Most of these examples connect to real hosts and apply changes over SSH
(or, for the Docker connector demo, a Docker daemon), exactly as when run
by hand -- including the state-mutating operation examples under
``examples/modules/``, so point their inventories at disposable machines
before a full sweep. The exceptions run without any host: ``composed_group_tag_roles``
works in memory, ``action_in_deploy_guard`` triggers its guard before any
transport, and ``action_wait_for_connection`` treats its expected timeout
as success. A failing example does not stop the sweep: its full output
and exit code are appended to the log, the reason for the failure is
echoed to the console, and the remaining scripts still run. This script
exits non-zero if any example failed.

Run it with::

    uv run python run_examples.py

or, for one subtree only::

    uv run python run_examples.py --path examples/modules/lxd
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
EXAMPLES_DIRS = (PROJECT_ROOT / "examples",)
# Example scripts under these directories (relative to the project root)
# are skipped by the sweep.
EXCLUDED_DIRS = (PROJECT_ROOT / "examples" / "modules" / "selinux",)
LOG_PATH = PROJECT_ROOT / "examples.log"

SEPARATOR = "=" * 78


def is_excluded(script: Path) -> bool:
    """Whether the script is under an excluded directory."""
    return any(directory in script.parents for directory in EXCLUDED_DIRS)


def display_name(path: Path) -> str:
    """The path relative to the project root, or absolute if outside it."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def run_example(script: Path) -> tuple[int, float, str]:
    """Run one example script, returning (exit code, seconds, output).

    The child runs with the same interpreter as this script and the
    project root as its working directory. stderr is merged into stdout
    so the log reads in the order the example produced its output.
    """
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return result.returncode, time.monotonic() - started, result.stdout


def failure_reason(output: str) -> str:
    """Pick the lines from a failed run's output that explain the failure.

    Every example reports typed errors as an ``ERROR: <message>`` block on
    stderr (merged into the captured output), so the last such block is the
    reason when present. The message may span several lines -- a
    ``CommandError`` appends the failing command's captured stderr below
    the summary line -- and the example exits right after printing it, so
    the block runs to the end of the output. Otherwise fall back to the
    last non-empty line -- e.g. the exception line of an unexpected
    traceback.
    """
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].startswith("ERROR:"):
            return "\n".join(lines[index:])
    return lines[-1] if lines else "no output was produced"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the documented examples and log the output to examples.log.",
    )
    parser.add_argument(
        "--path",
        type=Path,
        metavar="DIR",
        help=(
            "run only the examples in the directory tree under DIR "
            "(e.g. examples/modules/lxd); an explicit path includes the "
            "directories the full sweep excludes"
        ),
    )
    args = parser.parse_args()

    if args.path is None:
        directories = EXAMPLES_DIRS
        scripts = sorted(
            path
            for directory in directories
            for path in directory.rglob("*.py")
            if not is_excluded(path)
        )
    else:
        directory = args.path.resolve()
        if not directory.is_dir():
            print(
                f"ERROR: --path {args.path} is not an existing directory",
                file=sys.stderr,
            )
            return 1
        directories = (directory,)
        scripts = sorted(directory.rglob("*.py"))
    if not scripts:
        dirs = ", ".join(str(directory) for directory in directories)
        print(f"ERROR: no example scripts found in {dirs}", file=sys.stderr)
        return 1

    failures: list[tuple[str, str]] = []
    with LOG_PATH.open("w", encoding="utf-8") as log:
        log.write(f"{SEPARATOR}\n")
        log.write(f"Examples run at {datetime.now().isoformat()}\n")
        log.write(f"Interpreter: {sys.executable}\n")
        dirs = ", ".join(display_name(d) for d in directories)
        log.write(f"Examples:    {len(scripts)} scripts from {dirs}\n")
        log.write(f"{SEPARATOR}\n")

        for script in scripts:
            name = display_name(script)
            print(f"Running {name} ...", flush=True)
            try:
                returncode, elapsed, output = run_example(script)
            except OSError as exc:
                # The interpreter could not even be launched for this script.
                # Record it as a failed run so the sweep still continues.
                returncode, elapsed, output = 1, 0.0, f"ERROR: {exc}\n"

            status = "OK" if returncode == 0 else f"FAILED (exit {returncode})"
            print(f"  {status} in {elapsed:.1f}s", flush=True)
            if returncode != 0:
                reason = failure_reason(output)
                print(f"  {name}: {reason}", file=sys.stderr, flush=True)
                failures.append((name, reason))

            log.write(f"\n{SEPARATOR}\n")
            log.write(f"== {name} -- {status}, {elapsed:.1f}s\n")
            log.write(f"{SEPARATOR}\n")
            log.write(output)
            if output and not output.endswith("\n"):
                log.write("\n")
            # Persist each run's output as it completes, so a later failure
            # (or an interrupted sweep) never loses the earlier sections.
            log.flush()

        log.write(f"\n{SEPARATOR}\n")
        log.write("== Summary\n")
        log.write(f"{SEPARATOR}\n")
        log.write(f"{len(scripts) - len(failures)} of {len(scripts)} examples passed\n")
        for name, reason in failures:
            log.write(f"FAILED: {name} -- {reason}\n")

    print(f"\nOutput written to {LOG_PATH}")
    if failures:
        names = ", ".join(name for name, _reason in failures)
        print(
            f"ERROR: {len(failures)} example(s) failed: {names}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
