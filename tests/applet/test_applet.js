/*
 * Offline tests for the applet modules, run with plain cjs (no Cinnamon):
 *   cjs tests/applet/test_applet.js <repo>
 *
 * presenter.js is tested with answers of the fake core; coreClient.js with
 * real subprocesses of the fake core, including timeout and error cases.
 * No camera is touched (the fake core sets AVINCECAM_NO_HARDWARE=1).
 */

const GLib = imports.gi.GLib;

const REPO = ARGV[0];
const APPLET = GLib.build_filenamev([REPO, "applet", "avincecam@avince"]);
const FAKE = GLib.build_filenamev([REPO, "tests", "fake-core", "avincecam-core-fake"]);
imports.searchPath.unshift(APPLET);

const Presenter = imports.presenter.Presenter;
const CoreClient = imports.coreClient;

const _ = text => text;
let failures = 0;
let passed = 0;

function check(name, condition, info) {
    if (condition) {
        passed++;
    } else {
        failures++;
        print("FAIL: " + name + (info !== undefined ? "  -> " + JSON.stringify(info) : ""));
    }
}

function fake(scenario, args) {
    const [, out, , status] = GLib.spawn_sync(null,
        ["python3", "-I", FAKE, "--scenario=" + scenario].concat(args),
        null, GLib.SpawnFlags.SEARCH_PATH, null);
    let code = 0;
    try {
        GLib.spawn_check_wait_status(status);
    } catch (e) {
        code = 1;  // non-zero exit status raises an error
    }
    return [new TextDecoder().decode(out), code];
}

function fakeJson(scenario, args) {
    return JSON.parse(fake(scenario, args)[0]);
}

// ---------------------------------------------------------------- presenter

(function testTitlesAndSubtitles() {
    const cams = fakeJson("mixed", ["scan"]).cameras;
    check("three cameras", cams.length === 3, cams.length);
    check("front title", Presenter.cameraTitle(cams[0], _) === "Front camera");
    check("back title", Presenter.cameraTitle(cams[1], _) === "Back camera");
    check("ipu3 subtitle", Presenter.cameraSubtitle(cams[0], _) === "OV5670 · libcamera (IPU3)",
          Presenter.cameraSubtitle(cams[0], _));
    check("usb title", Presenter.cameraTitle(cams[2], _) === "HD Webcam");
    check("usb subtitle", Presenter.cameraSubtitle(cams[2], _) === "USB (UVC) · external",
          Presenter.cameraSubtitle(cams[2], _));
})();

(function testDetailsBeforeAndAfterProbe() {
    const scanCam = fakeJson("latitude", ["scan"]).cameras[0];
    const before = Presenter.detailLines(scanCam, _);
    check("not queried yet", before.includes("Picture sizes: not queried"), before);
    check("no invented frame rate", !before.some(l => l.startsWith("Frame rate")), before);

    const probed = fakeJson("latitude", ["probe", scanCam.id]).cameras[0];
    const after = Presenter.detailLines(probed, _);
    check("sensor size", after.includes("Sensor: 2624×1980"), after);
    check("formats", after.includes("Picture: 17 sizes, 160×120 to 1280×720 (NV12)"), after);
    check("frame rate", after.includes("Frame rate: up to 30 fps (according to libcamera)"), after);
    check("libcamera id", after.some(l => l.indexOf("_SB_.PCI0.LNK0") >= 0), after);
})();

(function testWarnings() {
    const back = fakeJson("latitude", ["scan"]).cameras[1];
    const texts = back.warnings.map(w => Presenter.warningText(w, _));
    check("focus motor note", texts.some(t => t.indexOf("focus motor") >= 0), texts);
    check("unknown warning shown raw", Presenter.warningText("something-new", _) === "something-new");
})();

(function testUvcFrameRateSource() {
    const cam = { formats: [{ pixel_format: "MJPG", width: 1920, height: 1080, fps: [30] }],
                  frame_rate_limits: { min: 15, max: 30, source: "V4L2 frame intervals" } };
    check("uvc rate source", Presenter.frameRateText(cam, _) === "up to 30 fps (reported by the camera)",
          Presenter.frameRateText(cam, _));
})();

(function testPreviewErrors() {
    const busy = Presenter.errorText({ code: "camera-busy", detail: "qcam" }, _);
    check("busy names the program", busy === "The camera is in use by another program. (qcam)", busy);
    const running = Presenter.errorText({ code: "preview-running", detail: "sensor:x" }, _);
    check("running without internal id", running.indexOf("sensor:") < 0, running);
})();

(function testNoteColor() {
    check("dark theme gets light green", Presenter.noteColor([225, 225, 225]) === "#7EE000");
    check("light theme gets dark green", Presenter.noteColor([48, 48, 48]) === "#3D6B00");
    check("unknown theme gets middle green", Presenter.noteColor(null) === "#5FA800");
})();

(function testParseCoreResult() {
    const ok = Presenter.parseCoreResult('{"cameras": [], "errors": []}', 0, "");
    check("ok result", ok.ok && ok.error === null, ok);

    const garbage = Presenter.parseCoreResult("not json", 0, "");
    check("garbage", !garbage.ok && garbage.error.code === "core-garbage", garbage);

    const crash = Presenter.parseCoreResult("", 1, "line1\nTraceback\nBoom");
    check("crash", !crash.ok && crash.error.code === "core-failed", crash);
    check("crash detail", crash.error.detail.indexOf("Boom") >= 0, crash);

    const [out, code] = fake("toolmissing", ["probe", "sensor:i2c-INT3479:00"]);
    const missing = Presenter.parseCoreResult(out, code, "");
    check("tool missing keeps data", missing.data !== null && missing.data.cameras.length === 2);
    check("tool missing error", missing.error.code === "libcamera-tools-missing", missing.error);
    check("tool missing text", Presenter.errorText(missing.error, _).indexOf("libcamera-tools") >= 0);
})();

(function testSummary() {
    const scan = fakeJson("latitude", ["scan"]);
    const text = Presenter.summaryText(scan, {}, _);
    check("summary names cameras", text.indexOf("Front camera") >= 0 && text.indexOf("Back camera") >= 0, text);
    check("summary empty", Presenter.summaryText(fakeJson("empty", ["scan"]), {}, _)
          .indexOf("No camera found.") >= 0);
})();

(function testUsageText() {
    const cams = fakeJson("mixed", ["scan"]).cameras;
    const front = cams[0].id, webcam = cams[2].id;
    check("no users, no text", Presenter.usageText([], cams, _) === null);
    check("own preview is not foreign", Presenter.usageText(
        [{ pid: 1, name: "qcam", cameras: [front], certain: true, own_preview: true }], cams, _) === null);
    const text = Presenter.usageText([
        { pid: 2, name: "firefox", cameras: [webcam], certain: true, own_preview: false },
        { pid: 3, name: null, cameras: [front, cams[1].id], certain: false, own_preview: false }
    ], cams, _);
    check("usage text", text === "HD Webcam (firefox), built-in camera (unknown program)", text);
    check("foreign users", Presenter.foreignUsers(
        [{ own_preview: true }, { own_preview: false }]).length === 1);
})();

// ---------------------------------------------------------------- coreClient

function runClient(prefix, args, timeout) {
    const loop = new GLib.MainLoop(null, false);
    let result = null;
    const client = new CoreClient.CoreClient(prefix);
    client.run(args, timeout, r => {
        result = r;
        loop.quit();
    });
    const guard = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 20, () => {
        loop.quit();
        return GLib.SOURCE_REMOVE;
    });
    if (result === null)
        loop.run();
    GLib.source_remove(guard);
    return [result, client];
}

const PY = GLib.find_program_in_path("python3");

(function testClientScan() {
    const [r] = runClient([PY, "-I", FAKE, "--scenario=latitude"], ["scan"], 10);
    check("client scan ok", r && r.ok && r.data.cameras.length === 2, r);
})();

(function testClientTimeout() {
    const start = GLib.get_monotonic_time();
    const [r, client] = runClient([PY, "-I", FAKE, "--scenario=slow"], ["scan"], 2);
    const seconds = (GLib.get_monotonic_time() - start) / 1e6;
    check("client timeout code", r && r.error && r.error.code === "core-timeout", r);
    check("client timeout fast", seconds < 6, seconds);
    check("client not busy after timeout", !client.busy);
})();

(function testClientGarbageAndCrash() {
    const [g] = runClient([PY, "-I", FAKE, "--scenario=garbage"], ["scan"], 10);
    check("client garbage", g && g.error.code === "core-garbage", g);
    const [c] = runClient([PY, "-I", FAKE, "--scenario=crash"], ["scan"], 10);
    check("client crash", c && c.error.code === "core-failed", c);
})();

(function testClientMissingCore() {
    const [r] = runClient(null, ["scan"], 10);
    check("no core found", r && r.error.code === "core-missing", r);
    const [r2] = runClient(["/nonexistent/avincecam-core"], ["scan"], 10);
    check("core not executable", r2 && r2.error.code === "core-missing", r2);
})();

(function testFindCoreInDevelopmentTree() {
    const prefix = CoreClient.findCore(APPLET);
    check("core found from applet folder", prefix !== null &&
          prefix[prefix.length - 1].endsWith("core/avincecam-core"), prefix);
})();

function withFakeRuntime(fn) {
    const dir = GLib.dir_make_tmp("avincecam-test-XXXXXX");
    GLib.setenv("AVINCECAM_FAKE_RUNTIME", dir, true);
    try {
        fn(dir);
    } finally {
        GLib.unsetenv("AVINCECAM_FAKE_RUNTIME");
        GLib.spawn_command_line_sync("rm -rf -- " + GLib.shell_quote(dir));
    }
}

(function testPreviewStartAndStop() {
    withFakeRuntime(() => {
        const loop = new GLib.MainLoop(null, false);
        const client = new CoreClient.CoreClient([PY, "-I", FAKE, "--scenario=latitude"]);
        let started = null, exited = null;
        client.startPreview(["preview", "sensor:i2c-INT3479:00"], 10,
            r => {
                started = r;
                client.run(["stop"], 10, () => {});
            },
            (status, signaled) => {
                exited = { status, signaled };
                loop.quit();
            });
        const guard = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 20, () => { loop.quit(); return false; });
        loop.run();
        GLib.source_remove(guard);
        check("preview started", started && !started.error && started.data.started, started);
        check("preview ended after stop", exited !== null && exited.status === 0, exited);
    });
})();

(function testPreviewViewerFailure() {
    withFakeRuntime(() => {
        GLib.setenv("FAKE_VIEWER_EXIT", "3", true);
        const loop = new GLib.MainLoop(null, false);
        const client = new CoreClient.CoreClient([PY, "-I", FAKE, "--scenario=latitude"]);
        let exited = null;
        client.startPreview(["preview", "sensor:i2c-INT3479:00"], 10, () => {},
            (status, signaled) => { exited = { status, signaled }; loop.quit(); });
        const guard = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 20, () => { loop.quit(); return false; });
        loop.run();
        GLib.source_remove(guard);
        GLib.unsetenv("FAKE_VIEWER_EXIT");
        check("failed viewer reports exit code", exited !== null && exited.status === 3, exited);
    });
})();

(function testDestroyDropsResult() {
    const loop = new GLib.MainLoop(null, false);
    let called = false;
    const client = new CoreClient.CoreClient([PY, "-I", FAKE, "--scenario=slow"]);
    client.run(["scan"], 30, () => { called = true; });
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 300, () => {
        client.destroy();
        return GLib.SOURCE_REMOVE;
    });
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1500, () => {
        loop.quit();
        return GLib.SOURCE_REMOVE;
    });
    loop.run();
    check("no callback after destroy", !called);
    check("not busy after destroy", !client.busy);
})();

(function testWatchFirstAnswerAndStop() {
    const loop = new GLib.MainLoop(null, false);
    const answers = [];
    let exited = false;
    const client = new CoreClient.CoreClient([PY, "-I", FAKE, "--scenario=mixed"]);
    const handle = client.watch(["watch"], r => { answers.push(r); loop.quit(); },
                                () => { exited = true; });
    const guard = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 20, () => { loop.quit(); return false; });
    loop.run();
    GLib.source_remove(guard);
    check("watch first answer", answers.length === 1 && answers[0].ok &&
          Array.isArray(answers[0].data.users) && answers[0].data.users.length === 0, answers);
    handle.stop();
    const loop2 = new GLib.MainLoop(null, false);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 500, () => { loop2.quit(); return false; });
    loop2.run();
    check("watch stopped, no exit callback", handle.stopped && !exited);
    check("watch process gone", handle.proc.get_identifier() === null);
})();

(function testSnapshotThroughFakeCore() {
    withFakeRuntime(dir => {
        const cams = fakeJson("mixed", ["scan"]).cameras;
        const [r] = runClient([PY, "-I", FAKE, "--scenario=mixed"],
                              ["snapshot", cams[0].id, "--size", "64x48"], 30);
        check("snapshot ok", r && r.ok && r.data.path.endsWith(".png"), r);
        check("snapshot stays in fake runtime", r && r.data.path.indexOf(dir) === 0, r && r.data.path);
        const [b] = runClient([PY, "-I", FAKE, "--scenario=mixed"],
                              ["snapshot", cams[1].id, "--size", "64x48"], 30);
        check("snapshot lens camera locked", b && b.error && b.error.code === "lens-blocked", b);
        const text = Presenter.errorText({ code: "cam-failed", message: null, detail: "x" }, _);
        check("snapshot error text", text.indexOf("The photo could not be taken.") === 0, text);
    });
})();

(function testWatchWithoutCore() {
    check("watch without core", new CoreClient.CoreClient(null).watch(["watch"], () => {}, () => {}) === null);
})();

print("applet tests: " + passed + " passed, " + failures + " failed");
if (failures > 0)
    imports.system.exit(1);
