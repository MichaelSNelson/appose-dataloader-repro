# Appose task body: build a trivial DataLoader and fetch one batch.
#
# Expected Appose-injected globals:
#   - num_workers (int)    -- number of DataLoader worker processes
#   - batch_size  (int)    -- batch size
#   - num_batches (int)    -- how many batches to fetch before declaring success
#   - persistent  (bool)   -- DataLoader persistent_workers flag
#
# Emits (via task.outputs):
#   - torch_version, platform, pid, start_method
#   - setup_seconds, first_batch_seconds, total_seconds
#   - batch_shape
#   - child_log_path (path to side-channel diagnostic log written by spawned
#     DataLoader children, regardless of whether the parent hangs)
import os
import sys
import tempfile
import time
import platform as _platform

import torch
import torch.multiprocessing as tmp
from torch.utils.data import DataLoader, TensorDataset


# Side-channel diagnostic log. Spawned DataLoader children cannot write to
# stdout/stderr (those streams are owned by the Appose JSON protocol), but
# they can append to a file. This lets us see how far the child gets even
# when the parent hangs forever waiting for a batch that never comes.
_CHILD_LOG_PATH = os.path.join(
    tempfile.gettempdir(),
    f"appose-dataloader-child-{os.getpid()}.log",
)


def _clog(msg):
    """Append to the side-channel log. Safe to call from spawned children."""
    try:
        with open(_CHILD_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.time():.3f} pid={os.getpid()} "
                    f"ppid={os.getppid()}] {msg}\n")
    except Exception:
        pass


def _log(msg):
    # stderr is surfaced via Appose's .debug() callback on the Java side
    print(f"[py] {msg}", file=sys.stderr, flush=True)
    _clog(msg)


# Instrument multiprocessing so we can see whether the spawn-path failure
# mode on Windows / macOS matches the fork-path _close_stdin block that
# carlosuc3m traced on Linux (apposed/appose#31). These hooks fire in BOTH
# the parent (when it tries to spawn workers) and in the children (because
# spawn re-imports __main__ and re-runs this script's module-level code).
import multiprocessing.util as _mpu
from multiprocessing.process import BaseProcess as _BaseProcess

_orig_close_stdin = _mpu._close_stdin
def _instrumented_close_stdin():
    _clog(f"BEFORE _close_stdin: sys.stdin={sys.stdin!r} "
          f"closed={getattr(sys.stdin, 'closed', '?')}")
    t0 = time.time()
    try:
        _orig_close_stdin()
        _clog(f"AFTER  _close_stdin: returned in {time.time()-t0:.4f}s")
    except BaseException as exc:
        _clog(f"EXC    _close_stdin: {type(exc).__name__}: {exc}")
        raise
_mpu._close_stdin = _instrumented_close_stdin

_orig_bootstrap = _BaseProcess._bootstrap
def _instrumented_bootstrap(self, *a, **kw):
    _clog(f"_bootstrap ENTER name={self.name} sys.stdin={sys.stdin!r}")
    try:
        rc = _orig_bootstrap(self, *a, **kw)
        _clog(f"_bootstrap EXIT  rc={rc}")
        return rc
    except BaseException as exc:
        _clog(f"_bootstrap RAISE {type(exc).__name__}: {exc}")
        raise
_BaseProcess._bootstrap = _instrumented_bootstrap

_clog(f"script import: start_method={tmp.get_start_method(allow_none=True)} "
      f"sys.platform={sys.platform} torch={torch.__version__}")

# Print the side-channel log path to stderr immediately so the Java driver
# can surface it even if the task hangs before reaching task.outputs. The
# Java side echoes [py-stderr] lines unconditionally.
print(f"[py] CHILD_LOG_PATH={_CHILD_LOG_PATH}", file=sys.stderr, flush=True)


t_setup_start = time.time()

# Appose injects these names into the task namespace.
nw = int(num_workers)  # noqa: F821
bs = int(batch_size)   # noqa: F821
nb = int(num_batches)  # noqa: F821
pw = bool(persistent)  # noqa: F821

start_method = tmp.get_start_method(allow_none=True)
_log(f"torch={torch.__version__} platform={sys.platform} pid={os.getpid()} "
     f"start_method={start_method} num_workers={nw} batch_size={bs} "
     f"num_batches={nb} persistent_workers={pw}")

# Use TensorDataset (a real class in torch.utils.data) rather than a
# script-defined Dataset subclass. On Windows + spawn, multiprocessing
# pickles the dataset by qualified name -- script-defined classes live in
# the synthetic <string> module and fail to pickle, masking the actual
# DataLoader hang we want to investigate. TensorDataset is fully picklable
# and lets the child get past spawn bootstrap.
n = max(64, bs * nb * 4)
ds = TensorDataset(
    torch.randn(n, 3, 32, 32),
    torch.zeros(n, 1, dtype=torch.long),
)
loader = DataLoader(
    ds,
    batch_size=bs,
    num_workers=nw,
    persistent_workers=(pw and nw > 0),
    shuffle=False,
    drop_last=False,
)
setup_seconds = time.time() - t_setup_start
_log(f"DataLoader constructed in {setup_seconds:.2f}s; fetching {nb} batch(es)...")

it = iter(loader)
t_first = time.time()
first_batch = next(it)
first_batch_seconds = time.time() - t_first
_log(f"first batch shape={tuple(first_batch[0].shape)} in {first_batch_seconds:.2f}s")

for i in range(1, nb):
    _ = next(it)
    _log(f"batch {i + 1}/{nb} ok")

total_seconds = time.time() - t_setup_start

task.outputs["torch_version"] = torch.__version__           # noqa: F821
task.outputs["platform"] = sys.platform
task.outputs["python_platform"] = _platform.platform()
task.outputs["pid"] = os.getpid()
task.outputs["start_method"] = str(start_method)
task.outputs["num_workers"] = nw
task.outputs["setup_seconds"] = float(setup_seconds)
task.outputs["first_batch_seconds"] = float(first_batch_seconds)
task.outputs["total_seconds"] = float(total_seconds)
task.outputs["batch_shape"] = list(first_batch[0].shape)
task.outputs["child_log_path"] = _CHILD_LOG_PATH
_log("done")
