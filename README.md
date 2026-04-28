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

### Linux / macOS

```bash
./gradlew run --args="60"
```

### Windows

Use the bundled `gradlew.bat` from `cmd.exe` or PowerShell. Either of these
works (PowerShell needs the leading `.\` for non-PATH executables):

```cmd
gradlew.bat run --args="60"
```

```powershell
.\gradlew.bat run --args="60"
```

Requirements on Windows:

- JDK 21+ visible on `PATH`, or `JAVA_HOME` pointing at one. Verify with
  `java --version`.
- A working internet connection on first run. Pixi will download a 1-2 GB
  Python + PyTorch CPU bundle into `.pixi\` next to the project; subsequent
  runs reuse the cache.
- Long-path support enabled on Windows is recommended; pixi environments
  bury wheels several layers deep. If gradle complains about path length,
  enable long paths via `gpedit` or the registry, or run from a short-path
  directory like `C:\repro\appose-dataloader-repro`.

There is no QuPath involvement: this is a standalone Java program that
embeds Appose directly, the same way QuPath does. No hardware needed.

### Argument

`60` is the hang timeout in seconds for the `num_workers=2` case. The
baseline (`num_workers=0`) has its own 30-second timeout, which is only
there to catch a broken environment; it should complete in well under a
second.

### Reading the diagnostic output

The Python task writes a side-channel log that captures what spawned
DataLoader children see, even when the parent hangs. The path is printed
to stderr near the top of the run as:

```
[py-stderr] [py] CHILD_LOG_PATH=/tmp/appose-dataloader-child-12345.log
```

On Windows it lands in `%TEMP%\appose-dataloader-child-<pid>.log`. Open it
after the run -- the file is most informative on a hang, where it shows
how far the spawned/forked DataLoader children got before deadlocking.

The log instruments two functions, in BOTH the parent and any children
that re-import the script under `spawn`:

- `multiprocessing.process.BaseProcess._bootstrap` -- entry point of every
  child process.
- `multiprocessing.util._close_stdin` -- the function carlosuc3m identified
  as the Linux fork-path deadlock site
  ([apposed/appose#31](https://github.com/apposed/appose/issues/31)).

What the log answers:

1. **Does the spawned/forked child reach `_bootstrap` at all?** A missing
   ENTER line means it dies before the child's bootstrap (e.g. import
   failure under spawn re-import).
2. **Does `_close_stdin` block?** A `BEFORE` with no matching `AFTER`
   means the same deadlock seen on Linux is biting on this platform too.
   `BEFORE` + `AFTER` in milliseconds means the deadlock is somewhere
   else (PyTorch worker init, mp queue handshake, etc.).
3. **What does `sys.stdin` look like in the child?** Same handle as the
   parent or a fresh one. Hints at handle inheritance vs re-open.

Exit codes:

- `0` -- both cases completed (no reproducer here, environment behaves
  differently than reported).
- `1` -- `num_workers=2` case hung and was cancelled after the timeout.
- `2` -- baseline (`num_workers=0`) failed; environment is broken.

## Observed behaviour

Reproduced on Linux (WSL2 Ubuntu 24.04, Python 3.12, PyTorch 2.10 CPU, default
`fork` start method). Originally observed on Windows 10/11 (Python 3.11, PyTorch
2.x CUDA, `spawn` start method) in the QuPath DL Pixel Classifier extension.

```
[java] === Running task with num_workers=0 (timeout 30s) ===
[py] torch=2.10.0 platform=linux pid=... start_method=None num_workers=0 ...
[py] DataLoader constructed in 0.00s; fetching 2 batch(es)...
[py] first batch shape=(4, 3, 32, 32) in 0.00s
[py] batch 2/2 ok
[py] done
[java] [evt] COMPLETION status=COMPLETE

[java] === Running task with num_workers=2 (timeout 45s) ===
[py] torch=2.10.0 platform=linux pid=... start_method=None num_workers=2 ...
[py] DataLoader constructed in 0.00s; fetching 2 batch(es)...
<no further output -- hangs>
[java] HANG: task still running after 45s -- calling cancel()
```

```
SUMMARY
  num_workers=0 : status=COMPLETE, total=2.04s
  num_workers=2 : status=RUNNING, elapsed=45.00s  (hung, cancelled)
```

Because the hang reproduces under `fork` (Linux default), the issue is not
exclusively about Windows `spawn` re-importing the worker module. Both start
methods interact with Appose's stdin/stdout-based JSON protocol; under
`fork`, the children inherit file descriptors for the pipe Appose uses. Under
`spawn`, they re-exec the interpreter, which triggers Appose's bootstrap a
second time. Both paths appear to deadlock.

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
