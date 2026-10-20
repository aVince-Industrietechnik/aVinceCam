"""Build fake sysfs trees for tests (no real hardware involved).

The layout follows real sysfs: device directories below devices/, driver and
subsystem symlinks into bus/, class/video4linux and bus/media/devices as
symlinks to directories inside the owning device.
"""

import os


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")


def _link(target, name):
    os.makedirs(os.path.dirname(name), exist_ok=True)
    os.symlink(target, name)


def add_device(root, relpath, driver=None, bus=None, attrs=None):
    """Create devices/<relpath> with optional driver/subsystem links."""
    path = os.path.join(root, "devices", relpath)
    os.makedirs(path, exist_ok=True)
    if bus:
        bus_dir = os.path.join(root, "bus", bus)
        os.makedirs(os.path.join(bus_dir, "devices"), exist_ok=True)
        _link(bus_dir, os.path.join(path, "subsystem"))
        _link(path, os.path.join(bus_dir, "devices", os.path.basename(path)))
        if driver:
            drv = os.path.join(bus_dir, "drivers", driver)
            os.makedirs(drv, exist_ok=True)
            _link(drv, os.path.join(path, "driver"))
    for key, value in (attrs or {}).items():
        _write(os.path.join(path, key), value)
    return path


def add_v4l(root, device_path, node, name, index=None):
    node_dir = os.path.join(device_path, "video4linux", node)
    _write(os.path.join(node_dir, "name"), name)
    if index is not None:
        _write(os.path.join(node_dir, "index"), str(index))
    _link(device_path, os.path.join(node_dir, "device"))
    _link(node_dir, os.path.join(root, "class", "video4linux", node))


def add_media(root, device_path, media, model):
    media_dir = os.path.join(device_path, media)
    _write(os.path.join(media_dir, "model"), model)
    _link(media_dir, os.path.join(root, "bus", "media", "devices", media))


def add_location(device_path, panel):
    for key, value in (("panel", panel), ("dock", "no"), ("lid", "no"),
                       ("horizontal_position", "right"),
                       ("vertical_position", "center")):
        _write(os.path.join(device_path, "physical_location", key), value)


def add_acpi_path(device_path, path):
    _write(os.path.join(device_path, "firmware_node", "path"), path)


def add_ipa_tuning(ipa_root, pipeline, *names):
    for name in names:
        _write(os.path.join(ipa_root, pipeline, name), "# tuning")


def make_dev(sysfs_root, dev_root):
    """A plain file for every node of the fake sysfs, as a stand-in for /dev."""
    from avincecam import sysfs  # the core module, importable in tests
    os.makedirs(os.path.join(dev_root, "dev"), exist_ok=True)
    for entry in sysfs.read_nodes(sysfs_root) + sysfs.read_media(sysfs_root):
        path = os.path.join(dev_root, entry["dev"].lstrip("/"))
        if not os.path.exists(path):
            open(path, "w").close()


# ---------------------------------------------------------------- scenarios

def empty(root):
    os.makedirs(os.path.join(root, "class", "video4linux"), exist_ok=True)


def latitude_5285(root):
    """Intel IPU3 2-in-1: OV5670 front, OV8858 back with DW9714 lens motor."""
    imgu = add_device(root, "pci0000:00/0000:00:05.0", "ipu3-imgu", "pci")
    cio2 = add_device(root, "pci0000:00/0000:00:14.3", "ipu3-cio2", "pci")
    add_media(root, imgu, "media0", "ipu3-imgu")
    add_media(root, cio2, "media1", "Intel IPU3 CIO2")
    for i, kind in enumerate(("input", "parameters", "output", "viewfinder", "3a stat")):
        add_v4l(root, imgu, f"video{i}", f"ipu3-imgu 0 {kind}", 0)
    for i in range(4):
        add_v4l(root, cio2, f"video{10 + i}", f"ipu3-cio2 {i}", 0)
        add_v4l(root, cio2, f"v4l-subdev{2 + i}", f"ipu3-csi2 {i}")
    add_v4l(root, imgu, "v4l-subdev0", "ipu3-imgu 0")

    front = add_device(root, "pci0000:00/INT3446:00/i2c-3/i2c-INT3479:00",
                       "ov5670", "i2c", {"name": "INT3479:00"})
    add_location(front, "front")
    add_acpi_path(front, r"\_SB_.PCI0.LNK0")
    add_v4l(root, front, "v4l-subdev6", "ov5670 3-0036")

    back = add_device(root, "pci0000:00/0000:00:15.2/i2c-2/i2c-INT3477:00",
                      "ov8858", "i2c", {"name": "INT3477:00"})
    add_location(back, "back")
    add_acpi_path(back, r"\_SB_.PCI0.LNK1")
    add_v4l(root, back, "v4l-subdev7", "ov8858 2-0010")

    vcm = add_device(root, "pci0000:00/0000:00:15.2/i2c-2/i2c-INT3477:00-VCM",
                     "dw9714", "i2c", {"name": "INT3477:00-VCM"})
    _link(back, os.path.join(vcm, "supplier:i2c:i2c-INT3477:00"))
    add_v4l(root, vcm, "v4l-subdev8", "dw9714 2-000c")


def usb_webcam(root, port="1-5", removable="removable",
               product="HD Webcam", first_video=0, media="media2"):
    usb = add_device(root, f"pci0000:00/0000:00:14.0/usb1/{port}", None, "usb",
                     {"idVendor": "046d", "idProduct": "0825",
                      "manufacturer": "Example", "product": product,
                      "removable": removable})
    iface = add_device(root, f"pci0000:00/0000:00:14.0/usb1/{port}/{port}:1.0",
                       "uvcvideo", "usb")
    add_v4l(root, iface, f"video{first_video}", f"{product}: {product}", 0)
    add_v4l(root, iface, f"video{first_video + 1}", f"{product}: {product}", 1)
    add_media(root, iface, media, product)
    return usb


def single_uvc(root):
    usb_webcam(root)


def ipu3_plus_uvc(root):
    latitude_5285(root)
    usb_webcam(root, first_video=20, media="media2")


def add_v4l_node(root, node, name, driver, bus="i2c"):
    """Stand-alone node with its own device (for safety rule tests)."""
    dev = add_device(root, f"misc/{node}-dev", driver, bus)
    add_v4l(root, dev, node, name)
