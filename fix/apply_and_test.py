"""Apply the apposed/appose#31 fix to the *installed* appose python_worker.py,
run the Java-free DataLoader(num_workers=2) harness before and after, then
restore the original. Cross-platform (Linux fork + Windows/macOS spawn).

Run with the interpreter that has appose installed (the pixi env this repo
builds), e.g.:

    <env>/bin/python fix/apply_and_test.py            # Linux/macOS
    <env>\\python.exe fix\\apply_and_test.py            # Windows

Expected output:
    BEFORE (stock):   STATUS=TIMEOUT     <- the hang
    AFTER  (patched): STATUS=COMPLETE    <- fixed

It edits a copy-in-place and always restores from a .orig backup, so the
environment is left exactly as found.
"""
import os
import subprocess
import sys

import appose.python_worker as w

WORKER = w.__file__
BACKUP = WORKER + ".orig"
HERE = os.path.dirname(os.path.abspath(__file__))
DRIVER = os.path.join(HERE, "drive_worker.py")

PATCH_MARKER = "_protocol_in"


def patched_source(src: str) -> str:
    if PATCH_MARKER in src:
        raise SystemExit("worker already patched; aborting")
    src = src.replace(
        "import ast\nimport os\nimport sys\nimport traceback",
        "import ast\nimport io\nimport os\nimport sys\nimport traceback", 1)
    src = src.replace(
        "from appose.util.message import Args\n",
        "from appose.util.message import Args\n\n_protocol_in = None\n_protocol_out = None\n", 1)
    src = src.replace(
        "            try:\n"
        "                line = input().strip()\n"
        "            except EOFError:\n"
        "                line = None",
        "            line = _protocol_in.readline()\n"
        "            if line == \"\":\n"
        "                line = None\n"
        "            else:\n"
        "                line = line.strip()", 1)
    src = src.replace(
        "def main() -> None:\n    worker = Worker()",
        "def main() -> None:\n"
        "    global _protocol_in, _protocol_out\n"
        "    _protocol_in = io.TextIOWrapper(io.FileIO(os.dup(0), \"r\"), encoding=\"utf-8\")\n"
        "    _protocol_out = io.TextIOWrapper(io.FileIO(os.dup(1), \"w\"), encoding=\"utf-8\", line_buffering=True)\n"
        "    _devnull = os.open(os.devnull, os.O_RDWR)\n"
        "    os.dup2(_devnull, 0)\n"
        "    os.dup2(_devnull, 1)\n"
        "    os.close(_devnull)\n"
        "    sys.stdin = open(os.devnull, \"r\")\n"
        "    sys.stdout = _protocol_out\n"
        "    worker = Worker()", 1)
    if PATCH_MARKER not in src:
        raise SystemExit("patch did not apply -- worker source differs from expected")
    return src


def run_harness(label: str) -> None:
    print(f"--- {label}: python {DRIVER} (num_workers=2, 45s timeout)")
    r = subprocess.run([sys.executable, DRIVER, sys.executable, "45"])
    print(f"    exit={r.returncode}")


def main():
    original = open(WORKER, "r", encoding="utf-8").read()
    print(f"worker: {WORKER}")

    run_harness("BEFORE (stock)")

    with open(BACKUP, "w", encoding="utf-8") as f:
        f.write(original)
    try:
        with open(WORKER, "w", encoding="utf-8") as f:
            f.write(patched_source(original))
        run_harness("AFTER  (patched)")
    finally:
        with open(WORKER, "w", encoding="utf-8") as f:
            f.write(original)
        os.remove(BACKUP)
        assert PATCH_MARKER not in open(WORKER, encoding="utf-8").read()
        print("restored worker to original")


if __name__ == "__main__":
    main()
