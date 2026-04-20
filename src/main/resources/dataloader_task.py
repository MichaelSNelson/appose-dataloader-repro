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
import os
import sys
import time
import platform as _platform

import torch
import torch.multiprocessing as tmp
from torch.utils.data import Dataset, DataLoader


def _log(msg):
    # stderr is surfaced via Appose's .debug() callback on the Java side
    print(f"[py] {msg}", file=sys.stderr, flush=True)


class SyntheticDataset(Dataset):
    def __init__(self, n=64):
        self._n = n

    def __len__(self):
        return self._n

    def __getitem__(self, idx):
        # Trivial per-item work so worker processes actually do something.
        x = torch.randn(3, 32, 32)
        y = torch.zeros(1, dtype=torch.long)
        return x, y


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

ds = SyntheticDataset(n=max(64, bs * nb * 4))
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
_log("done")
