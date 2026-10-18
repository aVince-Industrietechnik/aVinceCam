"""Build small fake sysfs trees for tests (no real hardware involved)."""

import os


def add_v4l_node(root, node, name, driver, bus="i2c"):
    """Create /class/video4linux/<node> with a device and driver link."""
    devices = os.path.join(root, "devices", node + "-dev")
    drivers = os.path.join(root, "bus", bus, "drivers", driver)
    os.makedirs(devices, exist_ok=True)
    os.makedirs(drivers, exist_ok=True)
    os.symlink(drivers, os.path.join(devices, "driver"))

    node_dir = os.path.join(root, "class", "video4linux", node)
    os.makedirs(node_dir, exist_ok=True)
    with open(os.path.join(node_dir, "name"), "w", encoding="utf-8") as handle:
        handle.write(name + "\n")
    os.symlink(devices, os.path.join(node_dir, "device"))


def latitude_5285(root):
    """Node set of an Intel IPU3 2-in-1 with OV5670, OV8858 and DW9714."""
    add_v4l_node(root, "video10", "ipu3-cio2 0", "ipu3-cio2", bus="pci")
    add_v4l_node(root, "video3", "ipu3-imgu 0 viewfinder", "ipu3-imgu", bus="pci")
    add_v4l_node(root, "v4l-subdev6", "ov5670 3-0036", "ov5670")
    add_v4l_node(root, "v4l-subdev7", "ov8858 2-0010", "ov8858")
    add_v4l_node(root, "v4l-subdev8", "dw9714 2-000c", "dw9714")


def single_uvc(root):
    add_v4l_node(root, "video0", "HD Webcam: HD Webcam", "uvcvideo", bus="usb")
    add_v4l_node(root, "video1", "HD Webcam: HD Webcam", "uvcvideo", bus="usb")
