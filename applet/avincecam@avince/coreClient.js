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

    /* Stop all calls started by this client and drop their results. */
    destroy() {
        this._destroyed = true;
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
