# Changelog

## Unreleased

- Photos of USB cameras: the core streams about a second from the webcam's
  own video node (MJPEG, else YUYV) and saves the last frame as JPEG – with
  the standard Huffman tables added when the camera leaves them out – or as
  PNG (D17); "Take photo" is offered for USB cameras too

## 0.9.0 – 2026-10-20

First public version.

### Cameras and details
- Camera detection from sysfs only: USB cameras (UVC) and sensors of complex
  pipelines (Intel IPU3/IPU6, MIPI), front/back position, lens motor and
  missing tuning hints; nothing is opened when the menu opens
- Details on request: libcamera cameras through `cam` (sizes, frame rate
  limits, sensor size, libcamera id; probed by id), USB cameras through
  read-only V4L2 enumeration ioctls
- Camera report to the clipboard

### Preview, photo, virtual camera
- Preview with `qcam` or GStreamer (libcamera) and VLC or GStreamer (USB), with
  lock file, busy check and own-process-only stop; the applet removes the lock
  of a preview whose window was closed
- GStreamer pipelines for libcamera cameras run with a private plugin set, so
  GStreamer's video4linux2 plugin never opens every camera node
- Photos of libcamera cameras: about 30 frames until the exposure has settled,
  the last one saved as PNG (default 1600 × 1200), folder and size settings
- Virtual camera: a libcamera camera offered through PipeWire for browsers and
  web video calls, with hints for Firefox and Chrome
- Camera in use: inotify on the device nodes plus `/proc`, red dot on the panel
  icon and a line in the menu; aVinceCam's own use is recognised

### Cameras with a focus motor
- Locked by default (setting to allow)
- When allowed they run without libcamera's autofocus: a copy of the tuning
  file without the `Af` algorithm is used only by the programs aVinceCam starts
- Setting "Use libcamera's autofocus" for machines whose focus motor works;
  core option `--autofocus` for `preview` and `snapshot`

### Applet
- Colourful aperture icon (default) or single-colour panel icon, icon size
  setting; the icon turns slowly or pulses while the own preview runs
- Notes in the menu in light green; English and German texts

### Installation and development
- `scripts/install.sh` / `scripts/uninstall.sh`: user-level installation with
  backup and an exact list of installed paths; `--purge` removes the settings
- Automatic tests without hardware (simulated sysfs trees, recorded `cam`
  output, fake core and fake viewers); `AVINCECAM_NO_HARDWARE=1` blocks every
  camera tool call; full output of each run in `~/.cache/avincecam-tests/`
- `scripts/check-public.py` checks that the repository contains nothing
  private; read-only diagnostic helpers in `scripts/diagnose/`
