# Changelog

## Unreleased

- Project skeleton, architecture notes and read-only diagnostic tools
- Core package skeleton with command line (`version`; other commands report
  "not implemented")
- Central safety rules: sub-devices and lens motors are never opened, V4L2
  queries only on UVC nodes – with unit tests on simulated sysfs trees
- Test runner, public-content check, decision log, JSON interface description
- `scan`: camera detection from sysfs only – UVC cameras and sensors of
  complex pipelines, front/back position, lens motor and missing tuning
  hints; simulated sysfs trees for tests
- Diagnose tool: media device driver is now read from the owning device
- `probe`: details of libcamera cameras from `cam` (sizes, frame rate limits,
  sensor size, libcamera id), matched to the sysfs cameras by model and
  position; stops calling `cam` as soon as all requested cameras are found
- Parser tests against recorded `cam` output of libcamera 0.2.0
- `AVINCECAM_NO_HARDWARE=1` blocks every camera tool call (set by the tests)
- Cinnamon applet: camera list with selection, details (queried only on
  request), notes, camera report to the clipboard; asynchronous core calls
  with time limits; no preview yet
- Fake core for applet tests and offline tests of the applet modules (cjs)
- `scripts/dev-link.sh` to link the applet for development
- Colourful aperture icon (default) and single-colour panel icon, icon size
  setting; `scripts/build-icons.py` builds them from `assets/icons`
- German translation; test that every applet text is translated
