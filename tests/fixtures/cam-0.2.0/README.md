# Recorded `cam` output – libcamera 0.2.0

Standard output of the libcamera `cam` tool on an Intel IPU3 machine with an
OV5670 front sensor and an OV8858 back sensor. libcamera log lines (stderr)
are removed.

| File | Command |
|---|---|
| `list.txt` | `cam --list` |
| `info-1.txt` | `cam -c 1 -I -p --list-controls` |
| `info-2.txt` | `cam -c 2 -I -p --list-controls` |

Used by the parser tests and by `probe --tool-output`. No device is touched.
