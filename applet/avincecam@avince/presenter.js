/*
 * aVinceCam – presentation logic.
 *
 * Pure functions without Cinnamon or GObject imports: they turn the JSON
 * answers of the core into texts for the menu. Kept separate so that they
 * can be tested with plain cjs (tests/applet/).
 *
 * Every function takes the translation function `_` as a parameter.
 */

const LOCATION_TITLES = {
    front: "Front camera",
    back: "Back camera"
};

const WARNING_TEXTS = {
    "needs-libcamera": "Needs libcamera – classic webcam programs cannot use this camera.",
    "lens-not-controlled": "Has a focus motor; aVinceCam does not control it.",
    "no-sensor-tuning": "No libcamera tuning file for this sensor – image quality may be reduced.",
    "pipeline-unknown": "The camera pipeline could not be determined.",
    "libcamera-no-match": "libcamera did not report this camera.",
    "location-mismatch": "sysfs and libcamera report different positions.",
    "cam-output-unparsed": "The output of the libcamera tool was not understood."
};

const ERROR_TEXTS = {
    "core-missing": "The aVinceCam core was not found.",
    "core-failed": "The aVinceCam core stopped with an error.",
    "core-timeout": "The aVinceCam core did not answer in time.",
    "core-garbage": "The answer of the aVinceCam core was not understood.",
    "unknown-camera": "This camera is no longer present.",
    "libcamera-tools-missing": "The libcamera tools (package libcamera-tools) are not installed.",
    "timeout": "The libcamera tool did not answer in time.",
    "hardware-blocked": "Hardware access is switched off.",
    "not-implemented": "This function is not available yet."
};

function upper(text) {
    return text ? String(text).toUpperCase() : "";
}

/* Title of a camera in the menu, e.g. "Front camera". */
function cameraTitle(cam, _) {
    if (cam.location && LOCATION_TITLES[cam.location])
        return _(LOCATION_TITLES[cam.location]);
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
    return WARNING_TEXTS[code] ? _(WARNING_TEXTS[code]) : code;
}

function errorText(error, _) {
    if (!error)
        return _("Unknown error");
    const known = ERROR_TEXTS[error.code];
    return known ? _(known) : (error.message || error.code || _("Unknown error"));
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
    return _("up to %s fps (according to libcamera)")
        .replace("%s", String(Math.round(limits.max)));
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
    cameraTitle, cameraSubtitle, warningText, errorText, formatsText,
    frameRateText, detailLines, parseCoreResult, summaryText
};
