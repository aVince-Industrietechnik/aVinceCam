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
| `stop` | none | stops the own preview only |
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

## Camera object

```json
{
  "id": "libcamera:\\_SB_.PCI0.LNK0",
  "label": "Front camera",
  "model": "ov5670",
  "kind": "integrated",
  "api": "libcamera",
  "pipeline": "ipu3",
  "location": "front",
  "nodes": ["/dev/media1"],
  "formats": null,
  "formats_source": "not probed",
  "status": "free",
  "status_certainty": "own-user-only",
  "warnings": ["no-sensor-tuning"]
}
```

Fields that cannot be determined reliably carry their source or certainty
(`formats_source`, `status_certainty`). Nothing is guessed.
