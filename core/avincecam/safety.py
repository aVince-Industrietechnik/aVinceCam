"""Central safety rules of aVinceCam.

Every module that is about to open a device node asks this module first.
The rules are deliberately strict:

* Sub-devices (/dev/v4l-subdev*) are never opened by aVinceCam. They belong to
  sensors and lens motors; libcamera handles them itself.
* Lens / voice coil motor (VCM) chips are recognised so that diagnostics can
  name them, but there is no code path that opens them.
* Plain V4L2 queries are only allowed on USB video class (UVC) nodes. Raw
  nodes of complex camera pipelines (for example Intel IPU3) are left to
  libcamera.
"""

import os

DEFAULT_SYSFS = "/sys"

# Drivers of lens / VCM chips found in mainline Linux.
LENS_DRIVERS = frozenset({
    "ad5820", "ak7375", "dw9714", "dw9719", "dw9768", "dw9807-vcm",
    "lc898217xc",
})

# Drivers whose video nodes may be queried with V4L2 ioctls.
V4L2_QUERY_DRIVERS = frozenset({"uvcvideo"})


class SafetyError(Exception):
    """Raised when an action would break a safety rule."""


def _node_name(dev_path):
    return os.path.basename(dev_path.rstrip("/"))


def _sysfs_node_dir(node, sysfs_root):
    return os.path.join(sysfs_root, "class", "video4linux", node)


def node_driver(dev_path, sysfs_root=DEFAULT_SYSFS):
    """Return the kernel driver bound to a V4L2 node, or None.

    Reads only a sysfs symlink; the device itself is not opened.
    """
    link = os.path.join(_sysfs_node_dir(_node_name(dev_path), sysfs_root),
                        "device", "driver")
    try:
        return os.path.basename(os.path.realpath(link, strict=True))
    except OSError:
        return None


def node_name_attr(dev_path, sysfs_root=DEFAULT_SYSFS):
    path = os.path.join(_sysfs_node_dir(_node_name(dev_path), sysfs_root), "name")
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return None


def is_subdev(dev_path):
    return _node_name(dev_path).startswith("v4l-subdev")


def is_lens_node(dev_path, sysfs_root=DEFAULT_SYSFS):
    """True if the node belongs to a lens / VCM chip."""
    if node_driver(dev_path, sysfs_root) in LENS_DRIVERS:
        return True
    name = (node_name_attr(dev_path, sysfs_root) or "").lower()
    return "vcm" in name.split() or name.endswith("-vcm")


def check_v4l2_query(dev_path, sysfs_root=DEFAULT_SYSFS):
    """Raise SafetyError unless dev_path may be opened for V4L2 queries.

    Allowed: /dev/videoN nodes driven by a UVC driver. Everything else is
    refused, including every sub-device and every lens node.
    """
    if is_subdev(dev_path):
        raise SafetyError(f"sub-devices are never opened: {dev_path}")
    if is_lens_node(dev_path, sysfs_root):
        raise SafetyError(f"lens/VCM nodes are never opened: {dev_path}")
    if not _node_name(dev_path).startswith("video"):
        raise SafetyError(f"not a video node: {dev_path}")
    driver = node_driver(dev_path, sysfs_root)
    if driver not in V4L2_QUERY_DRIVERS:
        raise SafetyError(
            f"V4L2 queries only on UVC nodes, {dev_path} uses {driver!r}")
