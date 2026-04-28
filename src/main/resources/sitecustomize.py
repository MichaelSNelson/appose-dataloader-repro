# sitecustomize.py -- auto-imported by every Python interpreter that finds
# this file on its sys.path. Python's site.py runs sitecustomize during
# interpreter startup, BEFORE __main__ is loaded. That means it fires:
#   - in the Appose worker (parent) on its initial launch, and
#   - in every multiprocessing spawn child on its python.exe re-execution
# Class-level monkey-patches set in the parent do NOT propagate to spawn
# children (they load a pristine stdlib), so this file is the only place
# instrumentation can be guaranteed to run in both.
#
# All output goes to a file under the system temp dir. Side-channel only:
# the spawned DataLoader children share stdin/stdout with the Appose
# parent worker, which owns those streams for the JSON protocol.
import os
import sys
import time
import tempfile

_LOG_PATH = os.path.join(
    tempfile.gettempdir(),
    "appose-dataloader-sitecustomize.log",
)


def _slog(msg):
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.time():.3f} pid={os.getpid()} "
                    f"ppid={os.getppid()}] {msg}\n")
    except Exception:
        pass


_slog(f"sitecustomize loaded: argv={sys.argv!r} "
      f"executable={sys.executable!r} sys.stdin={sys.stdin!r}")

try:
    import multiprocessing.util as _mpu
    from multiprocessing.process import BaseProcess as _BaseProcess

    _orig_close_stdin = _mpu._close_stdin

    def _instrumented_close_stdin():
        _slog(f"BEFORE _close_stdin: sys.stdin={sys.stdin!r} "
              f"closed={getattr(sys.stdin, 'closed', '?')} "
              f"fd={getattr(sys.stdin, 'fileno', lambda: '?')() if hasattr(sys.stdin, 'fileno') else '?'}")
        t0 = time.time()
        try:
            _orig_close_stdin()
            _slog(f"AFTER  _close_stdin: returned in {time.time()-t0:.4f}s")
        except BaseException as exc:
            _slog(f"EXC    _close_stdin: {type(exc).__name__}: {exc}")
            raise

    _mpu._close_stdin = _instrumented_close_stdin

    _orig_bootstrap = _BaseProcess._bootstrap

    def _instrumented_bootstrap(self, *a, **kw):
        _slog(f"_bootstrap ENTER name={self.name} sys.stdin={sys.stdin!r}")
        try:
            rc = _orig_bootstrap(self, *a, **kw)
            _slog(f"_bootstrap EXIT  rc={rc}")
            return rc
        except BaseException as exc:
            _slog(f"_bootstrap RAISE {type(exc).__name__}: {exc}")
            raise

    _BaseProcess._bootstrap = _instrumented_bootstrap

    _slog("multiprocessing instrumentation installed")
except Exception as _e:
    _slog(f"instrumentation install failed: {type(_e).__name__}: {_e}")
