"""Java-free driver for the Appose Python worker, for testing the
apposed/appose#31 fix without building the Gradle/Java side.

It speaks the same stdin/stdout JSON protocol the Appose Java Service uses:
spawn `python -c "import appose.python_worker; appose.python_worker.main()"`,
send one EXECUTE request whose script builds a DataLoader(num_workers=2) and
pulls two batches, then wait for COMPLETION / FAILURE.

Run it with the *same* interpreter that has appose installed, e.g. the pixi
env this repo builds:

    <env>/bin/python fix/drive_worker.py           # default 60s timeout

Against a stock appose-python it TIMES OUT (the hang this repo demonstrates).
Against a worker patched per fix/python_worker.py.patch it prints STATUS=COMPLETE
in ~1-2s. Use fix/apply_and_test.py to run both automatically.
"""
import json
import subprocess
import sys
import threading
import time
import uuid

TASK_BODY = """
import time, torch
from torch.utils.data import DataLoader, TensorDataset
t0 = time.time()
ds = TensorDataset(torch.randn(64, 3, 32, 32), torch.zeros(64, 1, dtype=torch.long))
loader = DataLoader(ds, batch_size=4, num_workers=2, persistent_workers=True)
it = iter(loader)
b = next(it)
_ = next(it)
task.outputs["first_batch_shape"] = list(b[0].shape)
task.outputs["elapsed"] = time.time() - t0
"""


def main():
    python = sys.argv[1] if len(sys.argv) > 1 else sys.executable
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0

    proc = subprocess.Popen(
        [python, "-c", "import appose.python_worker; appose.python_worker.main()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    # Drain stderr so worker warnings can't fill the pipe.
    threading.Thread(target=proc.stderr.read, daemon=True).start()

    tid = str(uuid.uuid4())
    req = {"task": tid, "requestType": "EXECUTE", "script": TASK_BODY, "inputs": {}}
    proc.stdin.write(json.dumps(req) + "\n")
    proc.stdin.flush()

    result = {"status": "TIMEOUT", "outputs": None}

    def reader():
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            rt = msg.get("responseType")
            if rt == "COMPLETION":
                result["status"] = "COMPLETE"
                result["outputs"] = msg.get("outputs")
                return
            if rt == "FAILURE":
                result["status"] = "FAILED"
                result["outputs"] = msg.get("error")
                return

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    t.join(timeout)
    proc.kill()
    print(f"STATUS={result['status']} OUTPUTS={result['outputs']}")
    sys.exit(0 if result["status"] == "COMPLETE" else 1)


if __name__ == "__main__":
    main()
