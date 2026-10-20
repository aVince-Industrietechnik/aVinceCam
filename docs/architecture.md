# Architecture

## Two parts

**Applet** (`applet/`, JavaScript for Cinnamon's cjs)
- panel icon, menu, texts, camera selection
- runs inside the Cinnamon process: a crash or a blocking call there affects the whole
  desktop – so the applet never opens a device and calls the core only asynchronously
  (`Gio.Subprocess` with a timeout)

**Core** (`core/`, Python 3)
- a short-lived command line program, started on demand, answers in JSON
- commands: `scan`, `probe`, `status`, `preview <id>` (also as virtual camera),
  `stop`, `snapshot <id>`, `watch`, `diagnose`, `version` – see
  [json-interface.md](json-interface.md)
- no daemon, no D-Bus service, no root rights

Decisions and their reasons: [decisions.md](decisions.md).

## Sources of information

| Source | Device access | Used for |
|---|---|---|
| sysfs (`/sys/class/video4linux`, `/sys/bus/media`) | none | node list, drivers, USB IDs, camera type |
| libcamera (via `cam`, D9) | opens devices, no streaming | complex cameras (IPU3, IPU6, MIPI) |
| V4L2 ioctls | opens the node | USB webcams (UVC) |

Opening the applet menu uses sysfs only. libcamera is called on demand,
because enumerating may power up sensors and lens motors.

Camera use is watched with inotify on the device nodes and `/proc` (D13); no
device is opened for that.

## Safety rules built into the design

- **Lens/VCM sub-devices** (focus motors such as `dw9714`) are never opened.
  On some machines merely opening them triggers a failing power-up.
- **Cameras with a focus motor** are locked by default and, when allowed, run
  without libcamera's autofocus unless the user switches it on (D11, D16).
- **One preview at a time** in version 1. A lock file under `$XDG_RUNTIME_DIR/avincecam/`
  stores PID and process start time.
- **Only own processes are stopped.** PID and start time must match; nothing is killed by name.
- No settings or controls are written to camera devices in version 1.
- Features the system cannot report reliably are labelled as such, never guessed.

## Layout

```
core/avincecam-core      launcher (python3 -I core/avincecam-core ...)
core/avincecam/          Python package
  __main__.py            command line
  model.py               data classes
  safety.py              safety rules (never open sub-devices / lens motors)
  sysfs.py libcamera.py v4l2.py merge.py busy.py preview.py
  snapshot.py            photos (cam, last of ~30 frames, PNG)
  uvcphoto.py            photos of USB cameras (own video node, MJPEG/YUYV, D17)
  virtualcam.py          virtual camera through PipeWire
  watch.py               camera use (inotify, /proc)
  gstisolation.py        private GStreamer plugin set (D12)
  tuning.py              tuning files without autofocus (D16)
applet/avincecam@avince/ Cinnamon applet
  applet.js              menu and panel icon
  coreClient.js          asynchronous core calls with time limit
  presenter.js           texts for the menu (pure functions, tested with cjs)
assets/                  icon sources
scripts/diagnose/        read-only diagnostic helpers for development
scripts/check-public.py  checks that the repository contains nothing private
scripts/dev-link.sh      link the applet into Cinnamon for development
scripts/install.sh       install for the current user (uninstall.sh removes it)
tests/unit/              automatic tests, no hardware needed
tests/applet/            cjs tests of the applet modules
tests/fake-core/         stand-in core for applet tests (simulated cameras)
tests/run-tests.sh       runs all automatic tests
docs/
```
