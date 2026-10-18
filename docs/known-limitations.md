# Known limitations

- **Image quality** depends on libcamera tuning files for the sensor. Without
  one, libcamera uses an uncalibrated default and pictures may look soft or
  have wrong colours. aVinceCam reports this but cannot fix it.
- **Autofocus** is not controlled by version 1. On some machines the lens
  motor is not powered correctly; aVinceCam never addresses lens motors.
- **Intel IPU3** can run only one camera pipeline at a time. aVinceCam allows
  one preview at a time.
- **Busy detection** sees only processes of the current user.
- **Frame rates** of libcamera cameras are limits reported by libcamera, not
  guaranteed values.
- Browsers and video conferencing tools may not see libcamera-only cameras;
  that needs PipeWire's libcamera support or a loopback device and is outside
  the scope of aVinceCam.
