/*
 * aVinceCam – presentation logic.
 *
 * Pure functions without Cinnamon or GObject imports: they turn the JSON
 * answers of the core into texts for the menu. Kept separate so that they
 * can be tested with plain cjs (tests/applet/).
 *
 * Every function takes the translation function `_` as a parameter.
 */

/*
 * The text tables are functions so that every string appears literally in a
 * _("...") call – the translation tool (xgettext) only finds those.
 */
function locationTitles(_) {
    return {
        front: _("Front camera"),
        back: _("Back camera")
    };
}

function warningTexts(_) {
    return {
        "needs-libcamera": _("Works only with libcamera, not with classic webcam programs."),
        "lens-not-controlled": _("Has a focus motor; libcamera drives it during a preview."),
        "no-sensor-tuning": _("No libcamera tuning file – image quality may suffer."),
        "pipeline-unknown": _("The camera pipeline could not be determined."),
        "libcamera-no-match": _("libcamera did not report this camera."),
        "location-mismatch": _("sysfs and libcamera report different positions."),
        "cam-output-unparsed": _("The output of the libcamera tool was not understood.")
    };
}

function errorTexts(_) {
    return {
        "core-missing": _("The aVinceCam core was not found."),
        "core-failed": _("The aVinceCam core stopped with an error."),
        "core-timeout": _("The aVinceCam core did not answer in time."),
        "core-garbage": _("The answer of the aVinceCam core was not understood."),
        "unknown-camera": _("This camera is no longer present."),
        "libcamera-tools-missing": _("The libcamera tools (package libcamera-tools) are not installed."),
        "timeout": _("The libcamera tool did not answer in time."),
        "hardware-blocked": _("Hardware access is switched off."),
        "not-implemented": _("This function is not available yet."),
        "preview-running": _("A preview is already running – stop it first."),
        "camera-busy": _("The camera is in use by another program."),
        "viewer-missing": _("The preview program is not installed."),
        "viewer-failed": _("The preview ended with an error."),
        "no-camera-id": _("The libcamera id of this camera is unknown."),
        "no-runtime-dir": _("No runtime folder for the preview is available."),
        "stop-failed": _("The preview could not be stopped."),
        "lens-blocked": _("Preview locked: camera has a focus motor")
    };
}

/* Errors whose detail is worth showing next to the text. */
const DETAIL_ERRORS = ["camera-busy", "viewer-missing", "viewer-failed"];

function upper(text) {
    return text ? String(text).toUpperCase() : "";
}

/* Title of a camera in the menu, e.g. "Front camera". */
function cameraTitle(cam, _) {
    const titles = locationTitles(_);
    if (cam.location && titles[cam.location])
        return titles[cam.location];
    return cam.label || _("Camera");
}

/* Interface line, e.g. "OV5670 · libcamera (IPU3)" or "USB (UVC) · external". */
function cameraSubtitle(cam, _) {
    const parts = [];
    if (cam.api === "libcamera") {
        if (cam.model)
            parts.push(upper(cam.model));
        parts.push(cam.pipeline ? "libcamera (" + upper(cam.pipeline) + ")" : "libcamera");
    } else if (cam.api === "v4l2") {
        parts.push(_("USB (UVC)"));
        if (cam.kind === "external")
            parts.push(_("external"));
        else if (cam.kind === "integrated")
            parts.push(_("built in"));
    }
    return parts.join(" · ");
}

function warningText(code, _) {
    return warningTexts(_)[code] || code;
}

function errorText(error, _) {
    if (!error)
        return _("Unknown error");
    const text = errorTexts(_)[error.code] || error.message || error.code || _("Unknown error");
    if (error.detail && DETAIL_ERRORS.indexOf(error.code) >= 0)
        return text + " (" + String(error.detail).slice(0, 80) + ")";
    return text;
}

/*
 * Colour for notes: a light green (#7EE000), in a shade that stays
 * readable on the current theme.
 * `fg` is the menu's text colour [r, g, b]; light text means a dark menu.
 * Both greens reach a contrast of at least 5.8:1 on typical menu backgrounds.
 */
function noteColor(fg) {
    if (!fg || fg.length < 3)
        return "#5FA800";
    const brightness = (0.299 * fg[0] + 0.587 * fg[1] + 0.114 * fg[2]) / 255;
    return brightness > 0.5 ? "#7EE000" : "#3D6B00";
}

function sizeText(w, h) {
    return w + "×" + h;
}

function formatsText(cam, _) {
    const formats = cam.formats || [];
    if (formats.length === 0)
        return null;
    let smallest = formats[0];
    let largest = formats[0];
    for (const f of formats) {
        if (f.width * f.height < smallest.width * smallest.height)
            smallest = f;
        if (f.width * f.height > largest.width * largest.height)
            largest = f;
    }
    const pixelFormats = [...new Set(formats.map(f => f.pixel_format))].join(", ");
    return _("%d sizes, %s to %s (%s)")
        .replace("%d", String(formats.length))
        .replace("%s", sizeText(smallest.width, smallest.height))
        .replace("%s", sizeText(largest.width, largest.height))
        .replace("%s", pixelFormats);
}

function frameRateText(cam, _) {
    const limits = cam.frame_rate_limits;
    if (!limits || !limits.max)
        return null;
    const max = String(Math.round(limits.max));
    if (limits.source && limits.source.indexOf("V4L2") === 0)
        return _("up to %s fps (reported by the camera)").replace("%s", max);
    return _("up to %s fps (according to libcamera)").replace("%s", max);
}

/*
 * Detail lines for a camera. Only what the core reported; unknown values are
 * named as unknown instead of being left out silently.
 */
function detailLines(cam, _) {
    const lines = [];
    if (cam.sensor_size)
        lines.push(_("Sensor: %s").replace("%s", sizeText(cam.sensor_size[0], cam.sensor_size[1])));
    const formats = formatsText(cam, _);
    lines.push(formats ? _("Picture: %s").replace("%s", formats)
                       : _("Picture sizes: not queried"));
    const rates = frameRateText(cam, _);
    if (rates)
        lines.push(_("Frame rate: %s").replace("%s", rates));
    else if (cam.formats)
        lines.push(_("Frame rate: unknown"));
    if (cam.backend_id)
        lines.push(_("libcamera id: %s").replace("%s", cam.backend_id));
    if (cam.nodes && cam.nodes.length)
        lines.push(_("Devices: %s").replace("%s", cam.nodes.join(", ")));
    return lines;
}

/*
 * Interpret the result of a core call.
 * Returns { ok, data, error } – error is { code, message, detail }.
 */
function parseCoreResult(stdout, exitCode, stderr) {
    let data = null;
    const text = (stdout || "").trim();
    if (text) {
        try {
            data = JSON.parse(text);
        } catch (e) {
            data = null;
        }
    }
    if (data === null || typeof data !== "object") {
        if (exitCode !== 0)
            return { ok: false, data: null,
                     error: { code: "core-failed", message: null,
                              detail: (stderr || "").trim().split("\n").slice(-3).join("\n") } };
        return { ok: false, data: null,
                 error: { code: "core-garbage", message: null, detail: text.slice(0, 200) } };
    }
    const errors = Array.isArray(data.errors) ? data.errors : [];
    if (exitCode !== 0 || errors.length > 0)
        return { ok: exitCode === 0, data: data,
                 error: errors[0] || { code: "core-failed", message: null, detail: null } };
    return { ok: true, data: data, error: null };
}

/* Plain text summary for the clipboard (diagnostics). */
function summaryText(scan, details, _) {
    const lines = ["aVinceCam"];
    const cameras = (scan && scan.cameras) || [];
    if (cameras.length === 0)
        lines.push(_("No camera found."));
    for (const cam of cameras) {
        const full = (details && details[cam.id]) || cam;
        lines.push("");
        lines.push(cameraTitle(full, _) + " – " + cameraSubtitle(full, _));
        lines.push("  id: " + full.id);
        for (const line of detailLines(full, _))
            lines.push("  " + line);
        for (const w of full.warnings || [])
            lines.push("  ! " + warningText(w, _));
    }
    const other = (scan && scan.unclassified) || [];
    for (const node of other)
        lines.push(_("Unclassified device: %s").replace("%s",
            node.dev + " (" + (node.driver || "?") + ")"));
    return lines.join("\n");
}

/* Exported for applet and tests (cjs legacy imports use top-level names). */
var Presenter = {
    cameraTitle, cameraSubtitle, warningText, errorText, noteColor, formatsText,
    frameRateText, detailLines, parseCoreResult, summaryText
};
