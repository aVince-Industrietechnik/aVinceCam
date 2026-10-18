# Changelog

## Unreleased

- Project skeleton, architecture notes and read-only diagnostic tools
- Core package skeleton with command line (`version`; other commands report
  "not implemented")
- Central safety rules: sub-devices and lens motors are never opened, V4L2
  queries only on UVC nodes – with unit tests on simulated sysfs trees
- Test runner, public-content check, decision log, JSON interface description
