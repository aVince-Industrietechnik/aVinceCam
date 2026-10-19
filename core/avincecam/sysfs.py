"""Quick camera detection from sysfs.

Only sysfs attributes and symlinks are read. No file below /dev is opened,
so no sensor, lens motor or USB camera is powered up by a scan.

Two kinds of cameras are recognised:

* USB video class (UVC) cameras: one entry per USB device, which usually owns
  two video nodes (capture and metadata).
* Sensors of complex camera pipelines (for example Intel IPU3): image sensors
  on the I2C bus that expose a V4L2 sub-device. They need libcamera.
"""

import os

from . import safety
from .model import Camera

DEFAULT_IPA_DIRS = ("/usr/share/libcamera/ipa", "/usr/local/share/libcamera/ipa")

# Drivers of camera receivers / image processors -> libcamera pipeline name.
PIPELINE_DRIVERS = {
    "ipu3-cio2": "ipu3",
    "ipu3-imgu": "ipu3",
    "intel-ipu6": "ipu6",
    "intel_ipu6_isys": "ipu6",
    "rkisp1": "rkisp1",
    "mxc-isi": "imx8-isi",
}

# Drivers that stand for the receiver of a pipeline (one per pipeline).
RECEIVER_DRIVERS = {"ipu3-cio2", "intel-ipu6", "intel_ipu6_isys", "rkisp1", "mxc-isi"}

LOCATION_LABELS = {"front": "Front camera", "back": "Back camera"}


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _real(path):
    try:
        return os.path.realpath(path, strict=True)
    except OSError:
        return None


def _link_base(path):
    real = _real(path)
    return os.path.basename(real) if real else None


def _node_sort_key(node):
    digits = "".join(ch for ch in node if ch.isdigit())
    return (node.rstrip("0123456789"), int(digits) if digits else 0)


def read_nodes(sysfs_root):
    """All V4L2 nodes with name, index, owning device, driver and bus."""
    base = os.path.join(sysfs_root, "class", "video4linux")
    try:
        names = sorted(os.listdir(base), key=_node_sort_key)
    except OSError:
        return []
    nodes = []
    for node in names:
        node_dir = os.path.join(base, node)
        nodes.append({
            "node": node,
            "dev": "/dev/" + node,
            "name": _read(os.path.join(node_dir, "name")),
            "index": _read(os.path.join(node_dir, "index")),
            "device": _real(os.path.join(node_dir, "device")),
            "driver": _link_base(os.path.join(node_dir, "device", "driver")),
            "bus": _link_base(os.path.join(node_dir, "device", "subsystem")),
        })
    return nodes


def read_media(sysfs_root):
    """Media controller devices with their model and owning device."""
    base = os.path.join(sysfs_root, "bus", "media", "devices")
    try:
        names = sorted(os.listdir(base), key=_node_sort_key)
    except OSError:
        return []
    media = []
    for name in names:
        path = _real(os.path.join(base, name))
        if not path:
            continue
        owner = os.path.dirname(path)
        media.append({
            "node": name,
            "dev": "/dev/" + name,
            "model": _read(os.path.join(path, "model")),
            "device": owner,
            "driver": _link_base(os.path.join(owner, "driver")),
        })
    return media


def _media_below(media, device):
    return [m["dev"] for m in media
            if m["device"] == device or m["device"].startswith(device + os.sep)]


def _uvc_cameras(nodes, media):
    groups = {}
    for node in nodes:
        if node["driver"] == "uvcvideo" and node["node"].startswith("video") \
                and node["device"]:
            groups.setdefault(os.path.dirname(node["device"]), []).append(node)

    cameras = []
    for usb_device, members in sorted(groups.items()):
        members.sort(key=lambda n: (n["index"] or "9", _node_sort_key(n["node"])))
        product = _read(os.path.join(usb_device, "product"))
        name = members[0]["name"] or ""
        removable = _read(os.path.join(usb_device, "removable"))
        kind = {"fixed": "integrated", "removable": "external"}.get(removable, "unknown")
        cameras.append(Camera(
            id="usb:" + os.path.basename(usb_device),
            label=product or name.split(":")[0].strip() or "USB camera",
            model=product,
            kind=kind,
            api="v4l2",
            pipeline="uvcvideo",
            location="external" if kind == "external" else None,
            nodes=[m["dev"] for m in members] + _media_below(media, usb_device),
            status="unknown",
            status_certainty="not checked",
        ))
    return cameras


def _lens_for(sensor_device, nodes):
    """Lens nodes whose device names the sensor as its I2C supplier."""
    sensor_name = os.path.basename(sensor_device)
    found = []
    for node in nodes:
        if node["driver"] not in safety.LENS_DRIVERS or not node["device"]:
            continue
        supplier = os.path.join(node["device"], "supplier:i2c:" + sensor_name)
        if os.path.lexists(supplier) or \
                os.path.basename(node["device"]).startswith(sensor_name + "-"):
            found.append(node["dev"])
    return found


def _has_tuning(pipeline, model, ipa_dirs):
    return any(os.path.isfile(os.path.join(d, pipeline, model + ".yaml"))
               for d in ipa_dirs)


def _sensor_cameras(nodes, media, ipa_dirs):
    receivers = {n["driver"] for n in nodes if n["driver"] in RECEIVER_DRIVERS}
    pipelines = sorted({PIPELINE_DRIVERS[d] for d in receivers})
    # Without opening the media graph the pipeline is only known if unique.
    pipeline = pipelines[0] if len(pipelines) == 1 else None
    receiver_media = [m["dev"] for m in media if m["driver"] in RECEIVER_DRIVERS]

    cameras = []
    for node in nodes:
        if not node["node"].startswith("v4l-subdev") or node["bus"] != "i2c":
            continue
        if node["driver"] in safety.LENS_DRIVERS or not node["device"]:
            continue
        model = node["driver"]
        panel = _read(os.path.join(node["device"], "physical_location", "panel"))
        location = panel.lower() if panel else None
        warnings = ["needs-libcamera"]
        if _lens_for(node["device"], nodes):
            warnings.append("lens-not-controlled")
        if pipeline is None:
            warnings.append("pipeline-unknown")
        elif model and not _has_tuning(pipeline, model, ipa_dirs):
            warnings.append("no-sensor-tuning")
        cameras.append(Camera(
            id="sensor:" + os.path.basename(node["device"]),
            label=LOCATION_LABELS.get(location, f"Camera {model or node['node']}"),
            model=model,
            kind="integrated",
            api="libcamera",
            pipeline=pipeline,
            location=location,
            nodes=receiver_media + [node["dev"]],
            status="unknown",
            status_certainty="not checked",
            warnings=warnings,
        ))
    return cameras


def _unclassified(nodes, cameras):
    used = {dev for cam in cameras for dev in cam.nodes}
    known = set(PIPELINE_DRIVERS) | safety.LENS_DRIVERS | {"uvcvideo"}
    return [{"dev": n["dev"], "name": n["name"], "driver": n["driver"]}
            for n in nodes if n["dev"] not in used and n["driver"] not in known]


def scan(sysfs_root="/sys", ipa_dirs=DEFAULT_IPA_DIRS):
    """Return (cameras, unclassified nodes). Opens no device."""
    nodes = read_nodes(sysfs_root)
    media = read_media(sysfs_root)
    cameras = _sensor_cameras(nodes, media, ipa_dirs) + _uvc_cameras(nodes, media)
    return cameras, _unclassified(nodes, cameras)
