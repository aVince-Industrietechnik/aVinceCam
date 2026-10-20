# Architecture decisions

Short records of decisions and why they were made. Newest last.

## D1 – Core and applet are separate processes
The Cinnamon applet runs inside the Cinnamon process. A blocking call or a
crash there affects the whole desktop. All device access therefore happens in
a separate, short-lived Python process (the core). The applet only starts it
asynchronously and reads JSON.

## D2 – No background service, no D-Bus in version 1
The core runs only for the duration of a request. A D-Bus service would need a
permanently running process and adds complexity without a benefit for v1.

## D3 – Quick detection from sysfs, libcamera only on demand
Measured on an Intel IPU3 laptop: every libcamera enumeration powers up the
sensors and the lens motor, which produces kernel I2C error messages on that
machine. Opening the menu therefore uses sysfs only (no device is opened);
libcamera is called when the user asks for details or a refresh, and the
result is cached.

## D4 – Sub-devices and lens motors are never opened by aVinceCam
Rules live in `core/avincecam/safety.py` and have their own tests. V4L2
queries are only allowed on UVC video nodes; raw nodes of complex pipelines
are left to libcamera.

## D5 – Preview through existing programs
The preview uses `qcam` (libcamera) or `gst-launch-1.0` (UVC). The core
writes a lock file and then replaces itself with the viewer (`exec`), so the
applet holds the viewer process directly and can stop exactly that process.

## D6 – Python standard library only
The core needs no additional packages. libcamera information comes from the
installed tools; which one (`cam` or the GStreamer device monitor) is decided
after measurements.

## D7 – License GPL-3.0-or-later
Compatible with Cinnamon and Cinnamon Spices.

## D8 – What sysfs alone can tell
Without opening any device, sysfs provides:
- image sensors of complex pipelines (I2C devices with a V4L2 sub-device),
  their driver (= sensor model) and, via `physical_location/panel` (ACPI
  `_PLD`), whether a sensor faces front or back;
- lens motors and the sensor they belong to (`supplier:i2c:<sensor>` link);
- USB cameras grouped by USB device, with product name and `removable`
  (`fixed` = built in, `removable` = external);
- whether a libcamera tuning file exists for a sensor.

Not available from sysfs: which receiver port a sensor is wired to when a
machine has several pipelines, formats, frame rates and the busy state. These
fields stay `unknown` / `not probed` after a scan; nothing is guessed.

## D9 – Details of libcamera cameras come from `cam`
Measured on an Intel IPU3 machine with libcamera 0.2.0, each command run once:

| Source | Sizes | Frame rates | Position, model | Sensor power-ups |
|---|---|---|---|---|
| `cam --list` | – | – | – | 1 |
| `gst-device-monitor-1.0 Video/Source` | yes | **no** | – | **3** |
| `cam -c N -I -p --list-controls` | yes | yes (`FrameDurationLimits`) | yes | 1 |

`probe` therefore uses `cam -c N -I -p --list-controls`, one camera per call.
The text output of `cam` is not a stable interface: the parser is kept in
one module and tested against recorded output of each supported version.
Unknown output leads to `formats_source: "unparsed"`, never to invented values.

Observed side effect: libcamera's camera manager starts every camera of the
machine, so querying one camera also powers up the others. On machines where a
power-up produces kernel errors this happens with every libcamera program;
aVinceCam limits it to explicit user requests (D3).

The GStreamer device monitor is not used for probing, but its pipeline form
`libcamerasrc camera-name="<id>"` is a candidate for the preview.

Frame rates shown are the limits libcamera reports for the default
(viewfinder) stream, not measured values.

## D10 – Preview: lock file, exec and own-process-only stop
`preview <id>` checks everything first (camera known, no aVinceCam preview
running, no visible process holding any node of the camera's pipeline,
viewer installed), then writes `$XDG_RUNTIME_DIR/avincecam/preview.json`
with PID and process start time, prints one JSON line and replaces itself
with the viewer (`exec`). The PID therefore stays the viewer's PID.

`stop` signals only a process whose PID **and** start time match the lock
(a reused PID never matches); a zombie counts as ended. After 5 seconds a
hanging viewer of our own is killed.

On Intel IPU3 the busy check covers all nodes of the pipeline, so a running
back camera also blocks the front camera.

The libcamera camera id comes from sysfs (`firmware_node/path`, the ACPI
path), so starting a preview needs no extra libcamera query.

## D11 – Preview of cameras with a focus motor is locked by default
Measured on an Intel IPU3 laptop (libcamera 0.2.0): while a camera with a
lens motor streams, libcamera's built-in automatic focus writes the lens
position about 48 times a second. It cannot be switched off in that version.
On a machine whose focus motor is not powered correctly every write fails
and floods the kernel log. `preview` therefore refuses cameras with a focus
motor (`lens-blocked`) unless `--allow-lens` is given; the applet passes it
only when the user enables "Allow preview of cameras with a focus motor".

## D12 – USB camera preview with VLC, GStreamer only as fallback
Measured on an Intel IPU3 laptop with inotify and /proc: when `gst-launch-1.0`
loads GStreamer's video4linux2 plugin, it opens **every** video and
sub-device node of the machine for a moment – including the IPU3 nodes and
the lens motor – which powers up the IPU3 pipeline. (Rebuilding the
GStreamer registry, e.g. by `gst-inspect-1.0`, also loads the libcamera
plugin and starts libcamera.)

VLC (`vlc --intf qt --qt-minimal-view --play-and-exit v4l2://<device>`) opened
only the USB camera's node, and closing its window ended VLC at once. The
plain video window of `cvlc` did not end VLC when closed and is not used.
UVC previews therefore use VLC when installed; GStreamer remains the
fallback. VLC creates its own settings below `~/.config/vlc/`.

Addendum (2026-10-20): processes that only need libcamera – the GStreamer
preview of a libcamera camera and the virtual camera (D14) – run with a
private plugin folder and registry in the runtime folder (`gstisolation`),
so the video4linux2 plugin is never loaded. The GStreamer fallback for USB
cameras needs `v4l2src` from that very plugin and cannot be isolated.

## D13 – Camera use is watched with inotify, never by opening a device
The core command `watch` adds inotify watches to the camera device nodes
(adding a watch only looks up the path; the device is not opened) and reads
the holders from /proc after an open or close. Opens shorter than 0.5 s
(udev, PipeWire enumerating devices) are not reported. The lens motor node is
not watched. aVinceCam's own preview, virtual camera and photo – including
child processes such as `cam` – count as own use. Measured on an Intel IPU3
laptop: 17 MB, no CPU while idle; a program opening the webcam is shown
after about 1 s.

## D14 – Virtual camera through PipeWire, no system change
Browsers and web video calls find cameras through PipeWire; IPU3 cameras only
work through libcamera. aVinceCam runs `libcamerasrc ! videoconvert !
pipewiresink mode=provide` while the user switches it on, so the camera
appears as "aVinceCam: <name>". No package, PipeWire or WirePlumber setting
is changed. GStreamer sees only the four plugins it needs (private plugin
folder and registry), so the video4linux2 plugin (D12) is never loaded.
Measured: Firefox 158 shows the front camera at 1280x720, ~7 % of one CPU
core. Firefox needs `media.webrtc.camera.allow-pipewire = true` and a
restart. libcamera keeps all cameras it knows open while it runs, including
USB cameras, which other programs then cannot use; libcamera 0.2.0 offers no
way to exclude them.

## D15 – Photos: about 30 frames, keep the last one
Measured on an Intel IPU3 laptop: with 10 frames the exposure is still
settling (analogue gain jumping between 1x and 33x) and the ImgU's temporal
noise reduction smears the picture; after about 30 frames it is stable. The
core runs `cam` for 30 frames at 1600x1200 (the camera is soft at full size,
so this keeps its detail), keeps the last frame and writes a PNG with the
standard library. Frames go to the runtime folder (tmpfs) and are deleted at
once. Cameras with a focus motor stay locked as for the preview (D11).

## D16 – Cameras with a lens motor run without libcamera's autofocus
libcamera's IPU3 autofocus sweeps the lens on every stream and ignores the
AfMode control (`auto-focus-mode=AfModeManual` of libcamerasrc is already
its default and changes nothing). On a machine whose lens motor does not
answer, that means dozens of failed I2C writes per second (desk test T5-2).
The IPA sends the focus position with every frame, but the kernel only calls
the driver when the value changes. Without the autofocus algorithm the value
stays constant and nothing is written while streaming.

For every libcamera camera with a lens motor, aVinceCam writes a copy of the
tuning file libcamera would load (`<sensor>.yaml`, else `uncalibrated.yaml`)
without the `Af` entry to its runtime folder and sets
`LIBCAMERA_IPA_CONFIG_PATH` for the processes it starts (preview, virtual
camera, photo). The system's files are not changed and any calibration in
them is kept. The lens stays where it rests; the focus is fixed. Whether the
lock of D11 can be relaxed is decided after a desk test.
