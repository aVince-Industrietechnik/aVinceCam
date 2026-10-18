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
