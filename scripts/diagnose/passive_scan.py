#!/usr/bin/env python3
"""List camera-related device nodes from sysfs only.

This script never opens anything below /dev. It reads sysfs attributes and
symlinks, which does not power up a camera sensor or a focus motor.

Usage: passive_scan.py [--pretty]
"""

import json
import os
import sys

V4L_CLASS = "/sys/class/video4linux"
MEDIA_BUS = "/sys/bus/media/devices"

# Drivers of lens / voice coil motor (VCM) chips. Their sub-devices must never
# be opened by aVinceCam: on some machines the power-up fails and floods the
# kernel log with I2C errors.
LENS_DRIVERS = {
    "ad5820", "ak7375", "dw9714", "dw9719", "dw9768", "dw9807-vcm",
    "lc898217xc",
}


def read_attr(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return None


def link_name(path):
    """Return the last element of a symlink target, or None."""
    try:
        return os.path.basename(os.path.realpath(path, strict=True))
    except (OSError, TypeError):
        return None


def usb_ids(device_path):
    """Walk up from a device to the USB device that owns it."""
    path = os.path.realpath(device_path)
    while path and path != "/":
        vendor = read_attr(os.path.join(path, "idVendor"))
        if vendor:
            return {
                "vendor_id": vendor,
                "product_id": read_attr(os.path.join(path, "idProduct")),
                "manufacturer": read_attr(os.path.join(path, "manufacturer")),
                "product": read_attr(os.path.join(path, "product")),
            }
        path = os.path.dirname(path)
    return None


def node_kind(node):
    if node.startswith("v4l-subdev"):
        return "subdev"
    if node.startswith("video"):
        return "video"
    return "other"


def scan_v4l():
    nodes = []
    if not os.path.isdir(V4L_CLASS):
        return nodes
    for node in sorted(os.listdir(V4L_CLASS)):
        base = os.path.join(V4L_CLASS, node)
        device = os.path.join(base, "device")
        name = read_attr(os.path.join(base, "name"))
        driver = link_name(os.path.join(device, "driver"))
        bus = link_name(os.path.join(device, "subsystem"))
        is_lens = (driver in LENS_DRIVERS) or ("vcm" in (name or "").lower())
        entry = {
            "node": node,
            "dev": "/dev/" + node,
            "kind": node_kind(node),
            "name": name,
            "driver": driver,
            "bus": bus,
            "index": read_attr(os.path.join(base, "index")),
            "lens_never_open": is_lens,
        }
        if bus == "usb":
            entry["usb"] = usb_ids(device)
        nodes.append(entry)
    return nodes


def scan_media():
    devices = []
    if not os.path.isdir(MEDIA_BUS):
        return devices
    for media in sorted(os.listdir(MEDIA_BUS)):
        base = os.path.join(MEDIA_BUS, media)
        # Media devices have no "device" link; the owner is the parent directory.
        owner = os.path.dirname(os.path.realpath(base))
        devices.append({
            "node": media,
            "dev": "/dev/" + media,
            "model": read_attr(os.path.join(base, "model")),
            "driver": link_name(os.path.join(owner, "driver")),
        })
    return devices


def summarize(nodes, media):
    drivers = sorted({n["driver"] for n in nodes if n["driver"]})
    complex_drivers = {"ipu3-cio2", "ipu3-imgu", "intel-ipu6", "intel_ipu6_isys",
                       "rkisp1", "mxc-isi"}
    return {
        "video_nodes": sum(1 for n in nodes if n["kind"] == "video"),
        "subdev_nodes": sum(1 for n in nodes if n["kind"] == "subdev"),
        "media_devices": len(media),
        "drivers": drivers,
        "uvc_nodes": [n["dev"] for n in nodes if n["driver"] == "uvcvideo"],
        "complex_camera_pipeline": sorted(set(drivers) & complex_drivers),
        "lens_nodes_never_open": [n["dev"] for n in nodes if n["lens_never_open"]],
    }


def main(argv):
    nodes = scan_v4l()
    media = scan_media()
    result = {
        "tool": "passive_scan",
        "opens_devices": False,
        "summary": summarize(nodes, media),
        "media": media,
        "v4l": nodes,
    }
    indent = 2 if "--pretty" in argv else None
    json.dump(result, sys.stdout, indent=indent, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
