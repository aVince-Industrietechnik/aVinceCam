# JSON interface between core and frontends

The core is called as `python3 -I <core-dir>/avincecam-core <command> --json`.
Isolated mode (`-I`) ignores `PYTHONPATH` and user site-packages; the launcher
adds only its own directory. The core prints exactly one JSON object on stdout.

## Commands

| Command | Device access | Purpose |
|---|---|---|
| `version` | none | version of the core |
| `scan` | none (sysfs only) | list of cameras with basic information |
| `probe [<id>]` | opens devices, no streaming | adds formats and frame rates |
| `status` | none | own preview, busy state |
| `preview <id>` | streams one camera | starts the viewer |
| `preview <id> --viewer virtual [--name NAME]` | streams one camera | offers it to other programs through PipeWire as "aVinceCam: NAME" (D14) |
| `stop` | none | stops the own preview only |
| `snapshot <id> [--size WxH] [--out DIR]` | streams ~1 s | saves a still photo: libcamera cameras as PNG (D15), USB cameras as JPEG (MJPEG) or PNG (YUYV) from their own video node only (D17) |

`preview` and `snapshot` refuse cameras with a focus motor (`lens-blocked`,
D11) unless `--allow-lens` is given; they then run without libcamera's
autofocus (D16). `--autofocus` (only together with `--allow-lens`) leaves
libcamera's own tuning file and autofocus in place.
| `watch` | none (inotify, /proc) | one line per change of camera use, until stdin closes (D13) |
| `diagnose` | like `probe` | text report |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | error (see `errors`) |
| 2 | usage error |
| 3 | command not implemented yet |

## Common fields

Every answer contains `schema` (interface version, currently 1) and
`command`. Errors are reported as a list:

```json
{"schema": 1, "command": "scan",
 "errors": [{"code": "not-implemented", "message": "...", "detail": null}]}
```

## `scan` answer

```json
{"schema": 1, "command": "scan", "source": "sysfs", "opens_devices": false,
 "cameras": [ ... ], "unclassified": [ ... ], "errors": []}
```

`unclassified` lists video nodes of unknown drivers (for example virtual
devices) so that they are visible in diagnostics instead of silently hidden.

Camera ids: `sensor:<i2c device>` for sensors of complex pipelines,
`usb:<usb port>` for UVC cameras.

Warnings: `needs-libcamera`, `lens-not-controlled`, `no-sensor-tuning`,
`pipeline-unknown`.

## `probe` answer

```json
{"schema": 1, "command": "probe", "source": "sysfs+libcamera",
 "opens_devices": true, "libcamera_calls": 2,
 "cameras": [ ... ], "notes": [], "errors": []}
```

`probe [<id>]` starts from the sysfs scan and adds details:

- libcamera cameras: `cam -c N -I -p --list-controls`, one call per camera,
  stopping as soon as all requested cameras are matched. `libcamera_calls`
  says how often libcamera was started (each start powers up the cameras).
- UVC cameras: read-only V4L2 enumeration (`VIDIOC_QUERYCAP`, `ENUM_FMT`,
  `ENUM_FRAMESIZES`, `ENUM_FRAMEINTERVALS`) of the capture node; no format
  is set and nothing streams. `formats[].fps` lists the offered frame rates.

Additional camera fields after `probe`: `backend_id` (libcamera camera id),
`sensor_size` (`[width, height]`), `frame_rate_limits`
(`{"min", "max", "source"}`), `formats` (default stream sizes).

Matching rule: sensor model and position; if that is not unique, model alone.
Without a unique match nothing is merged (`libcamera-no-match`). A position
that differs between sysfs and libcamera is reported (`location-mismatch`).

Error codes: `unknown-camera`, `libcamera-tools-missing`, `timeout`, `v4l2-failed`,
`hardware-blocked` (environment variable `AVINCECAM_NO_HARDWARE=1`, set by
the test suite). Note `cam-output-unparsed`: output of `cam` did not match the
known format.

## Camera object

```json
{
  "id": "sensor:i2c-INT3479:00",
  "label": "Front camera",
  "model": "ov5670",
  "kind": "integrated",
  "api": "libcamera",
  "pipeline": "ipu3",
  "location": "front",
  "nodes": ["/dev/media1", "/dev/v4l-subdev6"],
  "formats": null,
  "formats_source": "not probed",
  "status": "free",
  "status_certainty": "own-user-only",
  "warnings": ["no-sensor-tuning"]
}
```

Fields that cannot be determined reliably carry their source or certainty
(`formats_source`, `status_certainty`). Nothing is guessed.
