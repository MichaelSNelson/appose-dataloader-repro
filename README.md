# appose-dataloader-repro

Minimal reproducer for a hang observed when running a PyTorch `DataLoader` with
`num_workers > 0` inside an [Appose](https://github.com/apposed/appose) Python
worker.

## What it shows

One Java entry point runs the same task twice against one Appose Python
service:

1. **`num_workers=0`** (single-threaded data loading) -- completes in well
   under a second.
2. **`num_workers=2`** (DataLoader spawns child processes) -- the Appose task
   hangs indefinitely after the DataLoader is constructed; no batch is ever
   delivered. No exception, no log, no batch.

Both cases use the same trivial synthetic dataset (3x32x32 random tensors),
no GPU, no real I/O, no model. The only variable is `num_workers`.

## Requirements

- JDK 21+
- Internet access on first run (pixi downloads Python 3.12 + PyTorch CPU build,
  roughly 1-2 GB; cached under `.pixi/` thereafter)

## Running

```bash
./gradlew run --args="60"
```

`60` is the hang timeout in seconds for the `num_workers=2` case. The
baseline (`num_workers=0`) has its own 30-second timeout, which is only
there to catch a broken environment; it should complete in well under a
second.

Exit codes:

- `0` -- both cases completed (no reproducer here, environment behaves
  differently than reported).
- `1` -- `num_workers=2` case hung and was cancelled after the timeout.
- `2` -- baseline (`num_workers=0`) failed; environment is broken.

## Observed behaviour

On Windows (Python 3.12, PyTorch 2.10 CPU) and Linux (WSL2, Ubuntu 24.04):

```
[java] === Running task with num_workers=0 (timeout 30s) ===
[py-stderr] [py] torch=2.10.x platform=win32 pid=... start_method=None num_workers=0 ...
[py-stderr] [py] DataLoader constructed in 0.00s; fetching 2 batch(es)...
[py-stderr] [py] first batch shape=(4, 3, 32, 32) in 0.0xs
[py-stderr] [py] done
[java] [evt] COMPLETION status=COMPLETE

[java] === Running task with num_workers=2 (timeout 60s) ===
[py-stderr] [py] torch=2.10.x platform=win32 pid=... start_method=None num_workers=2 ...
[py-stderr] [py] DataLoader constructed in 0.0xs; fetching 2 batch(es)...
<no further output -- hangs>
[java] HANG: task still running after 60s -- calling cancel()
```

## Context

Filed for discussion with the Appose maintainers after observing this in the
[QuPath DL Pixel Classifier
extension](https://github.com/uw-loci/qupath-extension-dl-pixel-classifier).
That extension runs all model training inside a single long-lived Appose
worker; PyTorch's `DataLoader` with `num_workers > 0` would be a useful
throughput optimisation (GPU sits 30-70% idle between batches at
`num_workers=0`), but every attempt to enable it hangs in exactly this
pattern.

Working hypothesis: `torch.multiprocessing` (which uses the stdlib
`multiprocessing` module) spawns child processes that re-import the worker
script under `spawn` semantics; those children inherit or attempt to re-open
stdin/stdout, which Appose is already using for its JSON protocol. Confirmed
or ruled out by Appose maintainers -- see upstream discussion.

## Files

- `src/main/java/io/github/michaelsnelson/repro/ApposeDataLoaderRepro.java` --
  Java driver (builds pixi env, runs the task twice, enforces timeouts).
- `src/main/resources/dataloader_task.py` -- the Appose task body.
- `src/main/resources/pixi.toml` -- minimal pixi environment spec
  (Python 3.12 + PyTorch CPU + numpy).
