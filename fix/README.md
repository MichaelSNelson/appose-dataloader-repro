# Proposed fix for apposed/appose#31

`DataLoader(num_workers > 0)` inside an Appose Python worker deadlocks before
the first batch. Root cause: **child processes spawned by task code inherit
Appose's protocol stdio (fd 0 / fd 1)** and hang during their own stdio setup.

Two platform-specific manifestations of the same cause, confirmed with py-spy
native stack dumps:

- **Linux (fork):** the child inherits the parent's `sys.stdin` object and hangs
  in `multiprocessing.util._close_stdin()` → `sys.stdin.close()` on the inherited
  protocol pipe. (This is the mechanism carlosuc3m traced in the issue.)
- **Windows / macOS (spawn):** the child is a fresh interpreter that inherits OS
  handles (not Python objects) and hangs inside `Py_InitializeFromConfig` doing a
  blocking stdio flush/seek (`fflush → lseek → NtQueryInformationFile`) on the
  inherited protocol handle — *before* `site.py` runs. The parent task thread sits
  in `next(it) → queue.get → WaitForMultipleObjects`, identical in shape to Linux.

## The fix (`python_worker.py.patch`)

At the top of `main()`, keep private duplicates of fd 0 / fd 1 for the JSON
protocol, then point the real fd 0 / fd 1 at `os.devnull`; the receiver reads the
private stdin dup and `sys.stdout` is routed to the private stdout dup. Spawned
children then inherit harmless empty std streams while the protocol keeps working.

It closes both failure modes because it acts at **both** levels:
- `sys.stdin = open(os.devnull)` fixes Linux fork (`_close_stdin` closes devnull, instant);
- `os.dup2(devnull, 0/1)` fixes Windows/macOS spawn (children inherit devnull handles).

A `sys.stdin`-object swap *alone* is **not** enough for Windows, because spawn
children inherit OS handles rather than the Python object — the fd-level `dup2` is
the essential part. Bonus: with fd 1 pointed at devnull, a stray `print()` in user
or child code can no longer corrupt the JSON protocol.

~40 lines, no new dependencies.

## Verifying it (no Java/Gradle needed)

`drive_worker.py` speaks the same stdin/stdout JSON protocol the Java `Service`
uses, so you can test the worker fix directly. Run with the interpreter that has
appose installed (the pixi env this repo builds):

```
<env>/bin/python  fix/apply_and_test.py     # Linux/macOS
<env>\python.exe   fix\apply_and_test.py     # Windows
```

It runs the `DataLoader(num_workers=2)` harness against the stock worker (→
`STATUS=TIMEOUT`, the hang), then against a patched copy (→ `STATUS=COMPLETE`),
and restores the worker to its original state. Verified on Linux (Python 3.12,
fork) and Windows 11 (Python 3.12, spawn), both with PyTorch 2.10.
