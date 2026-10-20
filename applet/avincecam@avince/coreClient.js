/*
 * aVinceCam – asynchronous calls of the core.
 *
 * The applet lives inside the Cinnamon process, so nothing here may block:
 * the core runs as a separate process, its output is read asynchronously,
 * and a call that takes too long is ended. Only the child process started
 * here is ever stopped – never any other process.
 */

const Gio = imports.gi.Gio;
const GLib = imports.gi.GLib;

const UUID = "avincecam@avince";

// Inside Cinnamon modules live under imports.applets; the offline tests
// (plain cjs) put the applet folder on imports.searchPath instead.
function loadPresenter() {
    try {
        return imports.applets[UUID].presenter.Presenter;
    } catch (e) {
        return imports.presenter.Presenter;
    }
}
const Presenter = loadPresenter();

/*
 * Find the core. Order:
 *  1. bundled with the applet:  <applet>/core/avincecam-core
 *  2. development tree:         <repo>/core/avincecam-core
 *     (the applet folder is <repo>/applet/avincecam@avince)
 * Returns the argument list prefix, or null if no core was found.
 */
function findCore(appletPath) {
    const candidates = [
        GLib.build_filenamev([appletPath, "core", "avincecam-core"]),
        GLib.build_filenamev([appletPath, "..", "..", "core", "avincecam-core"])
    ];
    const python = GLib.find_program_in_path("python3");
    if (!python)
        return null;
    for (const path of candidates) {
        if (GLib.file_test(path, GLib.FileTest.IS_REGULAR))
            return [python, "-I", path];
    }
    return null;
}

var CoreClient = class CoreClient {
    constructor(commandPrefix) {
        // e.g. ["/usr/bin/python3", "-I", ".../avincecam-core"]
        this.commandPrefix = commandPrefix;
        this._running = new Set();
        this._watches = new Set();
        this._destroyed = false;
    }

    /*
     * Run the core with `args`. `callback(result)` receives
     * { ok, data, error } (see Presenter.parseCoreResult) and is never
     * called after destroy().
     */
    run(args, timeoutSeconds, callback) {
        if (!this.commandPrefix) {
            callback({ ok: false, data: null,
                       error: { code: "core-missing", message: null, detail: null } });
            return;
        }

        let proc;
        try {
            proc = Gio.Subprocess.new(
                this.commandPrefix.concat(args),
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
            );
        } catch (e) {
            callback({ ok: false, data: null,
                       error: { code: "core-missing", message: null, detail: String(e) } });
            return;
        }

        const call = { proc: proc, timer: 0, timedOut: false,
                       cancellable: new Gio.Cancellable() };
        this._running.add(call);

        call.timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, timeoutSeconds, () => {
            call.timer = 0;
            call.timedOut = true;
            // Our own child process only.
            proc.force_exit();
            return GLib.SOURCE_REMOVE;
        });

        proc.communicate_utf8_async(null, call.cancellable, (p, res) => {
            if (call.timer) {
                GLib.source_remove(call.timer);
                call.timer = 0;
            }
            this._running.delete(call);
            if (this._destroyed)
                return;

            let result;
            try {
                const [, stdout, stderr] = p.communicate_utf8_finish(res);
                if (call.timedOut) {
                    result = { ok: false, data: null,
                               error: { code: "core-timeout", message: null,
                                        detail: String(timeoutSeconds) + " s" } };
                } else {
                    result = Presenter.parseCoreResult(stdout, p.get_exit_status(), stderr);
                }
            } catch (e) {
                result = { ok: false, data: null,
                           error: { code: call.timedOut ? "core-timeout" : "core-failed",
                                    message: null, detail: String(e) } };
            }
            callback(result);
        });
    }

    get busy() {
        return this._running.size > 0;
    }

    /*
     * Start a preview. The core checks everything, prints one JSON line and
     * then becomes the viewer (same process). Callbacks:
     *   onStarted(result)            first answer, { ok, data, error }
     *   onExit(exitStatus, signaled) when the viewer has ended
     * Returns the Gio.Subprocess, or null if it could not be started.
     */
    startPreview(args, startTimeoutSeconds, onStarted, onExit) {
        if (!this.commandPrefix) {
            onStarted({ ok: false, data: null,
                        error: { code: "core-missing", message: null, detail: null } });
            return null;
        }
        let proc;
        try {
            proc = Gio.Subprocess.new(
                this.commandPrefix.concat(args),
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE
            );
        } catch (e) {
            onStarted({ ok: false, data: null,
                        error: { code: "core-missing", message: null, detail: String(e) } });
            return null;
        }

        let answered = false;
        const answer = result => {
            if (answered || this._destroyed)
                return;
            answered = true;
            onStarted(result);
        };

        const timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, startTimeoutSeconds, () => {
            answer({ ok: false, data: null,
                     error: { code: "core-timeout", message: null,
                              detail: String(startTimeoutSeconds) + " s" } });
            proc.force_exit();  // our own child only
            return GLib.SOURCE_REMOVE;
        });

        const stream = new Gio.DataInputStream({ base_stream: proc.get_stdout_pipe() });
        stream.read_line_async(GLib.PRIORITY_DEFAULT, null, (s, res) => {
            GLib.source_remove(timer);
            let line = null;
            try {
                [line] = s.read_line_finish_utf8(res);
            } catch (e) {
                line = null;
            }
            // Exit status is not known yet; a JSON answer with errors means failure.
            answer(Presenter.parseCoreResult(line || "", line ? 0 : 1, ""));
        });

        proc.wait_async(null, (p, res) => {
            try {
                p.wait_finish(res);
            } catch (e) {
                // nothing to do: the process is gone either way
            }
            if (this._destroyed)
                return;
            answer({ ok: false, data: null,
                     error: { code: "core-failed", message: null, detail: null } });
            onExit(p.get_if_exited() ? p.get_exit_status() : -1, p.get_if_signaled());
        });
        return proc;
    }

    /*
     * Run a long-lived core command that prints one JSON line per change
     * (the "watch" command). `onAnswer(result)` gets every line as
     * { ok, data, error }; `onExit()` is called once when the process has
     * ended. The core ends by itself when its stdin closes, so it never
     * outlives Cinnamon. Returns a handle with stop(), or null.
     */
    watch(args, onAnswer, onExit) {
        if (!this.commandPrefix)
            return null;
        let proc;
        try {
            proc = Gio.Subprocess.new(
                this.commandPrefix.concat(args),
                Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE |
                Gio.SubprocessFlags.STDERR_SILENCE
            );
        } catch (e) {
            return null;
        }
        const handle = {
            proc: proc,
            stopped: false,
            cancellable: new Gio.Cancellable(),
            stop: () => {
                if (handle.stopped)
                    return;
                handle.stopped = true;
                handle.cancellable.cancel();
                proc.force_exit();  // our own child only
                this._watches.delete(handle);
            }
        };
        this._watches.add(handle);

        const stream = new Gio.DataInputStream({ base_stream: proc.get_stdout_pipe() });
        const readNext = () => {
            stream.read_line_async(GLib.PRIORITY_DEFAULT, handle.cancellable, (s, res) => {
                let line = null;
                try {
                    [line] = s.read_line_finish_utf8(res);
                } catch (e) {
                    line = null;
                }
                if (line === null || handle.stopped || this._destroyed)
                    return;
                onAnswer(Presenter.parseCoreResult(line, 0, ""));
                readNext();
            });
        };
        readNext();

        proc.wait_async(null, (p, res) => {
            try {
                p.wait_finish(res);
            } catch (e) {
                // gone either way
            }
            this._watches.delete(handle);
            if (!handle.stopped && !this._destroyed)
                onExit();
        });
        return handle;
    }

    /* Stop all calls started by this client and drop their results. */
    destroy() {
        this._destroyed = true;
        for (const handle of Array.from(this._watches))
            handle.stop();
        for (const call of this._running) {
            if (call.timer) {
                GLib.source_remove(call.timer);
                call.timer = 0;
            }
            call.cancellable.cancel();
            call.proc.force_exit();
        }
        this._running.clear();
    }
};
