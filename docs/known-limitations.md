# Known limitations

- **Image quality** depends on libcamera tuning files for the sensor. Without
  one, libcamera uses an uncalibrated default and pictures may look soft or
  have wrong colours. aVinceCam reports this but cannot fix it.
- **Autofocus** is not controlled by version 1. On some machines the lens
  motor is not powered correctly; aVinceCam never addresses lens motors.
- **Cameras with a focus motor:** libcamera drives the motor automatically
  during a preview. aVinceCam locks their preview by default (see D11) and,
  when allowed, runs them without libcamera's autofocus (D16): the focus then
  stays fixed. The setting "Use libcamera's autofocus" switches libcamera's
  autofocus back on – only for machines whose lens motor works.
- **Intel IPU3** can run only one camera pipeline at a time. aVinceCam allows
  one preview at a time.
- **Busy detection** sees only processes of the current user.
- **Frame rates** of libcamera cameras are limits reported by libcamera, not
  guaranteed values.
- **Querying one libcamera camera powers up all cameras** of the machine for a
  moment (libcamera starts all of them). aVinceCam only queries libcamera when
  the user asks for details.
- Sizes are those of libcamera's default (viewfinder) stream; the sensor may
  offer more in other stream roles.
- Browsers and video conferencing tools do not see libcamera-only cameras by
  themselves. aVinceCam's **virtual camera** offers them through PipeWire
  (D14): Firefox needs `media.webrtc.camera.allow-pipewire = true`, Chrome its
  PipeWire camera flag. Native apps that only use V4L2 (e.g. the Zoom desktop
  client) would need a loopback device, which aVinceCam does not install.
- While the virtual camera runs, **USB cameras cannot be used** by other
  programs (libcamera keeps them open).
- **Photos** are PNG files only (no JPEG without an extra library); each photo
  powers up the cameras once, like a query.
- **Camera use** of processes of other users is shown as "unknown program".
- **GStreamer probes all video devices** when its video4linux2 plugin loads.
  On machines with complex camera pipelines this briefly powers them up.
  aVinceCam keeps that plugin out of its libcamera pipelines and uses VLC for
  USB cameras when available; only the GStreamer fallback for USB cameras
  still loads it (D12).
