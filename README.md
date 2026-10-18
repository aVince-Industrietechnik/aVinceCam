# aVinceCam

**Camera Control for Linux** – a lightweight Cinnamon applet that finds the cameras of a
computer, shows what they are and starts a matching preview.

> Status: early development (diagnostic phase). Nothing usable yet.

## Goals

- Detect cameras automatically: classic V4L2 devices (USB webcams) and libcamera-based
  cameras (for example Intel IPU3/IPU6 or other MIPI sensors)
- Show one readable entry per camera instead of a list of raw device nodes
- Start and stop a preview with an existing program (qcam, GStreamer)
- Show resolutions, frame rates and errors honestly – only what the system really reports
- No background service, no root rights, no changes to drivers or system configuration

## Design in short

```
Cinnamon applet (JavaScript, cjs)  →  avincecam core (Python, short-lived process, JSON)
                                       →  sysfs · libcamera · V4L2
                                       →  starts qcam / gst-launch-1.0 for the preview
```

The applet runs inside the Cinnamon process and therefore never touches hardware itself.
See [docs/architecture.md](docs/architecture.md).

## Requirements (planned)

- Linux Mint 22 / Cinnamon 6.x
- Python 3, python3-gi, GStreamer 1.x
- optional: libcamera tools and `gstreamer1.0-libcamera` for libcamera cameras

## License

GPL-3.0-or-later, see [LICENSE](LICENSE).
