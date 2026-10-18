# Architecture

## Two parts

**Applet** (`applet/`, JavaScript for Cinnamon's cjs)
- panel icon, menu, texts, camera selection
- runs inside the Cinnamon process: a crash or a blocking call there affects the whole
  desktop – so the applet never opens a device and calls the core only asynchronously
  (`Gio.Subprocess` with a timeout)

**Core** (`core/`, Python 3)
- a short-lived command line program, started on demand, answers in JSON
- planned commands: `list`, `info <id>`, `preview <id>`, `stop`, `diagnose`
- no daemon, no D-Bus service, no root rights

## Sources of information

| Source | Device access | Used for |
|---|---|---|
| sysfs (`/sys/class/video4linux`, `/sys/bus/media`) | none | node list, drivers, USB IDs, camera type |
| libcamera (via GStreamer device monitor or `cam`) | opens devices, no streaming | complex cameras (IPU3, IPU6, MIPI) |
| V4L2 ioctls | opens the node | USB webcams (UVC) |

Which libcamera access becomes the main path is decided by measurements in the
diagnostic phase (see `tools/diagnose/`).

## Safety rules built into the design

- **Lens/VCM sub-devices** (focus motors such as `dw9714`) are never opened.
  On some machines merely opening them triggers a failing power-up.
- **One preview at a time** in version 1. A lock file under `$XDG_RUNTIME_DIR/avincecam/`
  stores PID and process start time.
- **Only own processes are stopped.** PID and start time must match; nothing is killed by name.
- No settings or controls are written to camera devices in version 1.
- Features the system cannot report reliably are labelled as such, never guessed.

## Planned layout

```
core/avincecam/        Python package (CLI, model, sources, preview, diagnose)
applet/avincecam@avince/
tests/unit/            automatic tests, no hardware needed
tests/fixtures/        recorded sysfs / tool output
tests/manual/          manual test instructions
tools/diagnose/        read-only diagnostic helpers for development
docs/
```
