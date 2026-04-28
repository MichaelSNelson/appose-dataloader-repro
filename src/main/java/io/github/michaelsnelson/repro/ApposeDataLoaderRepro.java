package io.github.michaelsnelson.repro;

import org.apposed.appose.Appose;
import org.apposed.appose.Environment;
import org.apposed.appose.Service;
import org.apposed.appose.Service.Task;
import org.apposed.appose.Service.TaskStatus;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;
import java.util.stream.Stream;

/**
 * Minimal reproducer: PyTorch DataLoader with num_workers &gt; 0 hangs when run
 * inside an Appose Python worker, but succeeds with num_workers = 0.
 *
 * <p>Runs the same task twice:
 * <ol>
 *   <li>num_workers=0 -- expected to complete in under a second</li>
 *   <li>num_workers=2 -- expected to hang indefinitely (we time out)</li>
 * </ol>
 *
 * <p>Usage: {@code ./gradlew run --args="<hang_timeout_seconds>"} (default 60).
 */
public class ApposeDataLoaderRepro {

    private static final String ENV_NAME = "appose-dataloader-repro";
    private static final long BASELINE_TIMEOUT_SEC = 30;

    public static void main(String[] args) throws Exception {
        // Default 900s (15 min). The first Windows timing run showed spawn
        // workers taking 60-300+ seconds for cold python.exe startup +
        // site.py initialization inside the pixi env, with workers
        // reaching _worker_loop right around the 300s mark. To
        // distinguish "slow startup, batches eventually arrive" from
        // "true deadlock after worker_loop entry", we need to wait long
        // enough that startup is comfortably done. Override with the
        // first command-line arg in seconds for shorter runs.
        long hangTimeoutSec = args.length > 0 ? Long.parseLong(args[0]) : 900;

        String pixiToml = readResource("/pixi.toml");
        String script = readResource("/dataloader_task.py");

        log("Building pixi environment (first run downloads ~1-2 GB of torch CPU wheels)...");
        Environment env = Appose.pixi()
                .content(pixiToml)
                .scheme("pixi.toml")
                .name(ENV_NAME)
                .logDebug()
                .build();
        log("env.base = " + env.base());

        // Deploy sitecustomize.py into the env's site-packages BEFORE
        // starting the Appose service. Python imports sitecustomize during
        // interpreter startup of EVERY interpreter that finds it on
        // sys.path -- including the ones that multiprocessing.spawn
        // launches via subprocess.Popen on Windows. That gives us
        // child-visible instrumentation that class-level monkey-patches
        // in the parent task script cannot reach.
        String siteCustomize = readResource("/sitecustomize.py");
        List<Path> deployed = deploySitecustomize(Paths.get(env.base()), siteCustomize);
        if (deployed.isEmpty()) {
            log("WARNING: could not locate site-packages under " + env.base()
                    + " -- child-side instrumentation will not be available");
        } else {
            for (Path p : deployed) {
                log("Deployed sitecustomize.py to " + p);
            }
            log("Child-side log will be written to "
                    + System.getProperty("java.io.tmpdir")
                    + "appose-dataloader-sitecustomize.log");
        }

        try (Service py = env.python()) {
            py.debug(msg -> System.out.println("[py-stderr] " + msg));

            Result r0 = runOnce(py, script, 0, BASELINE_TIMEOUT_SEC);
            if (r0.status != TaskStatus.COMPLETE) {
                log("BASELINE FAILED at num_workers=0 (status=" + r0.status + ", err=" + r0.error
                        + "). Aborting -- the environment itself is broken.");
                System.exit(2);
            }

            Result r2 = runOnce(py, script, 2, hangTimeoutSec);

            System.out.println();
            System.out.println("============================================================");
            System.out.println("SUMMARY");
            System.out.println("============================================================");
            System.out.printf("  num_workers=0 : status=%s, total=%.2fs, outputs=%s%n",
                    r0.status, r0.elapsedSeconds, r0.outputs);
            System.out.printf("  num_workers=2 : status=%s, elapsed=%.2fs, outputs=%s%n",
                    r2.status, r2.elapsedSeconds, r2.outputs);
            if (r2.status == TaskStatus.COMPLETE) {
                System.out.println("  -> num_workers=2 COMPLETED. No reproducer here.");
                System.exit(0);
            } else {
                System.out.printf("  -> num_workers=2 HUNG (timed out after %ds).%n", hangTimeoutSec);
                System.exit(1);
            }
        }
    }

    private static Result runOnce(Service py, String script, int numWorkers, long timeoutSec)
            throws InterruptedException {
        log(String.format("=== Running task with num_workers=%d (timeout %ds) ===",
                numWorkers, timeoutSec));

        Map<String, Object> inputs = new LinkedHashMap<>();
        inputs.put("num_workers", numWorkers);
        inputs.put("batch_size", 4);
        inputs.put("num_batches", 2);
        inputs.put("persistent", true);

        Task task = py.task(script, inputs);
        task.listen(event -> {
            log("[evt] " + event.responseType + " status=" + task.status);
            // Surface the child-log path as soon as the script reaches the
            // line that publishes it (it ends up in task.outputs even on
            // hang because we set it before constructing the DataLoader).
            Object cl = task.outputs.get("child_log_path");
            if (cl != null) {
                log("[child_log] " + cl);
            }
        });

        long t0 = System.nanoTime();
        Thread waiter = new Thread(() -> {
            try {
                task.waitFor();
            } catch (Throwable t) {
                // waitFor throws on TaskException; we read status/error after the join anyway.
                log("[waiter] waitFor threw: " + t);
            }
        }, "appose-task-waiter-" + numWorkers);
        waiter.setDaemon(true);
        waiter.start();
        waiter.join(timeoutSec * 1000L);

        double elapsed = (System.nanoTime() - t0) / 1e9;
        if (waiter.isAlive()) {
            log(String.format("HANG: task still running after %ds -- calling cancel()", timeoutSec));
            try {
                task.cancel();
            } catch (Throwable t) {
                log("cancel() threw: " + t);
            }
            waiter.interrupt();
            // Give cancel a moment; do not block indefinitely.
            waiter.join(2000);
        }

        return new Result(task.status, task.error, new LinkedHashMap<>(task.outputs), elapsed);
    }

    /**
     * Walks the env tree looking for site-packages directories and writes
     * sitecustomize.py to each one found. Pixi env layouts:
     *   Windows: &lt;env_base&gt;/.pixi/envs/default/Lib/site-packages/
     *   Unix:    &lt;env_base&gt;/.pixi/envs/default/lib/python3.X/site-packages/
     * We don't hardcode either; we walk and write to whatever site-packages
     * directories appear under the env base.
     */
    private static List<Path> deploySitecustomize(Path envBase, String content)
            throws IOException {
        if (!Files.isDirectory(envBase)) {
            return List.of();
        }
        try (Stream<Path> walk = Files.walk(envBase, 6)) {
            List<Path> sitePackages = walk
                    .filter(Files::isDirectory)
                    .filter(p -> p.getFileName() != null
                            && "site-packages".equals(p.getFileName().toString()))
                    .collect(Collectors.toList());
            for (Path sp : sitePackages) {
                Path target = sp.resolve("sitecustomize.py");
                Files.writeString(target, content, StandardCharsets.UTF_8);
            }
            return sitePackages.stream()
                    .map(sp -> sp.resolve("sitecustomize.py"))
                    .collect(Collectors.toList());
        }
    }

    private static String readResource(String path) throws IOException {
        try (InputStream in = ApposeDataLoaderRepro.class.getResourceAsStream(path)) {
            if (in == null) throw new IOException("Resource not found: " + path);
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    private static void log(String msg) {
        System.out.println("[java] " + msg);
    }

    private record Result(TaskStatus status, String error, Map<String, Object> outputs,
                          double elapsedSeconds) {
    }
}
